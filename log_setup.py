"""Central loguru configuration — console + rotating file sink.

Idempotent: safe to call from multiple entry points (the eval task import and the UI
startup). Controlled by env vars:

- ``LOG_LEVEL`` — minimum level for both sinks (default ``INFO``).
- ``LOG_FILE``  — file sink path (default ``logs/teams-eval.log``); rotates at 10 MB and
  keeps 7 days of history. Set to an empty value to disable file logging.
"""

import os
import sys
from pathlib import Path

from loguru import logger

_FORMAT = "{time:DD-MM-YYYY at HH:mm:ss} | {level: <8} | {message}"
_configured = False


def configure_logging(force: bool = False) -> str | None:
    """Configure console + file logging once. Returns the active log file path (or None).

    Removes any existing sinks, adds a stderr sink and (unless ``LOG_FILE`` is blank) a
    rotating file sink. Calling again is a no-op unless ``force`` is True.
    """
    global _configured
    log_file = os.getenv("LOG_FILE", "logs/teams-eval.log").strip()
    if _configured and not force:
        return log_file or None

    level = os.getenv("LOG_LEVEL", "INFO")
    logger.remove()
    logger.add(sys.stderr, level=level, format=_FORMAT)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_file,
            level=level,
            format=_FORMAT,
            rotation="10 MB",
            retention="7 days",
            enqueue=True,
            encoding="utf-8",
        )

    _configured = True
    return log_file or None
