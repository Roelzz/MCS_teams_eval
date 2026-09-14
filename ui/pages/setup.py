"""Setup page — paste session details, app-registration provisioning, interactive sign-in."""

from __future__ import annotations

import reflex as rx

import auth
import setup_app
from teams_client import parse_chat_id
from ui.components.layout import page_shell
from ui.components.widgets import card, field, log_console
from ui.services import config_io, diagnostics, logstream
from ui.state import AuthState


class SetupState(rx.State):
    # App-registration provisioning
    app_name: str = "teams-eval"
    existing_client_id: str = ""
    running: bool = False
    log_lines: list[str] = []
    summary: str = ""
    creds_missing: list[str] = []

    # Session-details paste
    diag_text: str = ""
    teams_link: str = ""
    schema_name: str = ""
    paste_agent_name: str = "demo-agent"
    paste_summary: str = ""
    paste_error: str = ""

    @rx.event
    def set_app_name(self, value: str):
        self.app_name = value

    @rx.event
    def set_existing_client_id(self, value: str):
        self.existing_client_id = value

    @rx.event
    def set_diag_text(self, value: str):
        self.diag_text = value

    @rx.event
    def set_teams_link(self, value: str):
        self.teams_link = value

    @rx.event
    def set_schema_name(self, value: str):
        self.schema_name = value

    @rx.event
    def set_paste_agent_name(self, value: str):
        self.paste_agent_name = value

    @rx.event
    def check_creds(self):
        self.creds_missing = auth.missing_credentials()

    @rx.event
    def parse_and_save(self):
        """Extract agent coordinates from the pasted diagnostics + Teams link and save them."""
        self.paste_error = ""
        self.paste_summary = ""

        details = diagnostics.parse_session_details(self.diag_text)
        env_id = details.get("environment_id", "")

        chat_id = ""
        if self.teams_link.strip():
            try:
                chat_id = parse_chat_id(self.teams_link.strip())
            except ValueError as exc:
                self.paste_error = f"Teams link: {exc}"
                return

        missing: list[str] = []
        if not env_id:
            missing.append("Environment ID (paste the full Diagnostic info block)")
        if not self.schema_name.strip():
            missing.append(
                "Direct schema name — not in the diagnostics; copy it from Copilot Studio "
                "› your agent › Settings › Advanced › Metadata › Schema name (e.g. cr1bd_myAgent). "
                "The bot GUID will not work for Direct"
            )
        if not chat_id:
            missing.append("Teams chat (paste the Teams agent link)")
        if missing:
            self.paste_error = "Still need: " + "; ".join(missing)
            return

        agent = {
            "direct": {"environment_id": env_id, "agent_identifier": self.schema_name.strip()},
            "teams": {"chat_id": chat_id},
        }
        try:
            config_io.save_agent(self.paste_agent_name, agent)
        except ValueError as exc:
            self.paste_error = str(exc)
            return

        name = self.paste_agent_name.strip() or "demo-agent"
        parts = [
            f"Saved agent '{name}'",
            f"environment_id={env_id}",
            "Teams chat_id set",
            f"Direct schema name={self.schema_name.strip()}",
        ]

        domain = details.get("tenant_domain", "")
        if domain and not config_io.read_env().get("AZURE_AD_TENANT_ID", "").strip():
            config_io.write_env({"AZURE_AD_TENANT_ID": domain})
            auth.reload_config()
            parts.append(f"seeded AZURE_AD_TENANT_ID={domain} for sign-in")

        if details.get("bot_id"):
            parts.append(f"(bot GUID {details['bot_id']} — reference only)")

        self.paste_summary = "  •  ".join(parts) + "."
        return SetupState.check_creds

    async def _push(self, lines: list[str]) -> None:
        async with self:
            self.log_lines = self.log_lines + lines

    @rx.event(background=True)
    async def provision(self):
        async with self:
            self.running = True
            self.summary = ""
            self.log_lines = [f"Creating app registration '{self.app_name}'…"]
            name = self.app_name
        ok, result, err = await logstream.run_threaded(
            setup_app.provision_app, name, on_lines=self._push
        )
        async with self:
            self.running = False
            if ok:
                self.summary = (
                    f"tenant={result['tenant_id']}  client_id={result['client_id']}  "
                    "— wrote AZURE_AD_* to .env"
                )
                auth.reload_config()
            else:
                self.log_lines = self.log_lines + [f"ERROR: {err}"]
        if ok:
            return [AuthState.refresh_auth, SetupState.check_creds]

    @rx.event(background=True)
    async def add_scopes(self):
        async with self:
            if not self.existing_client_id.strip():
                self.log_lines = ["ERROR: enter an existing client_id first"]
                return
            self.running = True
            self.summary = ""
            self.log_lines = [f"Adding Graph scopes to {self.existing_client_id}…"]
            cid = self.existing_client_id.strip()
        ok, result, err = await logstream.run_threaded(
            setup_app.add_graph_scopes_to, cid, on_lines=self._push
        )
        async with self:
            self.running = False
            if ok:
                self.summary = (
                    f"tenant={result['tenant_id']}  client_id={result['client_id']}  "
                    "— added Graph scopes + wrote AZURE_AD_* to .env"
                )
                auth.reload_config()
            else:
                self.log_lines = self.log_lines + [f"ERROR: {err}"]
        if ok:
            return [AuthState.refresh_auth, SetupState.check_creds]


