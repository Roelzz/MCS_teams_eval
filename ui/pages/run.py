"""Run page — launch a parity eval against one or more agents and stream live progress."""

from __future__ import annotations

import asyncio

import reflex as rx

from ui.components.layout import page_shell
from ui.components.widgets import card, field, log_console
from ui.services import config_io, runner


class RunState(rx.State):
    agents: list[str] = []
    testsets: list[str] = []
    selected_agents: list[str] = []
    testset: str = ""
    quality: bool = True
    use_judge: bool = True
    judge_model: str = ""
    teams_wait: str = "10"
    running: bool = False
    done: bool = False
    error: str = ""
    log_lines: list[str] = []
    total: int = 0
    started: int = 0
    agent_status: dict[str, str] = {}

    @rx.event
    def toggle_agent(self, name: str):
        if name in self.selected_agents:
            self.selected_agents = [a for a in self.selected_agents if a != name]
        else:
            self.selected_agents = self.selected_agents + [name]

    @rx.event
    def set_testset(self, value: str):
        self.testset = value

    @rx.event
    def set_judge_model(self, value: str):
        self.judge_model = value

    @rx.event
    def set_teams_wait(self, value: str):
        self.teams_wait = value

    @rx.event
    def set_quality(self, value: bool):
        self.quality = value

    @rx.event
    def set_use_judge(self, value: bool):
        self.use_judge = value
        if not value:
            self.quality = False

    @rx.var
    def progress_label(self) -> str:
        if self.total:
            return f"{self.started}/{self.total} samples started"
        return f"{self.started} samples started"

    @rx.event
    def load(self):
        self.agents = list(config_io.read_agents().keys())
        self.testsets = config_io.list_testsets()
        if self.agents and not self.selected_agents:
            self.selected_agents = [self.agents[0]]
        if self.testsets and not self.testset:
            self.testset = self.testsets[0]
        self.judge_model = config_io.effective_env().get("JUDGE_MODEL", "")
        self.teams_wait = config_io.effective_env().get("TEAMS_TURN_WAIT_S", "10")
        self.agent_status = {a: "" for a in self.agents}
        if not self.running:
            self.error = ""
            self.done = False
            self.started = 0

    @rx.event(background=True)
    async def start(self):
        async with self:
            if not self.selected_agents or not self.testset:
                self.error = "Pick at least one agent and a testset first."
                return
            if self.use_judge:
                if not self.judge_model.strip():
                    self.error = (
                        "Set a judge model first (Settings → Judge model, e.g. openai/gpt-4o) — "
                        "or turn off 'Use LLM judge' to run deterministically."
                    )
                    return
                missing_keys = runner.missing_judge_key(self.judge_model)
                if missing_keys:
                    self.error = (
                        f"Judge model '{self.judge_model}' needs {', '.join(missing_keys)} — "
                        "set it in Settings (or .env), save, then run."
                    )
                    return
            self.running = True
            self.done = False
            self.error = ""
            self.log_lines = []
            self.started = 0
            agents = list(self.selected_agents)
            self.agent_status = {a: ("queued" if a in agents else "") for a in self.agents}
            try:
                cases = len(config_io.read_testset(self.testset).get("cases", []))
            except Exception:
                cases = 0
            self.total = cases * len(agents)
            testset, quality, judge, use_judge, teams_wait = (
                self.testset,
                self.quality,
                self.judge_model,
                self.use_judge,
                self.teams_wait,
            )
            try:
                max_parallel = int(config_io.effective_env().get("MAX_PARALLEL_AGENTS", "3") or 3)
            except ValueError:
                max_parallel = 3

        # Producer tasks (one subprocess per agent) push events onto a queue; this single
        # background coroutine is the only writer to `self`, avoiding concurrent state access.
        queue: asyncio.Queue = asyncio.Queue()

        async def on_line(agent: str, line: str):
            await queue.put(("line", agent, line))

        async def on_done(agent: str, info: dict):
            await queue.put(("done", agent, info))

        async def _run_and_signal():
            try:
                return await runner.launch_agents(
                    agents,
                    testset,
                    quality,
                    judge,
                    use_judge=use_judge,
                    teams_wait=teams_wait,
                    max_parallel=max_parallel,
                    on_line=on_line,
                    on_done=on_done,
                )
            finally:
                await queue.put(("__end__", "", None))

        run_task = asyncio.create_task(_run_and_signal())
        while True:
            kind, agent, payload = await queue.get()
            if kind == "__end__":
                break
            async with self:
                if kind == "line":
                    self.log_lines = self.log_lines + [f"[{agent}] {payload}"]
                    if "repeat=1/" in payload:
                        self.started += 1
                else:
                    status = (payload or {}).get("status", "")
                    self.agent_status = {**self.agent_status, agent: status}
        results = await run_task

        any_ok = any(r["ok"] for r in results)
        all_ok = all(r["ok"] for r in results) if results else False
        all_cancelled = bool(results) and all(r.get("error") == "cancelled" for r in results)
        failed = [r["agent"] for r in results if not r["ok"]]

        async with self:
            self.running = False
            self.done = any_ok and not all_cancelled
            if all_cancelled:
                self.error = "Run cancelled."
            elif not any_ok:
                self.error = "All runs failed — see log above."
            elif not all_ok:
                self.error = f"Some agents failed: {', '.join(failed)}. Showing the rest."

        if any_ok and not all_cancelled:
            return rx.redirect("/results?new=multi")

    @rx.event
    def cancel_run(self):
        """Kill all in-flight agent runs and unblock the Start button."""
        runner.kill_all_runs()
        self.running = False
        self.done = False
        self.error = "Run cancelled."


