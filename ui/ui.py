"""Reflex app entry point — registers every page.

Run the UI:
    uv run reflex run        # http://localhost:2009
"""

import reflex as rx

from log_setup import configure_logging
from ui.pages.agents import AgentsState, agents_page
from ui.pages.results import ResultsState, results_page
from ui.pages.run import RunState, run_page
from ui.pages.settings import SettingsState, settings_page
from ui.pages.setup import setup_page
from ui.pages.testsets import TestsetState, testsets_page
from ui.state import AuthState

configure_logging()

app = rx.App()
app.add_page(setup_page, route="/", title="Setup · Teams Parity", on_load=AuthState.refresh_auth)
app.add_page(agents_page, route="/agents", title="Agents · Teams Parity", on_load=AgentsState.load)
app.add_page(
    testsets_page,
    route="/testsets",
    title="Testsets · Teams Parity",
    on_load=TestsetState.load,
)
app.add_page(
    settings_page,
    route="/settings",
    title="Settings · Teams Parity",
    on_load=SettingsState.load,
)
app.add_page(run_page, route="/run", title="Run · Teams Parity", on_load=RunState.load)
app.add_page(
    results_page, route="/results", title="Results · Teams Parity", on_load=ResultsState.load
)
