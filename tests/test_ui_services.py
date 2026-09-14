"""Unit tests for ui.services.runner and ui.services.logstream.

These lock the Run-page plumbing (judge-model precedence, testset path, env wiring)
and the loguru→queue streaming helper without needing a browser or real credentials.
"""

from __future__ import annotations

import asyncio

import pytest
from loguru import logger

import channel_eval
from ui.services import logstream, runner


class _FakeLog:
    status = "success"


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Stub channel_parity + eval_async, recording the args runner passes them."""
    rec: dict = {}

    def fake_parity(*, agent: str, testset: str, quality_grading: bool):
        rec["parity"] = {
            "agent": agent,
            "testset": testset,
            "quality_grading": quality_grading,
        }
        return "TASK"

    async def fake_eval_async(task, *, model, log_dir, **kw):
        rec["eval"] = {"task": task, "model": model, "log_dir": log_dir, "kwargs": kw}
        return [_FakeLog()]

    monkeypatch.setattr(channel_eval, "channel_parity", fake_parity)
    monkeypatch.setattr(runner, "eval_async", fake_eval_async)
    return rec


def test_run_eval_wires_args_and_judge(captured: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JUDGE_MODEL", raising=False)
    log = asyncio.run(runner.run_eval("demo-agent", "smoke", False, "openai/gpt-4o"))

    assert isinstance(log, _FakeLog)
    assert captured["parity"] == {
        "agent": "demo-agent",
        "testset": "testsets/smoke.json",
        "quality_grading": False,
    }
    assert captured["eval"]["model"] == "openai/gpt-4o"
    assert captured["eval"]["log_dir"] == "logs"
    # explicit judge is exported for the scorers to read
    import os

    assert os.environ["JUDGE_MODEL"] == "openai/gpt-4o"


def test_run_eval_falls_back_to_env_judge(captured: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUDGE_MODEL", "preset/model")
    asyncio.run(runner.run_eval("a", "t", True, ""))
    # empty judge arg → keep the env value, do not overwrite it
    assert captured["eval"]["model"] == "preset/model"
    import os

    assert os.environ["JUDGE_MODEL"] == "preset/model"
    assert captured["parity"]["quality_grading"] is True


def test_run_eval_returns_none_when_no_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(channel_eval, "channel_parity", lambda **k: "TASK")

    async def empty(*a, **k):
        return []

    monkeypatch.setattr(runner, "eval_async", empty)
    assert asyncio.run(runner.run_eval("a", "t", False, "m")) is None


def test_logstream_capture_drain_and_cleanup() -> None:
    with logstream.capture() as q:
        logger.info("hello-stream-42")
        lines = logstream.drain(q)
    assert any("hello-stream-42" in ln for ln in lines)

    # sink removed on exit: further logs must not land in the same queue
    logger.info("after-close")
    assert logstream.drain(q) == []


# ── integration: real eval_async via runner (would catch unsupported-kwarg bugs) ──
class _FakeDirect:
    def __init__(self, *a, **k):
        pass

    async def start(self):
        pass

    async def ask(self, text):
        from compare import ChannelResponse

        return ChannelResponse(channel="direct", text="ok", raw="ok")


class _FakeTeams:
    def __init__(self, *a, **k):
        pass

    @classmethod
    def from_config(cls, cfg):
        return cls()

    async def reset(self, commands):
        pass

    async def ask(self, text):
        from compare import ChannelResponse

        return ChannelResponse(channel="teams", text="ok", raw="<p>ok</p>", is_html=True)


def test_run_eval_drives_real_eval_async(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise runner against the *real* inspect_ai.eval_async (no stub).

    Guards against passing kwargs the async API rejects (e.g. ``display``).
    """
    monkeypatch.setenv("COMPARE_SEMANTIC", "false")
    monkeypatch.delenv("JUDGE_MODEL", raising=False)  # runner sets it; ensure teardown clears it
    monkeypatch.setattr(channel_eval, "DirectClient", _FakeDirect)
    monkeypatch.setattr(channel_eval, "TeamsClient", _FakeTeams)
    monkeypatch.setattr(
        channel_eval,
        "get_agent",
        lambda name: {
            "direct": {"environment_id": "e", "agent_identifier": "cr_x"},
            "teams": {"chat_id": "19:abc@unq.gbl.spaces"},
        },
    )

    log = asyncio.run(
        runner.run_eval("demo", "smoke", False, "mockllm/model", log_dir=str(tmp_path))
    )
    assert log is not None
    assert log.status == "success"
    assert log.samples


# ── judge-key pre-flight guard + env sync ─────────────────────────────────────
def test_missing_judge_key_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    from ui.services import config_io

    monkeypatch.setattr(config_io, "effective_env", lambda: {"OPENAI_API_KEY": ""})
    assert runner.missing_judge_key("openai/gpt-4o") == ["OPENAI_API_KEY"]

    monkeypatch.setattr(config_io, "effective_env", lambda: {"OPENAI_API_KEY": "sk-x"})
    assert runner.missing_judge_key("openai/gpt-4o") == []


