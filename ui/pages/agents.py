"""Agents page — CRUD over agents.json with Teams-link validation."""

from __future__ import annotations

import reflex as rx

from direct_client import agentic_runtime_url
from ui.components.layout import page_shell
from ui.components.widgets import card, field
from ui.services import config_io


class AgentsState(rx.State):
    agents: dict[str, dict] = {}
    form_name: str = ""
    description: str = ""
    environment_id: str = ""
    agent_identifier: str = ""
    teams_value: str = ""
    editing: bool = False
    error: str = ""
    notice: str = ""

    @rx.var
    def direct_endpoint(self) -> str:
        if not self.environment_id.strip() or not self.agent_identifier.strip():
            return ""
        return agentic_runtime_url(
            self.environment_id.strip(),
            self.agent_identifier.strip(),
        )

    @rx.event
    def set_form_name(self, value: str):
        self.form_name = value

    @rx.event
    def set_description(self, value: str):
        self.description = value

    @rx.event
    def set_environment_id(self, value: str):
        self.environment_id = value

    @rx.event
    def set_agent_identifier(self, value: str):
        self.agent_identifier = value

    @rx.event
    def set_teams_value(self, value: str):
        self.teams_value = value

    @rx.var
    def rows(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for name, cfg in self.agents.items():
            t = cfg.get("teams", {})
            d = cfg.get("direct", {})
            out.append(
                {
                    "name": name,
                    "description": cfg.get("description", ""),
                    "environment_id": d.get("environment_id", ""),
                    "agent_identifier": d.get("agent_identifier", ""),
                    "teams": t.get("chat_id") or t.get("chat_link") or "",
                }
            )
        return out

    @rx.event
    def load(self):
        self.agents = config_io.read_agents()

    def _clear(self):
        self.form_name = ""
        self.description = ""
        self.environment_id = ""
        self.agent_identifier = ""
        self.teams_value = ""
        self.error = ""

    @rx.event
    def new_agent(self):
        self._clear()
        self.editing = False
        self.notice = ""

    @rx.event
    def edit(self, name: str):
        cfg = self.agents.get(name, {})
        self.form_name = name
        self.description = cfg.get("description", "")
        d = cfg.get("direct", {})
        self.environment_id = d.get("environment_id", "")
        self.agent_identifier = d.get("agent_identifier", "")
        t = cfg.get("teams", {})
        self.teams_value = t.get("chat_id") or t.get("chat_link") or ""
        self.editing = True
        self.error = ""
        self.notice = ""

    @rx.event
    def save(self):
        teams_raw = self.teams_value.strip()
        teams = {"chat_id": teams_raw} if teams_raw.startswith("19:") else {"chat_link": teams_raw}
        data = {
            "description": self.description,
            "direct": {
                "environment_id": self.environment_id.strip(),
                "agent_identifier": self.agent_identifier.strip(),
            },
            "teams": teams,
        }
        try:
            config_io.save_agent(self.form_name, data)
        except ValueError as e:
            self.error = str(e)
            return
        self.agents = config_io.read_agents()
        self.notice = f"Saved '{self.form_name.strip()}'."
        self._clear()
        self.editing = False

    @rx.event
    def delete(self, name: str):
        config_io.delete_agent(name)
        self.agents = config_io.read_agents()
        self.notice = f"Deleted '{name}'."


def _row(r: rx.Var) -> rx.Component:
    return rx.table.row(
        rx.table.cell(rx.text(r["name"], font_weight="600")),
        rx.table.cell(r["agent_identifier"]),
        rx.table.cell(rx.text(r["teams"], font_size="0.78em", color="var(--gray-11)")),
        rx.table.cell(
            rx.hstack(
                rx.button("Edit", size="1", variant="soft", on_click=AgentsState.edit(r["name"])),
                rx.button(
                    "Delete",
                    size="1",
                    variant="soft",
                    color_scheme="red",
                    on_click=AgentsState.delete(r["name"]),
                ),
                spacing="2",
            )
        ),
    )


def _table() -> rx.Component:
    return card(
        rx.hstack(
            rx.heading("Agents", size="4"),
            rx.spacer(),
            rx.button("+ New agent", size="2", on_click=AgentsState.new_agent),
            width="100%",
            align="center",
            margin_bottom="12px",
        ),
        rx.cond(
            AgentsState.rows.length() > 0,
            rx.table.root(
                rx.table.header(
                    rx.table.row(
                        rx.table.column_header_cell("Name"),
                        rx.table.column_header_cell("Direct agent id"),
                        rx.table.column_header_cell("Teams target"),
                        rx.table.column_header_cell(""),
                    )
                ),
                rx.table.body(rx.foreach(AgentsState.rows, _row)),
                width="100%",
            ),
            rx.text("No agents yet — add one below.", color="var(--gray-11)"),
        ),
        margin_bottom="20px",
    )


def _form() -> rx.Component:
    return card(
        rx.heading(
            rx.cond(AgentsState.editing, "Edit agent", "New agent"),
            size="4",
            margin_bottom="12px",
        ),
        rx.vstack(
            field(
                "Name",
                rx.input(
                    value=AgentsState.form_name,
                    on_change=AgentsState.setvar("form_name"),
                    disabled=AgentsState.editing,
                    width="100%",
                ),
                hint="Unique key in agents.json. Used with --agent on the CLI.",
            ),
            field(
                "Description",
                rx.input(
                    value=AgentsState.description,
                    on_change=AgentsState.setvar("description"),
                    width="100%",
                ),
            ),
            rx.hstack(
                field(
                    "Direct · environment_id",
                    rx.input(
                        value=AgentsState.environment_id,
                        on_change=AgentsState.setvar("environment_id"),
                        width="100%",
                    ),
                ),
                field(
                    "Direct · agent_identifier",
                    rx.input(
                        value=AgentsState.agent_identifier,
                        on_change=AgentsState.setvar("agent_identifier"),
                        width="100%",
                    ),
                ),
                width="100%",
                spacing="3",
            ),
            field(
                "Teams · chat link or chat id",
                rx.input(
                    value=AgentsState.teams_value,
                    on_change=AgentsState.setvar("teams_value"),
                    placeholder="https://teams.cloud.microsoft/l/chat/19:…  or  19:…@unq.gbl",
                    width="100%",
                ),
                hint="Pasted 'Copy link' URLs are parsed automatically; validated on save.",
            ),
            field(
                "Direct · agentic runtime endpoint",
                rx.input(
                    value=AgentsState.direct_endpoint,
                    read_only=True,
                    placeholder="Enter the environment ID and agent schema name above.",
                    width="100%",
                    font_family="monospace",
                ),
                hint="Derived from the saved environment ID and schema name. "
                "The client appends /conversations and uses api-version=1.",
            ),
            rx.hstack(
                rx.button("Save", on_click=AgentsState.save),
                rx.button("Clear", variant="soft", on_click=AgentsState.new_agent),
                spacing="3",
            ),
            rx.cond(
                AgentsState.error != "",
                rx.callout(AgentsState.error, icon="triangle_alert", color_scheme="red"),
            ),
            spacing="3",
            width="100%",
            align="start",
        ),
    )


def agents_page() -> rx.Component:
    return page_shell(
        "Agents",
        rx.cond(
            AgentsState.notice != "",
            rx.callout(AgentsState.notice, icon="info", margin_bottom="16px"),
        ),
        _table(),
        _form(),
        active="Agents",
    )
