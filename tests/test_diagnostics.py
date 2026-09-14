"""Tests for the Copilot Studio diagnostics parser used by the Setup paste feature."""

from __future__ import annotations

from ui.services import diagnostics

# The exact 'Diagnostic info' block a user copies out of Copilot Studio.
DIAG = """Diagnostic info

Account
Roel Schenk
Email
roel.schenk@copilotstudiotraining.onmicrosoft.com
Environment
RRS - Preview
Environment ID
2dd2ec79-3f5b-e241-b733-f7e34196b913
Cluster
public
Theme
light
Route
/environments/2dd2ec79-3f5b-e241-b733-f7e34196b913/agents/401a696b-caa6-44bb-b4d9-20121838b685/preview
Session ID
fe93ee10-6886-11f1-a7e0-b5956e055675
Build version
22.8.2
"""


def test_parses_full_block():
    out = diagnostics.parse_session_details(DIAG)
    assert out["environment_id"] == "2dd2ec79-3f5b-e241-b733-f7e34196b913"
    assert out["bot_id"] == "401a696b-caa6-44bb-b4d9-20121838b685"
    assert out["tenant_domain"] == "copilotstudiotraining.onmicrosoft.com"


def test_environment_id_via_label_only():
    out = diagnostics.parse_session_details(
        "Environment ID\n2dd2ec79-3f5b-e241-b733-f7e34196b913\n"
    )
    assert out["environment_id"] == "2dd2ec79-3f5b-e241-b733-f7e34196b913"
    assert "bot_id" not in out


def test_does_not_invent_schema_name():
    # The Direct schema name is never derivable from diagnostics.
    out = diagnostics.parse_session_details(DIAG)
    assert "agent_identifier" not in out
    assert "schema_name" not in out


def test_empty_input_is_empty():
    assert diagnostics.parse_session_details("") == {}
    assert diagnostics.parse_session_details("nothing useful here") == {}


def test_vanity_email_domain():
    out = diagnostics.parse_session_details("Email\njane.doe@contoso.com\n")
    assert out["tenant_domain"] == "contoso.com"