def test_missing_judge_key_azureai_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    from ui.services import config_io

    monkeypatch.setattr(
        config_io,
        "effective_env",
        lambda: {"AZUREAI_OPENAI_API_KEY": "k", "AZUREAI_OPENAI_BASE_URL": ""},
    )
    assert runner.missing_judge_key("azureai/gpt-4o") == ["AZUREAI_OPENAI_BASE_URL"]


def test_missing_judge_key_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from ui.services import config_io

    monkeypatch.setattr(config_io, "effective_env", lambda: {})
    # mockllm / unrecognised providers have no key requirement we enforce
    assert runner.missing_judge_key("mockllm/model") == []


def test_sync_judge_env_applies_nonempty(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from ui.services import config_io

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(config_io, "effective_env", lambda: {"OPENAI_API_KEY": "sk-live"})
    runner._sync_judge_env()
    assert os.environ["OPENAI_API_KEY"] == "sk-live"


def test_sync_judge_env_skips_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from ui.services import config_io

    monkeypatch.setenv("OPENAI_API_KEY", "preexisting")
    monkeypatch.setattr(config_io, "effective_env", lambda: {"OPENAI_API_KEY": ""})
    runner._sync_judge_env()
    # an empty .env placeholder must not clobber a real key already in the process
    assert os.environ["OPENAI_API_KEY"] == "preexisting"


async def _capture_launch(monkeypatch, **kwargs) -> dict:
    """Run launch_eval with create_subprocess_exec stubbed; return the captured call."""
    captured: dict = {}

    async def fake_exec(*cmd, **kw):
        captured["cmd"] = list(cmd)
        captured["kwargs"] = kw
        return object()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(runner, "_sync_judge_env", lambda: None)
    await runner.launch_eval(**kwargs)
    return captured


async def test_launch_eval_builds_command(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = await _capture_launch(
        monkeypatch,
        agent="Demo ING",
        testset_name="smoke",
        quality_grading=True,
        judge_model="openai/gpt-4o",
    )
    cmd = cap["cmd"]
    assert "eval_cli.py" in cmd
    assert cmd[cmd.index("--agent") + 1] == "Demo ING"
    assert cmd[cmd.index("--testset") + 1] == "smoke"
    assert cmd[cmd.index("--judge") + 1] == "openai/gpt-4o"
    assert "--quality" in cmd and "--no-quality" not in cmd
    # No TTY in the Reflex worker — Inspect's live display must be off in the child.
    assert cap["kwargs"]["env"]["INSPECT_DISPLAY"] == "none"
    assert cap["kwargs"]["stdout"] is asyncio.subprocess.PIPE
    assert cap["kwargs"]["stderr"] is asyncio.subprocess.STDOUT


async def test_launch_eval_no_quality_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = await _capture_launch(
        monkeypatch,
        agent="Demo ING",
        testset_name="smoke",
        quality_grading=False,
        judge_model="openai/gpt-4o",
    )
    assert "--no-quality" in cap["cmd"]
    assert "--quality" not in cap["cmd"]


# ── no-LLM-judge (deterministic) mode ─────────────────────────────────────────
def test_run_eval_no_judge_is_deterministic(
    captured: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    # baselines so monkeypatch restores them after the test
    monkeypatch.setenv("COMPARE_SEMANTIC", "true")
    monkeypatch.setenv("MATCH_POLICY", "normalized,semantic")
    monkeypatch.delenv("JUDGE_MODEL", raising=False)

    log = asyncio.run(
        runner.run_eval("demo", "smoke", quality_grading=True, judge_model="", use_judge=False)
    )

    assert isinstance(log, _FakeLog)
    # judge fully disabled: quality forced off, semantic off, policy normalized-only, mock model
    assert captured["parity"]["quality_grading"] is False
    assert captured["eval"]["model"] == "mockllm/model"
    assert os.environ["COMPARE_SEMANTIC"] == "false"
    assert os.environ["MATCH_POLICY"] == "normalized"


async def test_launch_eval_no_llm_judge_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = await _capture_launch(
        monkeypatch,
        agent="Demo ING",
        testset_name="smoke",
        quality_grading=True,  # ignored when judge is off
        judge_model="",
        use_judge=False,
    )
    cmd = cap["cmd"]
    assert "--no-llm-judge" in cmd and "--llm-judge" not in cmd
    # quality is meaningless without a judge → forced off in the child command
    assert "--no-quality" in cmd and "--quality" not in cmd


async def test_launch_eval_llm_judge_default(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = await _capture_launch(
        monkeypatch,
        agent="Demo ING",
        testset_name="smoke",
        quality_grading=True,
        judge_model="openai/gpt-4o",
    )
    assert "--llm-judge" in cap["cmd"] and "--no-llm-judge" not in cap["cmd"]


# ── Teams pacing: sequential eval + per-run wait override ──────────────────────
def test_run_eval_is_sequential(captured: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JUDGE_MODEL", raising=False)
    asyncio.run(runner.run_eval("demo", "smoke", False, "openai/gpt-4o"))
    # Teams is one shared chat → samples must run one at a time, never in parallel.
    assert captured["eval"]["kwargs"]["max_samples"] == 1


def test_run_eval_no_judge_is_sequential(captured: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JUDGE_MODEL", raising=False)
    asyncio.run(
        runner.run_eval("demo", "smoke", quality_grading=True, judge_model="", use_judge=False)
    )
    assert captured["eval"]["kwargs"]["max_samples"] == 1


async def test_launch_eval_teams_wait_override(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = await _capture_launch(
        monkeypatch,
        agent="Demo ING",
        testset_name="smoke",
        quality_grading=True,
        judge_model="openai/gpt-4o",
        teams_wait="7",
    )
    # per-run override is passed to the child via env (TeamsClient reads it at construction)
    assert cap["kwargs"]["env"]["TEAMS_TURN_WAIT_S"] == "7"


async def test_launch_eval_teams_wait_default_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEAMS_TURN_WAIT_S", raising=False)
    cap = await _capture_launch(
        monkeypatch,
        agent="Demo ING",
        testset_name="smoke",
        quality_grading=True,
        judge_model="openai/gpt-4o",
    )
    # empty teams_wait → no override injected; child falls back to .env / built-in default
    assert "TEAMS_TURN_WAIT_S" not in cap["kwargs"]["env"]


# ── multi-agent orchestration (launch_agents / kill_all_runs) ──────────────────


class _FakeProc:
    """Minimal asyncio.subprocess.Process stand-in: async-iterable stdout + wait/kill."""

    def __init__(self, lines: list[str], rc: int = 0):
        self._lines = [(line + "\n").encode() for line in lines]
        self._rc = rc
        self.returncode = None
        self.killed = False
        self.stdout = self

    def __aiter__(self):
        self._it = iter(self._lines)
        return self

    async def __anext__(self):
        await asyncio.sleep(0)  # yield so concurrent agents can interleave
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration

    async def wait(self):
        self.returncode = self._rc
        return self._rc

    def kill(self):
        self.killed = True
        self.returncode = -9


async def test_launch_agents_runs_all(monkeypatch: pytest.MonkeyPatch) -> None:
    procs = {
        "a": _FakeProc(["capability repeat=1/1", "RUN_OK logs/a.eval"]),
        "b": _FakeProc(["capability repeat=1/1", "RUN_OK logs/b.eval"]),
    }

    async def fake_launch_eval(agent, *a, **k):
        return procs[agent]

    monkeypatch.setattr(runner, "launch_eval", fake_launch_eval)
    lines: list[tuple[str, str]] = []
    done: list[tuple[str, str]] = []

    async def on_line(agent, line):
        lines.append((agent, line))

    async def on_done(agent, info):
        done.append((agent, info["status"]))

    results = await runner.launch_agents(
        ["a", "b"], "smoke", False, "openai/gpt-4o", on_line=on_line, on_done=on_done
    )

    assert {r["agent"] for r in results} == {"a", "b"}
    assert all(r["ok"] for r in results)
    assert sorted(runner.LAST_RUN_LOGS) == ["logs/a.eval", "logs/b.eval"]
    assert {r["location"] for r in results} == {"logs/a.eval", "logs/b.eval"}
    assert ("a", "RUN_OK logs/a.eval") in lines
    assert ("a", "running") in done and ("a", "done") in done


async def test_launch_agents_failure_marks_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_launch_eval(agent, *a, **k):
        return _FakeProc(["boom — no RUN_OK"], rc=1)

    monkeypatch.setattr(runner, "launch_eval", fake_launch_eval)
    results = await runner.launch_agents(["a"], "smoke", False, "openai/gpt-4o")

    assert results[0]["ok"] is False
    assert results[0]["error"] == "failed"
    assert results[0]["location"] == ""
    assert runner.LAST_RUN_LOGS == []  # nothing produced → nothing to show


async def test_launch_agents_respects_max_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    active = 0
    peak = 0
    lock = asyncio.Lock()

    class _CapProc(_FakeProc):
        async def wait(self):
            nonlocal active
            await asyncio.sleep(0.02)
            async with lock:
                active -= 1
            self.returncode = self._rc
            return self._rc

    async def fake_launch_eval(agent, *a, **k):
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        return _CapProc([f"RUN_OK logs/{agent}.eval"])

    monkeypatch.setattr(runner, "launch_eval", fake_launch_eval)
    await runner.launch_agents(["a", "b", "c", "d"], "smoke", False, "m", max_parallel=2)
    assert peak <= 2


def test_kill_all_runs_kills_active(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _FakeProc([])
    runner._ACTIVE_PROCS.clear()
    runner._ACTIVE_PROCS["a"] = proc
    runner.kill_all_runs()
    assert proc.killed is True
    assert runner._ACTIVE_PROCS == {}