def _session_card() -> rx.Component:
    return card(
        rx.heading("1 · Session details (paste)", size="4", margin_bottom="8px"),
        rx.text(
            "Paste the Copilot Studio 'Diagnostic info' block and your Teams agent link to "
            "pre-fill an agent. This fills the Environment ID (Direct) and the Teams chat, and "
            "seeds your tenant for sign-in.",
            color="var(--gray-11)",
            font_size="0.9em",
            margin_bottom="12px",
        ),
        field(
            "Diagnostic info",
            rx.text_area(
                value=SetupState.diag_text,
                on_change=SetupState.setvar("diag_text"),
                placeholder=(
                    "Environment ID\n2dd2ec79-…\nRoute\n/environments/…/agents/…/preview\n"
                    "Email\nyou@contoso.onmicrosoft.com"
                ),
                height="150px",
                width="100%",
            ),
        ),
        rx.box(height="12px"),
        field(
            "Teams agent link",
            rx.input(
                value=SetupState.teams_link,
                on_change=SetupState.setvar("teams_link"),
                placeholder="https://teams.cloud.microsoft/l/chat/19:…@unq.gbl.spaces/…",
                width="100%",
            ),
        ),
        rx.box(height="12px"),
        rx.hstack(
            field(
                "Agent name",
                rx.input(
                    value=SetupState.paste_agent_name,
                    on_change=SetupState.setvar("paste_agent_name"),
                    width="100%",
                ),
            ),
            field(
                "Direct schema name",
                rx.input(
                    value=SetupState.schema_name,
                    on_change=SetupState.setvar("schema_name"),
                    placeholder="cr1bd_myAgent",
                    width="100%",
                ),
                "Not in the diagnostics — Copilot Studio › agent › Settings › Advanced › "
                "Metadata › Schema name. The bot GUID won't work for Direct.",
            ),
            spacing="3",
            width="100%",
            align="start",
        ),
        rx.box(height="14px"),
        rx.button("Parse & save agent", on_click=SetupState.parse_and_save),
        rx.cond(
            SetupState.paste_error != "",
            rx.callout(
                SetupState.paste_error,
                icon="triangle_alert",
                color_scheme="red",
                margin_top="12px",
            ),
        ),
        rx.cond(
            SetupState.paste_summary != "",
            rx.callout(
                SetupState.paste_summary,
                icon="check",
                color_scheme="green",
                margin_top="12px",
            ),
        ),
        margin_bottom="20px",
        on_mount=SetupState.check_creds,
    )


def _provision_card() -> rx.Component:
    return card(
        rx.heading("2 · App registration", size="4", margin_bottom="8px"),
        rx.text(
            "Create a new Entra app with Graph + Power Platform delegated scopes (and admin "
            "consent), or add the Graph (Teams) scopes to an existing app. Either writes "
            "AZURE_AD_CLIENT_ID + AZURE_AD_TENANT_ID to .env so you can sign in next. Requires "
            "rights to create app registrations and grant consent.",
            color="var(--gray-11)",
            font_size="0.9em",
            margin_bottom="16px",
        ),
        rx.hstack(
            field(
                "New app display name",
                rx.input(
                    value=SetupState.app_name,
                    on_change=SetupState.setvar("app_name"),
                    width="100%",
                ),
            ),
            rx.button(
                "Create app",
                on_click=SetupState.provision,
                disabled=SetupState.running,
            ),
            align="end",
            spacing="3",
            width="100%",
        ),
        rx.divider(margin="16px 0"),
        rx.hstack(
            field(
                "Existing client_id (extend with Graph scopes)",
                rx.input(
                    value=SetupState.existing_client_id,
                    on_change=SetupState.setvar("existing_client_id"),
                    placeholder="00000000-0000-0000-0000-000000000000",
                    width="100%",
                ),
            ),
            rx.button(
                "Add Graph scopes",
                on_click=SetupState.add_scopes,
                disabled=SetupState.running,
                variant="outline",
            ),
            align="end",
            spacing="3",
            width="100%",
        ),
        rx.cond(
            SetupState.summary != "",
            rx.callout(
                SetupState.summary,
                icon="check",
                color_scheme="green",
                margin_top="16px",
            ),
        ),
        rx.cond(
            SetupState.log_lines.length() > 0,
            rx.box(log_console(SetupState.log_lines), margin_top="16px"),
        ),
        margin_bottom="20px",
    )


def _auth_card() -> rx.Component:
    return card(
        rx.heading("3 · Sign in", size="4", margin_bottom="8px"),
        rx.text(
            "Interactive browser login. One sign-in vends tokens for both Teams (Graph) "
            "and Direct (Power Platform). Needs an app registration (step 2) first.",
            color="var(--gray-11)",
            font_size="0.9em",
            margin_bottom="12px",
        ),
        rx.cond(
            SetupState.creds_missing.length() > 0,
            rx.callout(
                "No app registration yet — complete step 2 (App registration) first, then sign in.",
                icon="info",
                color_scheme="amber",
                margin_bottom="12px",
            ),
        ),
        rx.hstack(
            rx.button(
                rx.cond(AuthState.auth_busy, "Signing in…", "Sign in"),
                on_click=AuthState.sign_in,
                disabled=AuthState.auth_busy,
            ),
            rx.badge(AuthState.status_label, color_scheme="gray"),
            align="center",
            spacing="3",
        ),
        rx.cond(
            AuthState.auth_msg != "",
            rx.text(
                AuthState.auth_msg, color="var(--gray-11)", font_size="0.8em", margin_top="8px"
            ),
        ),
        margin_bottom="20px",
    )


def setup_page() -> rx.Component:
    return page_shell(
        "Setup",
        _session_card(),
        _provision_card(),
        _auth_card(),
        active="Setup",
    )
