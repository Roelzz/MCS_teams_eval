"""Testsets page — list, create, edit and save testsets/*.json."""

from __future__ import annotations

import dataclasses
import json

import reflex as rx

from ui.components.layout import page_shell
from ui.components.widgets import card, field
from ui.services import config_io


@dataclasses.dataclass
class Turn:
    query: str = ""
    expected: str = ""


@dataclasses.dataclass
class Case:
    id: str = ""
    turns: list[Turn] = dataclasses.field(default_factory=lambda: [Turn()])
    tags: str = ""
    repeats: str = ""
    agreement_threshold: str = ""


def _blank_case(n: int) -> Case:
    return Case(id=f"case-{n}", turns=[Turn()])


class TestsetState(rx.State):
    names: list[str] = []
    current: str = ""
    new_name: str = ""
    cases: list[Case] = []
    error: str = ""
    notice: str = ""

    @rx.event
    def set_new_name(self, value: str):
        self.new_name = value

    def _load_current(self):
        data = config_io.read_testset(self.current)
        rows: list[Case] = []
        for c in data.get("cases", []):
            inp = c.get("input", "")
            target = c.get("target", "")
            if isinstance(inp, list):
                turns = [
                    Turn(query=t.get("content", ""), expected=t.get("expected", ""))
                    for t in inp
                    if t.get("role", "user") == "user"
                ]
                if target and turns and not any(t.expected for t in turns):
                    turns[-1].expected = target
            else:
                turns = [Turn(query=inp, expected=target)]
            if not turns:
                turns = [Turn()]
            rows.append(
                Case(
                    id=c.get("id", ""),
                    turns=turns,
                    tags=",".join(c.get("tags", [])),
                    repeats="" if c.get("repeats") is None else str(c["repeats"]),
                    agreement_threshold=""
                    if c.get("agreement_threshold") is None
                    else str(c["agreement_threshold"]),
                )
            )
        self.cases = rows

    @rx.event
    def load(self):
        self.names = config_io.list_testsets()
        if self.current and self.current in self.names:
            self._load_current()
        elif self.names:
            self.current = self.names[0]
            self._load_current()

    @rx.event
    def select(self, name: str):
        self.current = name
        self.error = ""
        self.notice = ""
        self._load_current()

    @rx.event
    def create(self):
        name = self.new_name.strip()
        if not name:
            self.error = "Enter a testset name"
            return
        self.current = name
        self.cases = [_blank_case(1)]
        self.new_name = ""
        if name not in self.names:
            self.names = self.names + [name]
        self.error = ""
        self.notice = f"New testset '{name}' (unsaved — edit cases then Save)."

    @rx.event
    def add_case(self):
        self.cases = self.cases + [_blank_case(len(self.cases) + 1)]

    @rx.event
    def remove_case(self, idx: int):
        self.cases = [c for i, c in enumerate(self.cases) if i != idx]

    @rx.event
    def set_field(self, idx: int, key: str, val: str):
        cases = list(self.cases)
        setattr(cases[idx], key, val)
        self.cases = cases

    @rx.event
    def add_turn(self, idx: int):
        cases = list(self.cases)
        cases[idx].turns = list(cases[idx].turns) + [Turn()]
        self.cases = cases

    @rx.event
    def remove_turn(self, idx: int, turn_idx: int):
        cases = list(self.cases)
        turns = [t for i, t in enumerate(cases[idx].turns) if i != turn_idx]
        cases[idx].turns = turns or [Turn()]
        self.cases = cases

    @rx.event
    def set_turn_field(self, idx: int, turn_idx: int, key: str, val: str):
        cases = list(self.cases)
        setattr(cases[idx].turns[turn_idx], key, val)
        self.cases = cases

    def _payload(self) -> dict | None:
        """Serialize the current editor rows into a dict, or set self.error and return None."""
        cases: list[dict] = []
        for row in self.cases:
            turns = [
                {"query": t.query.strip(), "expected": t.expected.strip()}
                for t in row.turns
                if t.query.strip()
            ]
            case: dict = {"id": row.id.strip()}
            if len(turns) <= 1:
                # single (or empty) turn → classic string input + optional case-level target
                case["input"] = turns[0]["query"] if turns else ""
                if turns and turns[0]["expected"]:
                    case["target"] = turns[0]["expected"]
            else:
                case["input"] = [
                    {"role": "user", "content": t["query"], "expected": t["expected"]}
                    for t in turns
                ]
            tags = [t.strip() for t in row.tags.split(",") if t.strip()]
            if tags:
                case["tags"] = tags
            if row.repeats.strip():
                try:
                    case["repeats"] = int(row.repeats)
                except ValueError:
                    self.error = f"repeats must be an integer (case {row.id})"
                    return None
            if row.agreement_threshold.strip():
                try:
                    case["agreement_threshold"] = float(row.agreement_threshold)
                except ValueError:
                    self.error = f"agreement_threshold must be a number (case {row.id})"
                    return None
            cases.append(case)
        return {"name": self.current.strip(), "cases": cases}

    @rx.event
    def save(self):
        if not self.current.strip():
            self.error = "No testset selected"
            return
        payload = self._payload()
        if payload is None:
            return
        try:
            config_io.save_testset(self.current.strip(), payload)
        except ValueError as e:
            self.error = str(e)
            return
        self.error = ""
        self.notice = f"Saved '{self.current}' ({len(payload['cases'])} cases)."
        self.names = config_io.list_testsets()

    @rx.event
    def download(self):
        """Export the current editor state as a Copilot Studio Evaluate Agent CSV file."""
        if not self.current.strip():
            self.error = "No testset selected"
            return
        payload = self._payload()
        if payload is None:
            return
        self.error = ""
        return rx.download(
            data=config_io.testset_to_csv(payload),
            filename=f"{self.current.strip()}.csv",
        )

    @rx.event
    def download_json(self):
        """Export the current editor state as the internal JSON format."""
        if not self.current.strip():
            self.error = "No testset selected"
            return
        payload = self._payload()
        if payload is None:
            return
        self.error = ""
        return rx.download(
            data=json.dumps(payload, indent=2) + "\n",
            filename=f"{self.current.strip()}.json",
        )

    @rx.event
    async def handle_upload(self, files: list[rx.UploadFile]):
        """Import an uploaded testset (Evaluate Agent CSV or internal JSON)."""
        if not files:
            return
        f = files[0]
        filename = getattr(f, "name", None) or getattr(f, "filename", "") or "upload.json"
        try:
            raw = await f.read()
            name = config_io.import_testset(filename, raw)
        except ValueError as e:
            self.error = f"Upload failed — {e}"
            return
        except Exception as e:  # noqa: BLE001
            self.error = f"Upload failed — {e}"
            return
        self.error = ""
        self.names = config_io.list_testsets()
        self.current = name
        self._load_current()
        self.notice = f"Imported '{name}' ({len(self.cases)} cases)."

    @rx.event
    def delete(self):
        if self.current:
            config_io.delete_testset(self.current)
            self.names = config_io.list_testsets()
            self.current = ""
            self.cases = []
            self.notice = "Deleted."


