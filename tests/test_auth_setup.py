"""Tests for the credential-guard added to sign-in (no network: missing creds returns early)."""

from __future__ import annotations

import pytest

import auth


class _FakeApp:
    def __init__(self, accounts):
        self._accounts = accounts

    def get_accounts(self):
        return self._accounts

    def acquire_token_silent(self, scopes, account=None):
        return None


class _FakeCache:
    def save(self):
        pass


def _clear(monkeypatch):
    monkeypatch.delenv("AZURE_AD_TENANT_ID", raising=False)
    monkeypatch.delenv("AZURE_AD_CLIENT_ID", raising=False)


def test_missing_credentials_lists_both(monkeypatch):
    _clear(monkeypatch)
    assert auth.missing_credentials() == ["AZURE_AD_TENANT_ID", "AZURE_AD_CLIENT_ID"]


def test_missing_credentials_partial(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("AZURE_AD_TENANT_ID", "contoso.onmicrosoft.com")
    assert auth.missing_credentials() == ["AZURE_AD_CLIENT_ID"]


def test_blank_is_missing(monkeypatch):
    monkeypatch.setenv("AZURE_AD_TENANT_ID", "   ")
    monkeypatch.setenv("AZURE_AD_CLIENT_ID", "")
    assert auth.missing_credentials() == ["AZURE_AD_TENANT_ID", "AZURE_AD_CLIENT_ID"]


def test_sign_in_blocked_without_creds(monkeypatch):
    _clear(monkeypatch)
    res = auth.sign_in()
    assert res["success"] is False
    assert "App registration" in res["message"]
    assert "AZURE_AD_TENANT_ID" in res["message"]


def test_test_connection_blocked_without_creds(monkeypatch):
    _clear(monkeypatch)
    res = auth.test_connection()
    assert res["success"] is False
    assert "App registration" in res["message"]


def test_get_token_silent_only_raises_when_not_signed_in(monkeypatch):
    """Eval channel clients call with interactive=False — no cached token must fail fast,
    not open a browser (which would hang a non-interactive run)."""
    monkeypatch.setattr(auth, "_get_app", lambda: (_FakeApp([]), _FakeCache()))
    with pytest.raises(RuntimeError) as ei:
        auth.get_token("powerplatform", interactive=False)
    assert "Sign in" in str(ei.value)


def test_sign_in_primes_both_audiences(monkeypatch):
    """Sign in must prime Graph and Power Platform so a later eval is fully silent."""
    monkeypatch.setenv("AZURE_AD_TENANT_ID", "contoso.onmicrosoft.com")
    monkeypatch.setenv("AZURE_AD_CLIENT_ID", "client-123")
    calls: list[str] = []
    monkeypatch.setattr(auth, "get_token", lambda aud, *a, **k: calls.append(aud) or "tok")
    monkeypatch.setattr(auth, "current_account", lambda: "roel@example.com")
    res = auth.sign_in()
    assert res["success"] is True
    assert calls == ["graph", "powerplatform"]
    assert "roel@example.com" in res["message"]


# --- Corrupt token-cache recovery (regression: concurrent non-atomic writes doubled the JSON) ---

_VALID_CACHE = (
    '{"AccessToken": {"k": {"secret": "tok", "realm": "contoso"}}, '
    '"RefreshToken": {}, "Account": {}}'
)


def test_corrupt_cache_recovers_and_repairs(tmp_path):
    """A doubled-JSON cache ('Extra data') must load the first object and repair the file."""
    import json

    f = tmp_path / "cache.json"
    f.write_text(_VALID_CACHE + _VALID_CACHE)  # the exact corruption shape we saw on disk

    cache = auth.LocalTokenCache(str(f))

    # Token survived recovery.
    assert json.loads(cache.serialize())["AccessToken"]["k"]["secret"] == "tok"
    # File on disk is repaired to a single valid JSON object.
    repaired = f.read_text()
    assert json.loads(repaired)["AccessToken"]["k"]["secret"] == "tok"


def test_unrecoverable_cache_starts_empty(tmp_path):
    """Pure garbage must not raise — start empty so the user can sign in again."""
    f = tmp_path / "cache.json"
    f.write_text("}{ not json at all")
    cache = auth.LocalTokenCache(str(f))  # must not raise
    assert "tok" not in cache.serialize()


def test_atomic_save_writes_valid_json(tmp_path):
    import json

    f = tmp_path / "cache.json"
    cache = auth.LocalTokenCache(str(f))
    cache.deserialize(_VALID_CACHE)
    cache._atomic_write(cache.serialize())
    assert json.loads(f.read_text())["AccessToken"]["k"]["secret"] == "tok"
