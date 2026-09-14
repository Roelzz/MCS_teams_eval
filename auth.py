"""MSAL token provider — interactive delegated login, cached, two audiences.

One browser sign-in serves both channels:
- Microsoft Graph  → Teams channel (Chat.ReadWrite, ChatMessage.Send)
- Power Platform    → Direct-to-Engine channel

After the first interactive login the account is cached, so tokens for the second
audience are acquired silently (admin consent must already be granted — see SETUP.md).
"""

import json
import os
import threading
from typing import Literal

import msal
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

TOKEN_CACHE_FILE = ".local_token_cache.json"

Audience = Literal["graph", "powerplatform"]

# Scope sets per audience. Graph uses named delegated scopes; Power Platform uses
# the resource .default (these two cannot be combined in a single token request).
_AUDIENCE_SCOPES: dict[Audience, list[str]] = {
    "graph": ["Chat.ReadWrite", "ChatMessage.Send"],
    "powerplatform": ["https://api.powerplatform.com/.default"],
}

_app: msal.PublicClientApplication | None = None
# The MSAL app and its file-backed cache are shared across threads: an eval runs samples
# concurrently and each calls get_token via asyncio.to_thread. Without serialization, one
# thread reads .local_token_cache.json while another is mid-write, yielding a corrupt
# "Extra data" JSON parse. This lock makes the read → acquire → save sequence atomic.
_lock = threading.RLock()


class LocalTokenCache(msal.SerializableTokenCache):
    """File-backed MSAL token cache."""

    def __init__(self, cache_file: str = TOKEN_CACHE_FILE) -> None:
        super().__init__()
        self.cache_file = cache_file
        if os.path.exists(self.cache_file):
            self._load()

    def _load(self) -> None:
        raw = open(self.cache_file).read()
        try:
            self.deserialize(raw)
            return
        except ValueError as e:
            # A prior concurrent non-atomic write can leave a doubled/garbage file
            # ("Extra data: …"). Recover the first valid JSON object so cached tokens survive,
            # then repair the file on disk so future reads are clean.
            logger.warning("Token cache corrupt ({}); recovering first valid object", e)
        try:
            obj, _ = json.JSONDecoder().raw_decode(raw.lstrip())
            self.deserialize(json.dumps(obj))
        except Exception as e2:  # noqa: BLE001
            logger.warning("Token cache unrecoverable ({}); starting empty — sign in again", e2)
            return
        self._atomic_write(self.serialize())
        logger.info("Token cache repaired")

    def _atomic_write(self, data: str) -> None:
        tmp = f"{self.cache_file}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            f.write(data)
        os.replace(tmp, self.cache_file)

    def save(self) -> None:
        if self.has_state_changed:
            # Write atomically (temp file + os.replace) so a concurrent reader never sees a
            # half-written file.
            self._atomic_write(self.serialize())


def _get_app() -> tuple[msal.PublicClientApplication, LocalTokenCache]:
    global _app
    cache = LocalTokenCache()
    if _app is None:
        tenant_id = os.environ["AZURE_AD_TENANT_ID"]
        client_id = os.environ["AZURE_AD_CLIENT_ID"]
        _app = msal.PublicClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            token_cache=cache,
        )
    else:
        _app.token_cache = cache
    return _app, cache


def get_token(audience: Audience, interactive: bool = True) -> str:
    """Acquire a delegated access token for the given audience.

    Tries silent acquisition first. Falls back to an interactive browser login only when
    ``interactive`` is True (the Setup → Sign in flow). Eval channel clients call with
    ``interactive=False`` so a non-interactive run fails fast with a clear error instead of
    blocking on a browser prompt mid-eval.

    Raises:
        RuntimeError: if token acquisition fails, or no cached token exists and
            ``interactive`` is False.
    """
    scopes = _AUDIENCE_SCOPES[audience]

    # Serialize the whole read→acquire→save sequence: the file cache and shared MSAL app are
    # touched concurrently by samples running in parallel (asyncio.to_thread workers).
    with _lock:
        app, cache = _get_app()
        accounts = app.get_accounts()
        result = None
        if accounts:
            logger.debug("Silent token acquisition for audience={}", audience)
            result = app.acquire_token_silent(scopes, account=accounts[0])

        if not result or "access_token" not in result:
            if not interactive:
                raise RuntimeError(
                    f"Not signed in for audience={audience}. Open Setup → Sign in first "
                    "(it primes both Graph and Power Platform), then start the run."
                )
            logger.info("Interactive login required for audience={}", audience)
            result = app.acquire_token_interactive(
                scopes=scopes,
                prompt="select_account" if not accounts else None,
            )

        cache.save()

    if "access_token" not in result:
        err = result.get("error_description", result.get("error", "Unknown error"))
        raise RuntimeError(f"Token acquisition failed for audience={audience}: {err}")

    token = result["access_token"]
    logger.debug("Token acquired for audience={} (len={})", audience, len(token))
    return token


def missing_credentials() -> list[str]:
    """The AZURE_AD_* env vars required for sign-in that are currently absent/empty.

    Reflects the live process environment (updated by ``reload_config`` after a .env write),
    so the UI can guide the user to provision an app registration before signing in.
    """
    return [
        key
        for key in ("AZURE_AD_TENANT_ID", "AZURE_AD_CLIENT_ID")
        if not os.environ.get(key, "").strip()
    ]


_NO_APP_HINT = (
    "No app registration configured yet (missing {missing}). Create or extend one in the "
    "'App registration' step first, then sign in."
)


def test_connection() -> dict:
    """Smoke-test both audiences. Returns {success, message}."""
    missing = missing_credentials()
    if missing:
        return {"success": False, "message": _NO_APP_HINT.format(missing=", ".join(missing))}
    try:
        graph = get_token("graph")
        pp = get_token("powerplatform")
        return {
            "success": True,
            "message": f"Graph token len={len(graph)}, Power Platform token len={len(pp)}",
        }
    except KeyError as e:
        return {"success": False, "message": _NO_APP_HINT.format(missing=str(e))}
    except Exception as e:
        return {"success": False, "message": str(e)}


def reload_config() -> None:
    """Re-read .env (e.g. after provisioning) and drop the cached MSAL app."""
    global _app
    load_dotenv(override=True)
    _app = None


def current_account() -> str | None:
    """Username of the cached signed-in account, or None (no interactive prompt)."""
    try:
        app, _ = _get_app()
    except KeyError:
        return None
    accounts = app.get_accounts()
    return accounts[0]["username"] if accounts else None


def sign_in() -> dict:
    """Interactive browser sign-in. Primes BOTH audiences (Graph + Power Platform) so a
    subsequent eval acquires tokens silently and never blocks on a browser prompt mid-run.
    Returns {success, account, message}."""
    missing = missing_credentials()
    if missing:
        return {
            "success": False,
            "account": "",
            "message": _NO_APP_HINT.format(missing=", ".join(missing)),
        }
    try:
        get_token("graph")
        get_token("powerplatform")
    except KeyError as e:
        return {"success": False, "account": "", "message": _NO_APP_HINT.format(missing=str(e))}
    except Exception as e:
        return {"success": False, "account": "", "message": str(e)}
    acct = current_account() or ""
    return {
        "success": True,
        "account": acct,
        "message": f"Signed in as {acct} — Graph + Power Platform ready",
    }


if __name__ == "__main__":
    result = test_connection()
    print(result["message"])
    raise SystemExit(0 if result["success"] else 1)
