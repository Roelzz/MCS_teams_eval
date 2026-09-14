"""Parse a Copilot Studio 'Diagnostic info' block into agent coordinates.

The diagnostics panel (Account / Environment / Route / …) carries the Environment ID, the
bot GUID (from the Route), and the signed-in email. It does NOT contain the Direct-to-Engine
*schema name* (e.g. ``cr1bd_myAgent``) that the Direct channel needs, so that is never
returned here — the user must supply it separately.
"""

from __future__ import annotations

import re

_GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

_ROUTE_RE = re.compile(rf"/environments/({_GUID})/agents/({_GUID})")
_ENV_LABEL_RE = re.compile(rf"Environment ID[\s:\-]*({_GUID})", re.IGNORECASE)
_AGENT_RE = re.compile(rf"/agents/({_GUID})")
_EMAIL_RE = re.compile(r"[\w.+-]+@([A-Za-z0-9-]+\.[A-Za-z0-9.-]+)")


def parse_session_details(text: str) -> dict[str, str]:
    """Extract environment_id, bot_id and tenant_domain from a pasted diagnostics block.

    Only keys that are found are included.
    """
    out: dict[str, str] = {}
    text = text or ""

    m = _ROUTE_RE.search(text)
    if m:
        out["environment_id"], out["bot_id"] = m.group(1), m.group(2)

    if "environment_id" not in out:
        m = _ENV_LABEL_RE.search(text)
        if m:
            out["environment_id"] = m.group(1)

    if "bot_id" not in out:
        m = _AGENT_RE.search(text)
        if m:
            out["bot_id"] = m.group(1)

    m = _EMAIL_RE.search(text)
    if m:
        out["tenant_domain"] = m.group(1).lower()

    return out
