"""Inspect AI task: Copilot Studio channel parity (Teams vs Direct).

Run:
    uv run inspect eval channel_eval.py --model openai/gpt-4o \\
        -T agent=demo-agent -T testset=testsets/smoke.json

The `--model` is used as the **judge** for semantic comparison and quality grading. Set
JUDGE_MODEL in .env to use a different grader than the task model. The solver drives the Teams and
Direct channels itself — it does not call the task model for generation.
"""

import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import ModelOutput
from inspect_ai.scorer import (
    CORRECT,
    INCORRECT,
    NOANSWER,
    SampleScore,
    Score,
    Target,
    accuracy,
    metric,
    scorer,
    stderr,
)
from inspect_ai.solver import Generate, TaskState, solver
from loguru import logger

from compare import (
    ChannelResponse,
    aggregate,
    compare_normalized,
    compare_structured,
    get_judge_model,
    grade_quality,
    semantic_equivalent,
)
from direct_client import DirectClient
from log_setup import configure_logging
from teams_client import TeamsClient

load_dotenv()
configure_logging()

AGENTS_FILE = os.getenv("AGENTS_FILE", "agents.json")


# ── Config helpers ───────────────────────────────────────────────────────────


def _bool_env(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def load_agents() -> dict:
    path = Path(AGENTS_FILE)
    if not path.exists():
        raise FileNotFoundError(
            f"{AGENTS_FILE} not found. Copy agents.example.json to {AGENTS_FILE} and add your "
            "agent's Direct + Teams coordinates (or add one via the web app's Agents page)."
        )
    return json.loads(path.read_text())


def get_agent(name: str) -> dict:
    agents = load_agents()
    if name not in agents:
        raise ValueError(f"Agent {name!r} not in {AGENTS_FILE}. Available: {list(agents)}")
    return agents[name]


def _comparator_config() -> dict:
    return {
        "semantic": _bool_env("COMPARE_SEMANTIC", True),
        "structured": _bool_env("COMPARE_STRUCTURED", True),
        "citations": _bool_env("COMPARE_CITATIONS", True),
        "cards": _bool_env("COMPARE_CARDS", True),
        "suggested": _bool_env("COMPARE_SUGGESTED", True),
        "policy": [
            p.strip()
            for p in os.getenv("MATCH_POLICY", "normalized,semantic").split(",")
            if p.strip()
        ],
    }


# ── Dataset ──────────────────────────────────────────────────────────────────


def load_testset(path: str) -> MemoryDataset:
    data = json.loads(Path(path).read_text())
    samples: list[Sample] = []
    for case in data["cases"]:
        inp = case["input"]
        target = case.get("target", "")
        if isinstance(inp, str):
            turns = [{"query": inp, "expected": target}]
        else:
            turns = [
                {"query": t["content"], "expected": t.get("expected", "")}
                for t in inp
                if t.get("role", "user") == "user"
            ]
            # Back-compat: a legacy multi-turn case with only a case-level target graded the
            # final reply — carry that target onto the last turn when no per-turn expected exists.
            if target and turns and not any(t["expected"] for t in turns):
                turns[-1]["expected"] = target
        display = turns[-1]["query"] if turns else ""
        samples.append(
            Sample(
                input=display,
                target=target,
                id=str(case.get("id", display[:40])),
                metadata={
                    "turns": turns,
                    "tags": case.get("tags", []),
                    "repeats": case.get("repeats"),
                    "agreement_threshold": case.get("agreement_threshold"),
                },
            )
        )
    return MemoryDataset(samples=samples, name=data.get("name", Path(path).stem))


# ── Solver: drive both channels ──────────────────────────────────────────────


async def guarded_channel(coro, channel: str, timeout_s: float) -> ChannelResponse:
    """Await a channel coroutine with a hard timeout, converting any hang or failure into
    an error ``ChannelResponse``. Keeps a stuck Direct/Teams call from wedging the whole run —
    the failure surfaces as a channel difference in the results instead."""
    try:
        return await asyncio.wait_for(coro, timeout_s)
    except TimeoutError:
        logger.error("[{}] channel timed out after {:.0f}s", channel, timeout_s)
        return ChannelResponse(channel=channel, error=f"timed out after {timeout_s:.0f}s")
    except Exception as e:  # noqa: BLE001
        logger.error("[{}] channel failed: {}", channel, e)
        return ChannelResponse(channel=channel, error=f"{type(e).__name__}: {e}")


@solver
def dual_channel(agent: str):
    cfg = get_agent(agent)
    direct_cfg = cfg["direct"]
    teams_cfg = cfg["teams"]
    reset_commands = [
        c.strip() for c in os.getenv("TEAMS_RESET_COMMANDS", "").split(",") if c.strip()
    ]
    default_repeats = int(os.getenv("DEFAULT_REPEATS", "1"))
    timeout_s = float(os.getenv("CHANNEL_TIMEOUT_S", "90"))

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        turn_specs: list[dict] = state.metadata.get("turns") or [
            {"query": state.input_text, "expected": ""}
        ]
        repeats: int = state.metadata.get("repeats") or default_repeats

        runs: list[list[dict]] = []
        for i in range(repeats):
            logger.info("[{}] sample={} repeat={}/{}", agent, state.sample_id, i + 1, repeats)
            direct = DirectClient(direct_cfg["environment_id"], direct_cfg["agent_identifier"])
            teams = TeamsClient.from_config(teams_cfg)

            direct_err: str | None = None
            try:
                await asyncio.wait_for(direct.start(), timeout_s)
            except TimeoutError:
                direct_err = f"start timed out after {timeout_s:.0f}s"
                logger.error("[{}] Direct start timed out after {:.0f}s", agent, timeout_s)
            except Exception as e:  # noqa: BLE001
                direct_err = f"{type(e).__name__}: {e}"
                logger.error("[{}] Direct start failed: {}", agent, e)

            if reset_commands and not direct_err:
                try:
                    await asyncio.wait_for(teams.reset(reset_commands), timeout_s)
                except Exception as e:  # noqa: BLE001
                    logger.warning("[{}] Teams reset failed: {}", agent, e)

            per_turn: list[dict] = []
            for ti, spec in enumerate(turn_specs):
                turn = spec["query"]
                if direct_err:
                    d_resp = ChannelResponse(channel="direct", error=direct_err)
                    t_resp = await guarded_channel(teams.ask(turn), "teams", timeout_s)
                else:
                    d_resp, t_resp = await asyncio.gather(
                        guarded_channel(direct.ask(turn), "direct", timeout_s),
                        guarded_channel(teams.ask(turn), "teams", timeout_s),
                    )
                per_turn.append(
                    {
                        "turn": ti + 1,
                        "query": turn,
                        "expected": spec.get("expected", ""),
                        "direct": d_resp.model_dump(),
                        "teams": t_resp.model_dump(),
                    }
                )
            runs.append(per_turn)

        last_query = turn_specs[-1]["query"] if turn_specs else state.input_text
        state.metadata["channels"] = {"agent": agent, "prompt": last_query, "runs": runs}
        last = runs[0][-1] if runs and runs[0] else {}
        summary = (
            (last.get("teams") or {}).get("text")
            or (last.get("direct") or {}).get("text")
            or "[no output]"
        )
        state.output = ModelOutput.from_content("dual_channel", summary)
        return state

    return solve


# ── Metrics ──────────────────────────────────────────────────────────────────


@metric
def match_rate():
    """Fraction of CORRECT among scored samples, excluding NOANSWER."""

    def compute(scores: list[SampleScore]) -> float:
        vals = [
            1.0 if s.score.value == CORRECT else 0.0 for s in scores if s.score.value != NOANSWER
        ]
        return sum(vals) / len(vals) if vals else 0.0

    return compute


# ── Scorer: channel difference ───────────────────────────────────────────────


def _format_explanation(prompt: str, details: list[dict], agg: dict) -> str:
    lines = [f"PROMPT: {prompt}", ""]
    for d in details:
        head = f"— turn {d.get('turn', '?')}"
        if d.get("query"):
            head += f": {d['query'][:120]}"
        lines.append(f"{head} — layers: {d['layers']}")
        if d.get("direct_error") or d.get("teams_error"):
            lines.append(f"   errors: direct={d.get('direct_error')} teams={d.get('teams_error')}")
        lines.append(f"   DIRECT: {d['direct_text'][:300]}")
        lines.append(f"   TEAMS : {d['teams_text'][:300]}")
    lines.append("")
    lines.append(
        f"agreement={agg['agreement']:.2f} threshold={agg['threshold']:.2f} "
        f"=> {'MATCH' if agg['passed'] else 'DIFFER'} | per-layer={agg['per_layer_agreement']}"
    )
    return "\n".join(lines)


@scorer(metrics=[accuracy(), match_rate(), stderr()])
def channel_diff():
    cfg = _comparator_config()

    async def score(state: TaskState, target: Target) -> Score:
        ch = state.metadata.get("channels")
        if not ch or not ch.get("runs"):
            return Score(value=NOANSWER, explanation="no channel data captured")

        judge = get_judge_model() if cfg["semantic"] else None
        threshold = state.metadata.get("agreement_threshold")
        if threshold is None:
            threshold = float(os.getenv("DEFAULT_AGREEMENT_THRESHOLD", "0.8"))

        async def compute_layers(d: ChannelResponse, t: ChannelResponse) -> dict:
            if d.error or t.error:
                return {
                    "normalized": False,
                    "semantic": False if cfg["semantic"] else None,
                    "structured": False if cfg["structured"] else None,
                }
            layers: dict[str, bool | None] = {"normalized": compare_normalized(d, t)}
            if cfg["semantic"]:
                eq, _ = await semantic_equivalent(d, t, judge)
                layers["semantic"] = eq
            else:
                layers["semantic"] = None
            if cfg["structured"]:
                st = compare_structured(
                    d, t, citations=cfg["citations"], cards=cfg["cards"], suggested=cfg["suggested"]
                )
                layers["structured"] = bool(st["match"])
            else:
                layers["structured"] = None
            return layers

        flat_layers: list[dict] = []  # every (repeat, turn) — drives the case-level score
        per_turn_layers: dict[int, list[dict]] = {}  # turn -> layers across repeats
        details: list[dict] = []  # one entry per turn (first repeat), for the report
        for r_idx, run in enumerate(ch["runs"]):
            for turn in run:
                d = ChannelResponse.model_validate(turn["direct"])
                t = ChannelResponse.model_validate(turn["teams"])
                layers = await compute_layers(d, t)
                flat_layers.append(layers)
                ti = turn.get("turn", 1)
                per_turn_layers.setdefault(ti, []).append(layers)
                if r_idx == 0:
                    details.append(
                        {
                            "turn": ti,
                            "query": turn.get("query", ""),
                            "expected": turn.get("expected", ""),
                            "direct_text": d.text,
                            "teams_text": t.text,
                            "direct": turn["direct"],
                            "teams": turn["teams"],
                            "layers": layers,
                            "direct_error": d.error,
                            "teams_error": t.error,
                        }
                    )

        for det in details:
            det["agg"] = aggregate(per_turn_layers.get(det["turn"], []), threshold, cfg["policy"])

        agg = aggregate(flat_layers, threshold, cfg["policy"])
        value = CORRECT if agg["passed"] else INCORRECT
        return Score(
            value=value,
            answer=(details[-1]["teams_text"][:200] if details else ""),
            explanation=_format_explanation(ch["prompt"], details, agg),
            metadata={"agreement": agg, "details": details, "prompt": ch["prompt"]},
        )

    return score


# ── Scorer: quality vs target (optional, only for targeted samples) ──────────


@scorer(metrics=[match_rate(), accuracy()])
def quality():
    async def score(state: TaskState, target: Target) -> Score:
        ch = state.metadata.get("channels")
        if not ch or not ch.get("runs"):
            return Score(value=NOANSWER, explanation="no channel data")

        run0 = ch["runs"][0] if ch["runs"] else []
        judge = get_judge_model()
        turn_results: list[dict] = []
        all_ok = True
        for turn in run0:
            expected = (turn.get("expected") or "").strip()
            if not expected:
                continue  # only grade turns that carry an expected response
            d = ChannelResponse.model_validate(turn["direct"])
            t = ChannelResponse.model_validate(turn["teams"])
            question = turn.get("query") or ch["prompt"]
            d_ok, d_expl = await grade_quality(question, d.text, expected, judge)
            t_ok, t_expl = await grade_quality(question, t.text, expected, judge)
            turn_results.append(
                {
                    "turn": turn.get("turn"),
                    "expected": expected,
                    "direct_ok": d_ok,
                    "teams_ok": t_ok,
                    "direct_explanation": d_expl,
                    "teams_explanation": t_expl,
                }
            )
            all_ok = all_ok and d_ok and t_ok

        if not turn_results:
            return Score(value=NOANSWER, explanation="no expected responses to grade")

        value = CORRECT if all_ok else INCORRECT
        explanation = "; ".join(
            f"t{r['turn']}: direct={'C' if r['direct_ok'] else 'I'} "
            f"teams={'C' if r['teams_ok'] else 'I'}"
            for r in turn_results
        )
        return Score(
            value=value,
            explanation=explanation,
            metadata={
                "turns": turn_results,
                # flat fields (first graded turn) kept for backward-compatible readers
                "direct_ok": turn_results[0]["direct_ok"],
                "teams_ok": turn_results[0]["teams_ok"],
            },
        )

    return score


# ── Task ─────────────────────────────────────────────────────────────────────


@task
def channel_parity(
    agent: str = "demo-agent",
    testset: str = "testsets/smoke.json",
    quality_grading: bool = True,
) -> Task:
    scorers = [channel_diff()]
    if quality_grading:
        scorers.append(quality())
    return Task(
        dataset=load_testset(testset),
        solver=dual_channel(agent),
        scorer=scorers,
        name=f"channel_parity-{agent}",
    )
