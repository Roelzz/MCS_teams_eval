"""Settings page — edit the .env knobs that drive comparison, polling and the judge."""

from __future__ import annotations

import reflex as rx

from ui.components.layout import page_shell
from ui.components.widgets import card, field
from ui.services import config_io

BOOL_KEYS = {
    "COMPARE_SEMANTIC",
    "COMPARE_STRUCTURED",
    "COMPARE_CITATIONS",
    "COMPARE_CARDS",
    "COMPARE_SUGGESTED",
}
SECRET_KEYS = {"OPENAI_API_KEY", "AZUREAI_OPENAI_API_KEY"}

ALL_KEYS = [
    "LOG_LEVEL",
    "AZURE_AD_TENANT_ID",
    "AZURE_AD_CLIENT_ID",
    "COMPARE_SEMANTIC",
    "COMPARE_STRUCTURED",
    "COMPARE_CITATIONS",
    "COMPARE_CARDS",
    "COMPARE_SUGGESTED",
    "MATCH_POLICY",
    "DEFAULT_REPEATS",
    "DEFAULT_AGREEMENT_THRESHOLD",
    "TEAMS_POLL_INTERVAL_S",
    "TEAMS_POLL_TIMEOUT_S",
    "TEAMS_QUIET_WINDOW_S",
    "TEAMS_RATE_DELAY_S",
    "TEAMS_TURN_WAIT_S",
    "TEAMS_RESET_COMMANDS",
    "JUDGE_MODEL",
    "OPENAI_API_KEY",
    "AZUREAI_OPENAI_API_KEY",
    "AZUREAI_OPENAI_BASE_URL",
    "AZUREAI_OPENAI_API_VERSION",
]


class SettingsState(rx.State):
    values: dict[str, str] = {}
    notice: str = ""

    @rx.event
    def load(self):
        merged = config_io.effective_env()
        for k in ALL_KEYS:
            merged.setdefault(k, "")
        self.values = merged
        self.notice = ""

    @rx.event
    def set_value(self, key: str, val: str):
        self.values[key] = val

    @rx.event
    def toggle(self, key: str, on: bool):
        self.values[key] = "true" if on else "false"

    @rx.event
    def save(self):
        config_io.write_env({k: self.values.get(k, "") for k in ALL_KEYS})
        self.notice = "Saved to .env."


def _text(key: str, label: str, hint: str = "") -> rx.Component:
    return field(
        label,
        rx.input(
            default_value=SettingsState.values[key],
            on_blur=lambda v: SettingsState.set_value(key, v),
            type="password" if key in SECRET_KEYS else "text",
            width="100%",
        ),
        hint,
    )


def _bool(key: str, label: str) -> rx.Component:
    return rx.hstack(
        rx.switch(
            checked=SettingsState.values[key] == "true",
            on_change=lambda v: SettingsState.toggle(key, v),
        ),
        rx.text(label, font_size="0.9em"),
        spacing="2",
        align="center",
    )


def _comparison() -> rx.Component:
    return card(
        rx.heading("Comparison", size="4", margin_bottom="12px"),
        rx.vstack(
            _bool("COMPARE_SEMANTIC", "Semantic (model-graded) layer"),
            _bool("COMPARE_STRUCTURED", "Structured layer (citations / cards / suggested)"),
            _bool("COMPARE_CITATIONS", "Compare citations"),
            _bool("COMPARE_CARDS", "Compare adaptive cards"),
            _bool("COMPARE_SUGGESTED", "Compare suggested actions"),
            spacing="2",
            align="start",
        ),
        rx.box(
            _text(
                "MATCH_POLICY",
                "Match policy",
                "Layers that must agree for a sample to match — any of: normalized,semantic",
            ),
            margin_top="12px",
        ),
        margin_bottom="20px",
    )


def _repeats() -> rx.Component:
    return card(
        rx.heading("Repeats & agreement", size="4", margin_bottom="12px"),
        rx.hstack(
            _text("DEFAULT_REPEATS", "Default repeats"),
            _text("DEFAULT_AGREEMENT_THRESHOLD", "Default agreement threshold"),
            spacing="3",
            width="100%",
        ),
        margin_bottom="20px",
    )


def _teams() -> rx.Component:
    return card(
        rx.heading("Teams channel", size="4", margin_bottom="12px"),
        rx.hstack(
            _text("TEAMS_POLL_INTERVAL_S", "Poll interval (s)"),
            _text("TEAMS_POLL_TIMEOUT_S", "Poll timeout (s)"),
            spacing="3",
            width="100%",
        ),
        rx.hstack(
            _text("TEAMS_QUIET_WINDOW_S", "Quiet window (s)"),
            _text("TEAMS_RATE_DELAY_S", "Rate delay (s)"),
            spacing="3",
            width="100%",
            margin_top="8px",
        ),
        rx.box(
            _text(
                "TEAMS_TURN_WAIT_S",
                "Turn wait (s)",
                "Sequential eval: wait this long after each Teams prompt before polling "
                "(never retries before it). Paces the shared chat. Overridable per-run on Run.",
            ),
            margin_top="8px",
        ),
        rx.box(
            _text(
                "TEAMS_RESET_COMMANDS",
                "Reset commands (comma-separated)",
                "e.g. /debug clearstate,/debug clearhistory — sent before each sample.",
            ),
            margin_top="8px",
        ),
        margin_bottom="20px",
    )


def _judge() -> rx.Component:
    return card(
        rx.heading("Judge model", size="4", margin_bottom="12px"),
        _text("JUDGE_MODEL", "Inspect model string", "e.g. openai/gpt-4o or azureai/gpt-4o"),
        rx.hstack(
            _text("OPENAI_API_KEY", "OPENAI_API_KEY"),
            _text("AZUREAI_OPENAI_API_KEY", "AZUREAI_OPENAI_API_KEY"),
            spacing="3",
            width="100%",
            margin_top="8px",
        ),
        rx.hstack(
            _text("AZUREAI_OPENAI_BASE_URL", "AZUREAI_OPENAI_BASE_URL"),
            _text("AZUREAI_OPENAI_API_VERSION", "AZUREAI_OPENAI_API_VERSION"),
            spacing="3",
            width="100%",
            margin_top="8px",
        ),
        margin_bottom="20px",
    )


def _general() -> rx.Component:
    return card(
        rx.heading("General", size="4", margin_bottom="12px"),
        rx.hstack(
            field(
                "LOG_LEVEL",
                rx.select(
                    ["DEBUG", "INFO", "WARNING", "ERROR"],
                    value=SettingsState.values["LOG_LEVEL"],
                    on_change=lambda v: SettingsState.set_value("LOG_LEVEL", v),
                ),
            ),
            spacing="3",
            width="100%",
        ),
        rx.hstack(
            _text("AZURE_AD_TENANT_ID", "AZURE_AD_TENANT_ID"),
            _text("AZURE_AD_CLIENT_ID", "AZURE_AD_CLIENT_ID"),
            spacing="3",
            width="100%",
            margin_top="8px",
        ),
        margin_bottom="20px",
    )


def settings_page() -> rx.Component:
    return page_shell(
        "Settings",
        rx.hstack(
            rx.button("Save to .env", on_click=SettingsState.save),
            rx.cond(
                SettingsState.notice != "",
                rx.badge(SettingsState.notice, color_scheme="green"),
            ),
            spacing="3",
            align="center",
            margin_bottom="16px",
        ),
        _general(),
        _comparison(),
        _repeats(),
        _teams(),
        _judge(),
        active="Settings",
    )
