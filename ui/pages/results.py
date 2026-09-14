"""Results page — parity dashboard over a chosen Inspect log (reuses report.py)."""

from __future__ import annotations

from pathlib import Path

import reflex as rx
from inspect_ai.log import list_eval_logs, read_eval_log

import report
from ui.components.layout import page_shell
from ui.components.widgets import card
from ui.services import runner


def _summarize(rows: list[dict]) -> list[dict]:
    """Per-agent matched/total summary, preserving first-seen agent order."""
    by: dict[str, dict] = {}
    for r in rows:
        a = str(r.get("agent", "") or "—")
        d = by.setdefault(a, {"agent": a, "matched": 0, "total": 0})
        d["total"] += 1
        if r["result"] == "MATCH":
            d["matched"] += 1
    return list(by.values())


class ResultsState(rx.State):
    logs: list[str] = []
    log_map: dict[str, str] = {}
    current: str = ""
    rows: list[dict] = []
    filter: str = "all"
    matched: int = 0
    total: int = 0
    notice: str = ""
    error: str = ""
    # multi-agent run view (set when redirected from a multi-agent run with ?new=multi)
    multi: bool = False
    agent_summary: list[dict] = []

    @rx.var
    def differ(self) -> int:
        return self.total - self.matched

    @rx.var
    def visible_rows(self) -> list[dict]:
        if self.filter == "all":
            return self.rows
        return [r for r in self.rows if r["result"] == self.filter]

    def _finalize(self):
        self.matched = sum(1 for r in self.rows if r["result"] == "MATCH")
        self.total = len(self.rows)
        self.agent_summary = _summarize(self.rows)
        self.error = ""

    def _load_multi(self):
        try:
            self.rows = report.rows_for_logs(list(runner.LAST_RUN_LOGS))
            self._finalize()
        except Exception as e:  # noqa: BLE001
            self.rows = []
            self.matched = self.total = 0
            self.agent_summary = []
            self.error = f"Could not read run logs: {e}"

    def _load_rows(self):
        try:
            location = self.log_map.get(self.current, self.current)
            eval_log = read_eval_log(location, resolve_attachments=True)
            self.rows = report._rows(eval_log)
            self._finalize()
        except Exception as e:  # noqa: BLE001
            self.rows = []
            self.matched = self.total = 0
            self.agent_summary = []
            self.error = f"Could not read log: {e}"

    @rx.event
    def load(self):
        infos = list_eval_logs("logs")
        self.log_map = {Path(i.name).name: i.name for i in infos}
        self.logs = list(self.log_map.keys())
        fresh = self.router.page.params.get("new")
        # A multi-agent run redirects with ?new=multi → show the combined view of new logs.
        if fresh == "multi" and runner.LAST_RUN_LOGS:
            self.multi = True
            self.current = ""
            self.filter = "all"
            self._load_multi()
            return
        # list_eval_logs returns newest-first; a single run redirects with ?new=1
        # so the just-finished log is shown instead of a stale prior selection.
        self.multi = False
        if self.logs and (fresh or not self.current or self.current not in self.logs):
            self.current = self.logs[0]
        if self.current:
            self._load_rows()

    @rx.event
    def select(self, name: str):
        self.current = name
        self.filter = "all"
        self.notice = ""
        self.multi = False
        self._load_rows()

    @rx.event
    def set_filter(self, k: str):
        self.filter = k

    @rx.event
    def export(self):
        if not self.rows:
            return
        out = Path("reports")
        out.mkdir(parents=True, exist_ok=True)
        if self.multi:
            stem = "multi-agent"
            title = f"Channel parity — {len(self.agent_summary)} agents ({self.total} responses)"
        else:
            stem = Path(self.current).stem
            title = f"Channel parity — {stem} ({self.total} turn responses)"
        report._write_csv(self.rows, out / f"{stem}.csv")
        report._write_html(self.rows, out / f"{stem}.html", title)
        self.notice = f"Exported reports/{stem}.csv and .html"


def _flag(v: rx.Var) -> rx.Component:
    return rx.table.cell(
        v,
        color=rx.match(v, ("yes", "var(--green-11)"), ("no", "var(--red-11)"), "var(--gray-10)"),
        font_weight="600",
        text_align="center",
    )


