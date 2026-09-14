"""Create or extend the Entra app registration used by teams-eval.

The tool signs in interactively (delegated) and needs ONE public-client app registration carrying
delegated permissions for two APIs:

  • Microsoft Graph   — ChatMessage.Send, Chat.ReadWrite     (Teams channel)
  • Power Platform API — CopilotStudio.Copilots.Invoke, ...   (Direct-to-Engine channel)

Adapted from the original evals/provisioner.py (which set up only the Power Platform side); this
version also wires the Microsoft Graph delegated scopes needed for Teams.

Commands:
  create            Create a brand new app registration with BOTH APIs + admin consent.
  add-graph-scopes  Add the Graph (Teams) scopes + consent to an EXISTING app registration.

Running this requires a sign-in with rights to create/modify app registrations and grant admin
consent (Application.ReadWrite.All). It signs in with an interactive browser pop-up (Azure CLI
public client).

The ``provision_app`` and ``add_graph_scopes_to`` functions are importable so the Reflex UI can
drive provisioning; the typer commands below are thin wrappers around them.
"""

import jwt
import msal
import typer
from dotenv import load_dotenv
from httpx import Client
from loguru import logger

from log_setup import configure_logging

load_dotenv()
configure_logging()

app = typer.Typer(add_completion=False)

_CLI_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"  # Azure CLI public client
_LOGIN_SCOPES = ["https://graph.microsoft.com/Application.ReadWrite.All"]
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"

_GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"
_POWER_PLATFORM_APP_ID = "8578e004-a5c6-46e7-913e-12f58912df43"

_GRAPH_SCOPES = ["Chat.ReadWrite", "ChatMessage.Send"]
_PP_SCOPES = ["CopilotStudio.Copilots.Invoke", "user_impersonation"]

ENV_FILE = ".env"


# ── Login ────────────────────────────────────────────────────────────────────


def _interactive_login() -> tuple[dict, str]:
    """Interactive browser login. Returns (graph_headers, access_token)."""
    msal_app = msal.PublicClientApplication(
        client_id=_CLI_CLIENT_ID, authority="https://login.microsoftonline.com/common"
    )
    logger.info("Opening browser for sign-in (admin consent rights required)…")
    result = msal_app.acquire_token_interactive(scopes=_LOGIN_SCOPES)
    if "access_token" not in result:
        raise RuntimeError(result.get("error_description", "login failed"))
    token = result["access_token"]
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, token


# ── Graph helpers ────────────────────────────────────────────────────────────


def _ensure_sp(client: Client, headers: dict, app_id: str) -> dict:
    """Get (or create) the service principal for a resource app id."""
    r = client.get(
        f"{_GRAPH_BASE}/servicePrincipals",
        headers=headers,
        params={"$filter": f"appId eq '{app_id}'"},
    )
    r.raise_for_status()
    sps = r.json().get("value", [])
    if sps:
        return sps[0]
    logger.info("Creating service principal for {}", app_id)
    r = client.post(f"{_GRAPH_BASE}/servicePrincipals", headers=headers, json={"appId": app_id})
    if r.status_code >= 400:
        raise RuntimeError(f"Failed to create SP for {app_id}: {r.status_code} {r.text}")
    return r.json()


def _scope_ids(sp: dict, wanted: list[str]) -> list[dict]:
    """Resolve delegated scope value -> {id, value} from an SP's oauth2PermissionScopes."""
    by_value = {s.get("value"): s for s in sp.get("oauth2PermissionScopes", [])}
    found = []
    for value in wanted:
        s = by_value.get(value)
        if s:
            found.append({"id": s["id"], "value": s["value"]})
        else:
            logger.warning("Scope {!r} not found on SP {}", value, sp.get("appId"))
    if not found:
        raise RuntimeError(f"None of {wanted} found on SP {sp.get('appId')}")
    return found


def _resource_block(app_id: str, scopes: list[dict]) -> dict:
    return {
        "resourceAppId": app_id,
        "resourceAccess": [{"id": s["id"], "type": "Scope"} for s in scopes],
    }


def _grant_consent(
    client: Client, headers: dict, our_sp_id: str, resource_sp_id: str, scopes: list[dict]
) -> None:
    body = {
        "clientId": our_sp_id,
        "consentType": "AllPrincipals",
        "resourceId": resource_sp_id,
        "scope": " ".join(s["value"] for s in scopes),
    }
    r = client.post(f"{_GRAPH_BASE}/oauth2PermissionGrants", headers=headers, json=body)
    if r.status_code >= 400:
        logger.warning("Admin consent grant failed ({}): {}", r.status_code, r.text)
    else:
        logger.info("Admin consent granted for resource {}", resource_sp_id)


def _our_sp(client: Client, headers: dict, client_id: str) -> str:
    """Object id of our app's service principal (create if missing)."""
    r = client.get(
        f"{_GRAPH_BASE}/servicePrincipals",
        headers=headers,
        params={"$filter": f"appId eq '{client_id}'"},
    )
    r.raise_for_status()
    sps = r.json().get("value", [])
    if sps:
        return sps[0]["id"]
    r = client.post(f"{_GRAPH_BASE}/servicePrincipals", headers=headers, json={"appId": client_id})
    if r.status_code >= 400:
        raise RuntimeError(f"Failed to create our SP: {r.status_code} {r.text}")
    return r.json()["id"]


def _update_env(updates: dict[str, str]) -> None:
    from pathlib import Path

    path = Path(ENV_FILE)
    lines = path.read_text().splitlines() if path.exists() else []
    keys = set(updates)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in keys:
            out.append(f"{key}={updates[key]}")
            keys.discard(key)
        else:
            out.append(line)
    for key in keys:
        out.append(f"{key}={updates[key]}")
    path.write_text("\n".join(out) + "\n")
    logger.info("Wrote {} to {}", list(updates), ENV_FILE)