def _turn_editor(case_idx: rx.Var, turn: rx.Var, turn_idx: rx.Var) -> rx.Component:
    return rx.hstack(
        rx.badge(
            "Turn ",
            (turn_idx + 1).to_string(),
            variant="soft",
            color_scheme="gray",
            margin_top="26px",
        ),
        rx.vstack(
            field(
                "Query (user turn)",
                rx.text_area(
                    default_value=turn.query,
                    on_blur=lambda v: TestsetState.set_turn_field(case_idx, turn_idx, "query", v),
                    rows="2",
                    width="100%",
                ),
            ),
            field(
                "Expected Response (optional — graded per turn)",
                rx.text_area(
                    default_value=turn.expected,
                    on_blur=lambda v: TestsetState.set_turn_field(
                        case_idx, turn_idx, "expected", v
                    ),
                    rows="2",
                    width="100%",
                ),
            ),
            spacing="1",
            width="100%",
        ),
        rx.button(
            "✕",
            size="1",
            variant="soft",
            color_scheme="red",
            margin_top="26px",
            on_click=TestsetState.remove_turn(case_idx, turn_idx),
        ),
        width="100%",
        align="start",
        spacing="2",
    )


def _case_editor(case: rx.Var, idx: rx.Var) -> rx.Component:
    return card(
        rx.hstack(
            rx.input(
                default_value=case.id,
                on_blur=lambda v: TestsetState.set_field(idx, "id", v),
                placeholder="case id",
                width="240px",
            ),
            rx.spacer(),
            rx.button(
                "Remove case",
                size="1",
                variant="soft",
                color_scheme="red",
                on_click=TestsetState.remove_case(idx),
            ),
            width="100%",
            align="center",
        ),
        rx.vstack(
            rx.foreach(case.turns, lambda turn, ti: _turn_editor(idx, turn, ti)),
            spacing="3",
            width="100%",
            margin_top="8px",
        ),
        rx.button(
            "+ Add turn",
            size="1",
            variant="soft",
            on_click=TestsetState.add_turn(idx),
            margin_top="6px",
        ),
        rx.hstack(
            field(
                "Tags (comma-separated)",
                rx.input(
                    default_value=case.tags,
                    on_blur=lambda v: TestsetState.set_field(idx, "tags", v),
                    width="100%",
                ),
            ),
            field(
                "Repeats",
                rx.input(
                    default_value=case.repeats,
                    on_blur=lambda v: TestsetState.set_field(idx, "repeats", v),
                    placeholder="default",
                    width="100%",
                ),
            ),
            field(
                "Agreement threshold",
                rx.input(
                    default_value=case.agreement_threshold,
                    on_blur=lambda v: TestsetState.set_field(idx, "agreement_threshold", v),
                    placeholder="default",
                    width="100%",
                ),
            ),
            spacing="3",
            width="100%",
            margin_top="10px",
        ),
        margin_bottom="14px",
    )