def _row(r: rx.Var) -> rx.Component:
    return rx.table.row(
        rx.table.cell(rx.text(r["id"], font_family="monospace", font_size="0.78em")),
        rx.table.cell(rx.text(r["turn"], font_size="0.78em"), text_align="center"),
        rx.table.cell(rx.text(r["agent"], font_size="0.76em", color="var(--gray-11)")),
        rx.table.cell(rx.box(r["prompt"], max_width="200px", white_space="pre-wrap")),
        rx.table.cell(
            rx.box(
                r["expected"],
                max_width="200px",
                max_height="160px",
                overflow="auto",
                white_space="pre-wrap",
                color="var(--gray-11)",
            )
        ),
        rx.table.cell(rx.box(r["direct"], max_width="280px", max_height="160px", overflow="auto")),
        rx.table.cell(
            rx.vstack(
                rx.box(r["teams"], max_width="280px", max_height="160px", overflow="auto"),
                rx.cond(r["structured_html"] != "", rx.html(r["structured_html"])),
                spacing="1",
                align="start",
            )
        ),
        rx.table.cell(
            rx.vstack(
                rx.box(rx.html(r["diff_html"]), max_width="320px"),
                rx.cond(
                    r["errors"] != "",
                    rx.text(r["errors"], color="var(--red-11)", font_size="0.72em"),
                ),
                spacing="1",
                align="start",
            )
        ),
        _flag(r["normalized"]),
        _flag(r["semantic"]),
        _flag(r["structured"]),
        rx.table.cell(
            rx.vstack(
                rx.text(r["layers_summary"], font_size="0.7em", color="var(--gray-11)"),
                rx.text(r["agreement"], font_weight="700"),
                spacing="0",
                align="center",
            ),
            text_align="center",
        ),
        rx.table.cell(
            rx.text(
                r["result"],
                color=rx.cond(r["result"] == "MATCH", "var(--green-11)", "var(--red-11)"),
                font_weight="700",
            ),
            text_align="center",
        ),
        rx.table.cell(rx.text(r["quality"], font_size="0.78em"), text_align="center"),
    )


def _table() -> rx.Component:
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.table.column_header_cell("id"),
                rx.table.column_header_cell("turn"),
                rx.table.column_header_cell("agent"),
                rx.table.column_header_cell("prompt"),
                rx.table.column_header_cell("expected"),
                rx.table.column_header_cell("Direct"),
                rx.table.column_header_cell("Teams"),
                rx.table.column_header_cell("Difference"),
                rx.table.column_header_cell("norm"),
                rx.table.column_header_cell("sem"),
                rx.table.column_header_cell("struct"),
                rx.table.column_header_cell("agreement"),
                rx.table.column_header_cell("result"),
                rx.table.column_header_cell("quality"),
            )
        ),
        rx.table.body(rx.foreach(ResultsState.visible_rows, _row)),
        width="100%",
        font_size="0.82em",
    )


def _agent_chip(a: rx.Var) -> rx.Component:
    return rx.badge(
        rx.text(a["agent"], font_weight="600"),
        rx.text(" ", a["matched"].to_string(), "/", a["total"].to_string()),
        variant="soft",
        color_scheme=rx.cond(a["matched"] == a["total"], "green", "amber"),
    )


def _toolbar() -> rx.Component:
    return card(
        rx.hstack(
            rx.select(
                ResultsState.logs,
                value=ResultsState.current,
                on_change=ResultsState.select,
                placeholder=rx.cond(ResultsState.multi, "multi-agent run", "select a run…"),
            ),
            rx.cond(
                ResultsState.multi,
                rx.badge(
                    ResultsState.agent_summary.length().to_string() + " agents",
                    color_scheme="blue",
                    variant="soft",
                ),
            ),
            rx.spacer(),
            rx.button("All", size="1", variant="soft", on_click=ResultsState.set_filter("all")),
            rx.button(
                "Differences",
                size="1",
                variant="soft",
                on_click=ResultsState.set_filter("DIFFER"),
            ),
            rx.button(
                "Matches", size="1", variant="soft", on_click=ResultsState.set_filter("MATCH")
            ),
            rx.button("Export CSV/HTML", size="1", on_click=ResultsState.export),
            width="100%",
            align="center",
            spacing="2",
        ),
        rx.hstack(
            rx.text(
                "Channels matched on ",
                rx.text.strong(f"{ResultsState.matched}/{ResultsState.total}"),
                " turn responses.",
            ),
            rx.text(
                "red = only Direct · green = only Teams", color="var(--gray-11)", font_size="0.8em"
            ),
            spacing="4",
            margin_top="10px",
            align="center",
        ),
        rx.cond(
            ResultsState.multi,
            rx.hstack(
                rx.text("Per agent:", font_size="0.8em", color="var(--gray-11)"),
                rx.foreach(ResultsState.agent_summary, _agent_chip),
                spacing="2",
                margin_top="8px",
                align="center",
                wrap="wrap",
            ),
        ),
        rx.cond(
            ResultsState.notice != "",
            rx.badge(ResultsState.notice, color_scheme="green", margin_top="8px"),
        ),
        margin_bottom="16px",
    )


def results_page() -> rx.Component:
    return page_shell(
        "Results",
        rx.cond(
            ResultsState.error != "",
            rx.callout(
                ResultsState.error, icon="triangle_alert", color_scheme="red", margin_bottom="16px"
            ),
        ),
        rx.cond(
            ResultsState.logs.length() > 0,
            rx.box(_toolbar(), _table(), width="100%"),
            rx.text("No runs yet — start one on the Run page.", color="var(--gray-11)"),
        ),
        active="Results",
    )
