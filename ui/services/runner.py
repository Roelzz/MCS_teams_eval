"""Run a channel-parity eval from the UI via inspect_ai.eval_async.

The judge model is selected through the JUDGE_MODEL env var (read by the scorers),
so we set it before launching and also pass it as the eval's task model.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Awaitable, Callable

from inspect_ai import eval_async

import channel_eval

_REPO_ROOT = Path(__file__).resolve().parents[2]

# In-flight eval subprocesses keyed by agent name, so a whole multi-agent run can be
# cancelled from the UI (one process per agent — see launch_agents).
_ACTIVE_PROCS: dict[str, asyncio.subprocess.Process] = {}

# Locations of the .eval logs produced by the most recent multi-agent run, in agent
# (selection) order. The Results page reads these when redirected with ?new=multi.
LAST_RUN_LOGS: list[str] = []

_RUN_OK_RE = re.compile(r"^RUN_OK\s+(.+)$")

# Provider env vars Inspect needs per judge-model prefix (openai/… , azureai/…).
_PROVIDER_KEYS: dict[str, list[str]] = {
    "openai": ["OPENAI_API_KEY"],
    "azureai": ["AZUREAI_OPENAI_API_KEY", "AZUREAI_OPENAI_BASE_URL"],
}
# All judge-provider keys synced from .env into the process before a run.
_JUDGE_ENV_KEYS = (
    "OPENAI_API_KEY",
    "AZUREAI_OPENAI_API_KEY",
    "AZUREAI_OPENAI_BASE_URL",
    "AZUREAI_OPENAI_API_VERSION",
)


def _provider(judge_model: str) -> str:
    model = (judge_model or "").strip()
    return model.split("/", 1)[0] if "/" in model else ""


def missing_judge_key(judge_model: str) -> list[str]:
    """Env keys the judge model's provider needs that are still unset in .env (UI guard)."""
    from ui.services import config_io

    needed = _PROVIDER_KEYS.get(_provider(judge_model), [])
    env = config_io.effective_env()
    return [k for k in needed if not env.get(k, "").strip()]


def _sync_judge_env() -> None:
    """Apply non-empty judge keys from .env to os.environ so Inspect (which reads the
    process env) sees keys saved via the Settings page or edited by hand after startup."""
    from ui.services import config_io

    env = config_io.effective_env()
    for key in _JUDGE_ENV_KEYS:
        value = env.get(key, "").strip()
        if value:
            os.environ[key] = value


async def run_eval(
    agent: str,
    testset_name: str,
    quality_grading: bool,
    judge_model: str,
    log_dir: str = "logs",
    use_judge: bool = True,
):
    """Launch one parity eval. Returns the produced EvalLog (or None).

    When ``use_judge`` is False the run is fully deterministic: the semantic layer and quality
    grading (the only two LLM uses) are disabled, the match policy is forced to ``normalized``,
    and Inspect runs against ``mockllm/model`` so no OpenAI/Foundry key is required.
    """
    _sync_judge_env()

    if not use_judge:
        os.environ["COMPARE_SEMANTIC"] = "false"
        os.environ["MATCH_POLICY"] = "normalized"
        model = "mockllm/model"
        return _first(
            await eval_async(
                channel_eval.channel_parity(
                    agent=agent,
                    testset=f"testsets/{testset_name}.json",
                    quality_grading=False,
                ),
                model=model,
                log_dir=log_dir,
                # One sample at a time: Teams is a single shared chat — parallel samples flood it
                # and their replies get mis-attributed. See plan / TEAMS_TURN_WAIT_S.
                max_samples=1,
            )
        )

    if judge_model:
        os.environ["JUDGE_MODEL"] = judge_model

    task = channel_eval.channel_parity(
        agent=agent,
        testset=f"testsets/{testset_name}.json",
        quality_grading=quality_grading,
    )
    logs = await eval_async(
        task,
        model=judge_model or os.getenv("JUDGE_MODEL"),
        log_dir=log_dir,
        # One sample at a time — see the no-judge branch above (shared Teams chat).
        max_samples=1,
    )
    return _first(logs)


def _first(logs):
    return logs[0] if logs else None


async def launch_eval(
    agent: str,
    testset_name: str,
    quality_grading: bool,
    judge_model: str,
    log_dir: str = "logs",
    use_judge: bool = True,
    teams_wait: str = "",
) -> asyncio.subprocess.Process:
    """Spawn the eval as its own process (eval_cli.py) and return it.

    Inspect's eval owns the process/event loop; running it inside Reflex's granian worker loop
    wedges at the first sample. A dedicated subprocess mirrors the working CLI path. stderr is
    merged into stdout so the caller can stream loguru progress lines and detect completion via
    the child's exit code. The child is killable (Cancel) — unlike an in-loop asyncio task.

    With ``use_judge`` False the child runs deterministically (no LLM, no provider key needed).
    """
    _sync_judge_env()
    env = dict(os.environ)
    env["INSPECT_DISPLAY"] = "none"  # no TTY in the worker; keep Inspect's live UI off
    if teams_wait.strip():
        env["TEAMS_TURN_WAIT_S"] = teams_wait.strip()  # per-run override (not written to .env)
    for key in _JUDGE_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            env[key] = value

    cmd = [
        sys.executable,
        "-u",
        "eval_cli.py",
        "--agent",
        agent,
        "--testset",
        testset_name,
        "--judge",
        judge_model or "",
        "--log-dir",
        log_dir,
        "--llm-judge" if use_judge else "--no-llm-judge",
        "--quality" if (quality_grading and use_judge) else "--no-quality",
    ]
    return await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(_REPO_ROOT),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )


