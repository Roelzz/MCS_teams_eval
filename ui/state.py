"""Shared app state — auth status (sidebar badge) + interactive sign-in."""

from __future__ import annotations

import reflex as rx

import auth
from ui.services import logstream


class AuthState(rx.State):
    signed_in: bool = False
    account: str = ""
    auth_busy: bool = False
    auth_msg: str = ""

    @rx.var
    def status_label(self) -> str:
        if self.auth_busy:
            return "Signing in…"
        return f"Signed in · {self.account}" if self.signed_in else "Not signed in"

    @rx.event
    def refresh_auth(self):
        acct = auth.current_account()
        self.signed_in = bool(acct)
        self.account = acct or ""

    @rx.event(background=True)
    async def sign_in(self):
        async with self:
            self.auth_busy = True
            self.auth_msg = "Opening browser for sign-in…"
        ok, result, err = await logstream.run_threaded(auth.sign_in)
        async with self:
            self.auth_busy = False
            if ok and result.get("success"):
                self.signed_in = True
                self.account = result["account"]
                self.auth_msg = result["message"]
            else:
                self.signed_in = False
                self.auth_msg = (result or {}).get("message", "") if ok else str(err)
