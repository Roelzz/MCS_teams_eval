import os
from pathlib import Path

import reflex as rx

# Exclude the eval-log dir from Reflex's hot reload. Inspect writes `.eval`
# files into logs/ while a run is in flight; Reflex watches every top-level
# non-hidden dir, so a new `.eval` would trigger a reload that kills the
# running eval (the run then appears wedged in the UI). We set the exclude
# env var here, before `reflex run` computes its reload paths.
_LOGS_DIR = (Path(__file__).parent / "logs").resolve()
_LOGS_DIR.mkdir(exist_ok=True)
_existing = [
    p for p in os.environ.get("REFLEX_HOT_RELOAD_EXCLUDE_PATHS", "").split(os.pathsep) if p
]
if str(_LOGS_DIR) not in _existing:
    _existing.append(str(_LOGS_DIR))
os.environ["REFLEX_HOT_RELOAD_EXCLUDE_PATHS"] = os.pathsep.join(_existing)

config = rx.Config(
    app_name="ui",
    frontend_port=2009,
    backend_port=2010,
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.RadixThemesPlugin(
            theme=rx.theme(accent_color="blue", gray_color="slate"),
        ),
    ],
)