# ── Core (importable) ────────────────────────────────────────────────────────


def provision_app(name: str = "teams-eval") -> dict:
    """Create a new public-client app with Graph + Power Platform delegated scopes + consent.

    Returns ``{"tenant_id", "client_id", "scopes"}``. Logs progress via loguru so callers
    (CLI or UI) can stream it.
    """
    headers, token = _interactive_login()
    tenant_id = jwt.decode(token, options={"verify_signature": False}).get("tid", "")

    with Client(timeout=30) as client:
        graph_sp = _ensure_sp(client, headers, _GRAPH_APP_ID)
        pp_sp = _ensure_sp(client, headers, _POWER_PLATFORM_APP_ID)
        graph_scopes = _scope_ids(graph_sp, _GRAPH_SCOPES)
        pp_scopes = _scope_ids(pp_sp, _PP_SCOPES)

        app_body = {
            "displayName": name,
            "signInAudience": "AzureADMyOrg",
            "publicClient": {"redirectUris": ["http://localhost"]},
            "requiredResourceAccess": [
                _resource_block(_GRAPH_APP_ID, graph_scopes),
                _resource_block(_POWER_PLATFORM_APP_ID, pp_scopes),
            ],
        }
        r = client.post(f"{_GRAPH_BASE}/applications", headers=headers, json=app_body)
        if r.status_code >= 400:
            raise RuntimeError(f"Create app failed: {r.status_code} {r.text}")
        client_id = r.json()["appId"]
        logger.info("Created app registration {} ({})", name, client_id)

        our_sp_id = _our_sp(client, headers, client_id)
        _grant_consent(client, headers, our_sp_id, graph_sp["id"], graph_scopes)
        _grant_consent(client, headers, our_sp_id, pp_sp["id"], pp_scopes)

    _update_env({"AZURE_AD_TENANT_ID": tenant_id, "AZURE_AD_CLIENT_ID": client_id})
    logger.info("Done. tenant={} client_id={}", tenant_id, client_id)
    return {
        "tenant_id": tenant_id,
        "client_id": client_id,
        "scopes": {"graph": list(_GRAPH_SCOPES), "power_platform": list(_PP_SCOPES)},
    }


def add_graph_scopes_to(client_id: str) -> dict:
    """Add Microsoft Graph (Teams) delegated scopes + consent to an EXISTING app registration.

    Also records ``AZURE_AD_CLIENT_ID`` + ``AZURE_AD_TENANT_ID`` in ``.env`` so the tool can
    sign in with this app right away.
    """
    headers, token = _interactive_login()
    tenant_id = jwt.decode(token, options={"verify_signature": False}).get("tid", "")

    with Client(timeout=30) as client:
        r = client.get(
            f"{_GRAPH_BASE}/applications",
            headers=headers,
            params={"$filter": f"appId eq '{client_id}'"},
        )
        r.raise_for_status()
        apps = r.json().get("value", [])
        if not apps:
            raise RuntimeError(f"No app registration with appId {client_id}")
        app_obj = apps[0]
        object_id = app_obj["id"]

        graph_sp = _ensure_sp(client, headers, _GRAPH_APP_ID)
        graph_scopes = _scope_ids(graph_sp, _GRAPH_SCOPES)

        # Merge: keep all non-Graph resource blocks, replace the Graph one.
        existing = app_obj.get("requiredResourceAccess", [])
        merged = [b for b in existing if b.get("resourceAppId") != _GRAPH_APP_ID]
        merged.append(_resource_block(_GRAPH_APP_ID, graph_scopes))

        r = client.patch(
            f"{_GRAPH_BASE}/applications/{object_id}",
            headers=headers,
            json={"requiredResourceAccess": merged},
        )
        if r.status_code >= 400:
            raise RuntimeError(f"PATCH app failed: {r.status_code} {r.text}")
        logger.info("Added Graph scopes to app {}", client_id)

        our_sp_id = _our_sp(client, headers, client_id)
        _grant_consent(client, headers, our_sp_id, graph_sp["id"], graph_scopes)

    _update_env({"AZURE_AD_TENANT_ID": tenant_id, "AZURE_AD_CLIENT_ID": client_id})
    return {
        "client_id": client_id,
        "tenant_id": tenant_id,
        "scopes": {"graph": list(_GRAPH_SCOPES)},
    }


# ── Commands ─────────────────────────────────────────────────────────────────


@app.command()
def create(name: str = typer.Option("teams-eval", help="Display name for the app registration.")):
    """Create a new public-client app with Graph + Power Platform delegated scopes + consent."""
    res = provision_app(name)
    typer.secho(
        f"Done. tenant={res['tenant_id']} client_id={res['client_id']}\n"
        "Scopes: Graph(Chat.ReadWrite, ChatMessage.Send) + "
        "Power Platform(CopilotStudio.Copilots.Invoke, user_impersonation).",
        fg=typer.colors.GREEN,
    )


@app.command(name="add-graph-scopes")
def add_graph_scopes(
    client_id: str = typer.Option(..., help="appId of the existing app registration to extend."),
):
    """Add Microsoft Graph (Teams) delegated scopes + consent to an EXISTING app registration."""
    res = add_graph_scopes_to(client_id)
    typer.secho(
        f"Done. Added Graph(Chat.ReadWrite, ChatMessage.Send) to {res['client_id']}.\n"
        f"Wrote AZURE_AD_CLIENT_ID + AZURE_AD_TENANT_ID ({res['tenant_id']}) to .env.",
        fg=typer.colors.GREEN,
    )


if __name__ == "__main__":
    app()