def _agent_row(a: rx.Var) -> rx.Component:
    status = RunState.agent_status[a]
    return rx.hstack(
        rx.checkbox(
            checked=RunState.selected_agents.contains(a),
            on_change=RunState.toggle_agent(a),
        ),
        rx.text(a, font_size="0.9em"),
        rx.spacer(),
        rx.cond(
            status != "",
            rx.badge(
                status,
                color_scheme=rx.match(
                    status,
                    ("done", "green"),
                    ("running", "blue"),
                    ("queued", "gray"),
                    ("cancelled", "amber"),
                    "red",
                ),
                variant="soft",
                size="1",
            ),
        ),
        width="100%",
        align="center",
        spacing="2",
    )


def _config_card() -> rx.Component:
    return card(
        rx.heading("Run a parity eval", size="4", margin_bottom="12px"),
        rx.hstack(
            field(
                "Agents",
                rx.vstack(
                    rx.foreach(RunState.agents, _agent_row),
                    spacing="1",
                    width="100%",
                    max_height="200px",
                    overflow="auto",
                    padding="8px",
                    border="1px solid var(--gray-6)",
                    border_radius="8px",
                ),
                hint="Pick one or more — selected agents run in parallel, each doing its own "
                "Teams-vs-Direct parity (capped by MAX_PARALLEL_AGENTS).",
            ),
            field(
                "Testset",
                rx.select(
                    RunState.testsets,
                    value=RunState.testset,
                    on_change=RunState.setvar("testset"),
                    placeholder="select testset…",
                ),
            ),
            spacing="3",
            width="100%",
            align="start",
        ),
        rx.hstack(
            field(
                "Judge model",
                rx.input(
                    value=RunState.judge_model,
                    on_change=RunState.setvar("judge_model"),
                    width="100%",
                    disabled=~RunState.use_judge,
                ),
                hint="Inspect model string — openai/gpt-4o or azureai/gpt-4o",
            ),
            rx.hstack(
                rx.switch(checked=RunState.use_judge, on_change=RunState.set_use_judge),
                rx.text("Use LLM judge", font_size="0.9em"),
                spacing="2",
                align="center",
            ),
            rx.hstack(
                rx.switch(
                    checked=RunState.quality,
                    on_change=RunState.setvar("quality"),
                    disabled=~RunState.use_judge,
                ),
                rx.text("Quality grading", font_size="0.9em"),
                spacing="2",
                align="center",
            ),
            spacing="4",
            width="100%",
            align="end",
            margin_top="8px",
        ),
        rx.hstack(
            field(
                "Teams turn wait (s)",
                rx.input(
                    value=RunState.teams_wait,
                    on_change=RunState.set_teams_wait,
                    type="number",
                    width="120px",
                ),
                hint="Sequential eval: wait this long after each Teams prompt before polling "
                "(never retries before it). Paces the shared chat so prompts don't flood.",
            ),
            spacing="3",
            width="100%",
            margin_top="8px",
        ),
        rx.cond(
            ~RunState.use_judge,
            rx.text(
                "Deterministic mode — compares normalized text only (no semantic/quality). "
                "No OpenAI/Foundry key needed.",
                font_size="0.8em",
                color_scheme="gray",
                margin_top="6px",
            ),
        ),
        rx.hstack(
            rx.button(
                rx.cond(RunState.running, "Running…", "Start run"),
                on_click=RunState.start,
                disabled=RunState.running,
            ),
            rx.cond(
                RunState.running,
                rx.button(
                    "Cancel",
                    on_click=RunState.cancel_run,
                    color_scheme="red",
                    variant="soft",
                ),
            ),
            rx.cond(
                RunState.running | RunState.done,
                rx.badge(RunState.progress_label, color_scheme="blue"),
            ),
            spacing="3",
            align="center",
            margin_top="16px",
        ),
        rx.cond(
            RunState.error != "",
            rx.callout(
                RunState.error, icon="triangle_alert", color_scheme="red", margin_top="12px"
            ),
        ),
        margin_bottom="20px",
    )


def run_page() -> rx.Component:
    return page_shell(
        "Run",
        _config_card(),
        rx.cond(
            RunState.log_lines.length() > 0,
            card(
                rx.heading("Live log", size="3", margin_bottom="8px"),
                log_console(RunState.log_lines, height="320px"),
            ),
        ),
        active="Run",
    )