def _picker() -> rx.Component:
    return card(
        rx.hstack(
            field(
                "Open testset",
                rx.select(
                    TestsetState.names,
                    value=TestsetState.current,
                    on_change=TestsetState.select,
                    placeholder="select…",
                ),
            ),
            rx.spacer(),
            field(
                "New testset",
                rx.hstack(
                    rx.input(
                        value=TestsetState.new_name,
                        on_change=TestsetState.setvar("new_name"),
                        placeholder="name",
                    ),
                    rx.button("Create", on_click=TestsetState.create),
                    spacing="2",
                ),
            ),
            field(
                "Import testset",
                rx.upload(
                    rx.text(
                        "Drop or click a .csv or .json",
                        font_size="0.85em",
                        color_scheme="gray",
                    ),
                    id="ts_upload",
                    accept={"text/csv": [".csv"], "application/json": [".json"]},
                    max_files=1,
                    on_drop=TestsetState.handle_upload(rx.upload_files(upload_id="ts_upload")),
                    border="1px dashed var(--gray-7)",
                    padding="9px 14px",
                    border_radius="8px",
                ),
            ),
            width="100%",
            align="end",
            spacing="4",
        ),
        margin_bottom="20px",
    )


def testsets_page() -> rx.Component:
    return page_shell(
        "Testsets",
        rx.cond(
            TestsetState.notice != "",
            rx.callout(TestsetState.notice, icon="info", margin_bottom="16px"),
        ),
        rx.cond(
            TestsetState.error != "",
            rx.callout(
                TestsetState.error, icon="triangle_alert", color_scheme="red", margin_bottom="16px"
            ),
        ),
        _picker(),
        rx.cond(
            TestsetState.current != "",
            rx.box(
                rx.hstack(
                    rx.heading(TestsetState.current, size="4"),
                    rx.spacer(),
                    rx.button("+ Add case", variant="soft", on_click=TestsetState.add_case),
                    rx.button("Save", on_click=TestsetState.save),
                    rx.button("Download CSV", variant="soft", on_click=TestsetState.download),
                    rx.button("Download JSON", variant="soft", on_click=TestsetState.download_json),
                    rx.button(
                        "Delete", variant="soft", color_scheme="red", on_click=TestsetState.delete
                    ),
                    width="100%",
                    align="center",
                    margin_bottom="12px",
                    spacing="3",
                ),
                rx.foreach(TestsetState.cases, _case_editor),
                width="100%",
            ),
        ),
        active="Testsets",
    )
