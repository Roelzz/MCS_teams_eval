"""Teams channel client via Microsoft Graph.

Drives a published Copilot Studio agent through its 1:1 Teams chat using only Graph calls:
POST a message, then poll for the bot's reply. A turn = the prompt plus every bot message it
produces (collected until a quiet window elapses), concatenated into one response.

Notes / caveats (see SETUP.md):
- Delegated token only; Graph throttles chat endpoints (TEAMS_RATE_DELAY_S between calls).
- Bot responses are HTML.
- A 1:1 thread can't truly reset — optional /debug reset commands are best-effort.
"""

import asyncio
import json
import os
import urllib.parse
from datetime import datetime

import httpx
from dotenv import load_dotenv
from loguru import logger

from auth import get_token
from compare import ChannelResponse, extract_html_links, html_to_text

load_dotenv()

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def parse_chat_id(link_or_id: str) -> str:
    """Extract the chat ID (19:...@unq.gbl.spaces) from a Teams 'Copy link' URL or raw ID."""
    s = link_or_id.strip()
    if "/l/chat/" in s:
        s = s.split("/l/chat/", 1)[1].split("/conversations", 1)[0].split("?", 1)[0]
        s = urllib.parse.unquote(s)
    if not s.startswith("19:"):
        raise ValueError(
            f"Could not parse a Teams chat ID from {link_or_id!r} "
            "(expected something like 19:...@unq.gbl.spaces)"
        )
    return s


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class TeamsClient:
    """Stateful Teams chat driver for one agent (one chat ID)."""

    def __init__(self, chat_id: str) -> None:
        self.chat_id = chat_id
        self.poll_interval = float(os.getenv("TEAMS_POLL_INTERVAL_S", "1.5"))
        self.poll_timeout = float(os.getenv("TEAMS_POLL_TIMEOUT_S", "60"))
        self.quiet_window = float(os.getenv("TEAMS_QUIET_WINDOW_S", "4"))
        self.rate_delay = float(os.getenv("TEAMS_RATE_DELAY_S", "1.0"))
        self.turn_wait = float(os.getenv("TEAMS_TURN_WAIT_S", "10"))

    @classmethod
    def from_config(cls, teams_cfg: dict) -> "TeamsClient":
        if teams_cfg.get("chat_id"):
            return cls(parse_chat_id(teams_cfg["chat_id"]))
        if teams_cfg.get("chat_link"):
            return cls(parse_chat_id(teams_cfg["chat_link"]))
        raise ValueError("Teams config needs 'chat_id' or 'chat_link'")

    async def _headers(self) -> dict:
        token = await asyncio.to_thread(get_token, "graph", False)
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async def _post_message(self, client: httpx.AsyncClient, text: str) -> dict:
        r = await client.post(
            f"{_GRAPH_BASE}/chats/{self.chat_id}/messages",
            headers=await self._headers(),
            json={"body": {"content": text}},
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Graph POST message failed: {r.status_code} {r.text[:500]}")
        await asyncio.sleep(self.rate_delay)
        return r.json()

    async def _get_messages(self, client: httpx.AsyncClient, top: int = 20) -> list[dict]:
        r = await client.get(
            f"{_GRAPH_BASE}/chats/{self.chat_id}/messages",
            headers=await self._headers(),
            params={"$top": top},
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Graph GET messages failed: {r.status_code} {r.text[:500]}")
        await asyncio.sleep(self.rate_delay)
        return r.json().get("value", [])

    @staticmethod
    def _is_bot(msg: dict) -> bool:
        frm = msg.get("from") or {}
        return frm.get("application") is not None

    async def reset(self, commands: list[str]) -> None:
        """Best-effort context reset: send each command and let it settle."""
        if not commands:
            return
        async with httpx.AsyncClient(timeout=30) as client:
            for cmd in commands:
                logger.debug("Teams reset command: {}", cmd)
                await self._post_message(client, cmd)
                await asyncio.sleep(self.quiet_window)

    async def ask(self, text: str) -> ChannelResponse:
        """Send one prompt; collect all bot messages of the turn into one ChannelResponse."""
        async with httpx.AsyncClient(timeout=60) as client:
            sent = await self._post_message(client, text)
            boundary = _parse_dt(sent["createdDateTime"])
            sent_id = sent.get("id")

            # Give the agent time to answer before polling — never retry before this wait. With the
            # sequential eval (one prompt in flight at a time) this paces the shared Teams chat so
            # prompts don't pile up on one another.
            if self.turn_wait > 0:
                await asyncio.sleep(self.turn_wait)

            collected: dict[str, dict] = {}
            loop = asyncio.get_event_loop()
            start = loop.time()
            last_new = loop.time()

            while loop.time() - start < self.poll_timeout:
                await asyncio.sleep(self.poll_interval)
                msgs = await self._get_messages(client)
                new_found = False
                for m in msgs:
                    if m.get("id") == sent_id or not self._is_bot(m):
                        continue
                    created = m.get("createdDateTime")
                    if not created or _parse_dt(created) <= boundary:
                        continue
                    if m["id"] not in collected:
                        collected[m["id"]] = m
                        new_found = True
                if new_found:
                    last_new = loop.time()
                elif collected and (loop.time() - last_new) >= self.quiet_window:
                    break

            return self._build_response(collected)

    def _build_response(self, collected: dict[str, dict]) -> ChannelResponse:
        ordered = sorted(collected.values(), key=lambda m: m.get("createdDateTime", ""))
        if not ordered:
            return ChannelResponse(
                channel="teams", error="No bot response within timeout", is_html=True
            )

        html_parts: list[str] = []
        text_parts: list[str] = []
        citations: list[str] = []
        cards: list[dict] = []
        suggested: list[str] = []

        for m in ordered:
            content = (m.get("body") or {}).get("content", "") or ""
            html_parts.append(content)
            text_parts.append(html_to_text(content))
            citations.extend(extract_html_links(content))
            for att in m.get("attachments") or []:
                card = self._parse_attachment(att)
                if card is not None:
                    cards.append(card)
                    suggested.extend(self._card_action_titles(card))

        return ChannelResponse(
            channel="teams",
            text="\n".join(p for p in text_parts if p),
            messages=text_parts,
            raw="\n".join(html_parts),
            is_html=True,
            citations=citations,
            cards=cards,
            suggested=suggested,
            activities=ordered,
        )

    @staticmethod
    def _parse_attachment(att: dict) -> dict | None:
        content = att.get("content")
        if isinstance(content, str):
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return None
        return content if isinstance(content, dict) else None

    @staticmethod
    def _card_action_titles(card: dict) -> list[str]:
        actions = card.get("actions") or []
        return [a.get("title") for a in actions if isinstance(a, dict) and a.get("title")]


async def ask_once(chat_link_or_id: str, text: str) -> ChannelResponse:
    """Convenience: single turn against a chat."""
    return await TeamsClient(parse_chat_id(chat_link_or_id)).ask(text)


if __name__ == "__main__":
    import sys

    link = os.environ["TEAMS_CHAT_LINK"]
    msg = sys.argv[1] if len(sys.argv) > 1 else "Hello"
    resp = asyncio.run(ask_once(link, msg))
    print(resp.text or resp.error)