# ── multi-agent orchestration ─────────────────────────────────────────────────

LineCb = Callable[[str, str], Awaitable[None]]
DoneCb = Callable[[str, dict], Awaitable[None]]


def kill_all_runs() -> None:
    """Kill every in-flight eval subprocess (Cancel from the UI). Safe to call when idle."""
    for proc in list(_ACTIVE_PROCS.values()):
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
    _ACTIVE_PROCS.clear()


async def _stream_one(
    agent: str,
    testset_name: str,
    quality_grading: bool,
    judge_model: str,
    use_judge: bool,
    teams_wait: str,
    log_dir: str,
    sem: asyncio.Semaphore,
    on_line: LineCb,
    on_done: DoneCb,
) -> dict:
    """Run one agent's eval subprocess, stream its lines, and report a result dict.

    Result: {agent, ok, rc, location, error}. ``location`` is the produced .eval log path
    (parsed from the child's ``RUN_OK`` line); empty when the run failed before writing one.
    """
    async with sem:
        await on_done(agent, {"agent": agent, "status": "running"})
        proc = await launch_eval(
            agent,
            testset_name,
            quality_grading,
            judge_model,
            log_dir=log_dir,
            use_judge=use_judge,
            teams_wait=teams_wait,
        )
        _ACTIVE_PROCS[agent] = proc
        location = ""
        try:
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").rstrip()
                if not line:
                    continue
                m = _RUN_OK_RE.match(line)
                if m:
                    location = m.group(1).strip()
                await on_line(agent, line)
            rc = await proc.wait()
        finally:
            _ACTIVE_PROCS.pop(agent, None)

    ok = rc == 0
    if ok:
        error = ""
    elif rc is not None and rc < 0:
        error = "cancelled"  # SIGKILL → negative rc
    else:
        error = "failed"
    result = {"agent": agent, "ok": ok, "rc": rc, "location": location, "error": error}
    await on_done(agent, {**result, "status": "done" if ok else (error or "failed")})
    return result


async def launch_agents(
    agents: list[str],
    testset_name: str,
    quality_grading: bool,
    judge_model: str,
    use_judge: bool = True,
    teams_wait: str = "",
    log_dir: str = "logs",
    max_parallel: int = 3,
    on_line: LineCb | None = None,
    on_done: DoneCb | None = None,
) -> list[dict]:
    """Run the testset against several agents concurrently (one subprocess per agent).

    Each agent keeps the proven single-agent path (eval_cli.py, sequential samples) — only the
    agents themselves run in parallel, which is safe because every agent has its own Teams chat.
    ``max_parallel`` caps how many run at once. ``on_line(agent, line)`` streams merged stdout;
    ``on_done(agent, info)`` reports status transitions ({status: running|done|failed|cancelled}).
    Returns one result dict per agent and records produced logs in ``LAST_RUN_LOGS``.
    """

    async def _noop_line(agent: str, line: str) -> None:  # pragma: no cover - default
        return None

    async def _noop_done(agent: str, info: dict) -> None:  # pragma: no cover - default
        return None

    line_cb = on_line or _noop_line
    done_cb = on_done or _noop_done
    sem = asyncio.Semaphore(max(1, max_parallel))

    tasks = [
        asyncio.create_task(
            _stream_one(
                agent,
                testset_name,
                quality_grading,
                judge_model,
                use_judge,
                teams_wait,
                log_dir,
                sem,
                line_cb,
                done_cb,
            )
        )
        for agent in agents
    ]
    gathered = await asyncio.gather(*tasks, return_exceptions=True)

    results: list[dict] = []
    for agent, res in zip(agents, gathered):
        if isinstance(res, dict):
            results.append(res)
        elif isinstance(res, asyncio.CancelledError):
            results.append(
                {"agent": agent, "ok": False, "rc": None, "location": "", "error": "cancelled"}
            )
        else:
            results.append(
                {
                    "agent": agent,
                    "ok": False,
                    "rc": None,
                    "location": "",
                    "error": f"{type(res).__name__}: {res}",
                }
            )

    global LAST_RUN_LOGS
    LAST_RUN_LOGS = [r["location"] for r in results if r.get("location")]
    return results
