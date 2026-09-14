"""Direct-to-Engine channel client — the stand-in for the Copilot Studio test pane.

Wraps microsoft-agents-copilotstudio-client. Each DirectClient owns one server-side conversation;
call start() once, then ask() per turn. A turn's bot messages are concatenated into one response.
"""

import asyncio
import os
import re
from urllib.parse import quote, urlparse, urlunparse

import httpx
from dotenv import load_dotenv
from loguru import logger
from microsoft_agents.activity import Activity, ActivityTypes, ConversationAccount
from microsoft_agents.copilotstudio.client import ConnectionSettings, CopilotClient
from microsoft_agents.copilotstudio.client.execute_turn_request import ExecuteTurnRequest
from microsoft_agents.copilotstudio.client.power_platform_cloud import PowerPlatformCloud
from microsoft_agents.copilotstudio.client.power_platform_environment import (
    PowerPlatformEnvironment,
)

from auth import get_token
from compare import ChannelResponse

load_dotenv()

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_AGENTIC_RUNTIME_PATH = (
    "/copilotstudio/agenticruntime/3p/dataverse-backed/authenticated/bots"
)
_AGENTIC_API_VERSION = "1"


def agentic_runtime_url(environment_id: str, agent_identifier: str) -> str:
    host = PowerPlatformEnvironment.get_environment_endpoint(
        PowerPlatformCloud.PROD,
        environment_id,
    )
    path = f"{_AGENTIC_RUNTIME_PATH}/{quote(agent_identifier, safe='')}"
    return urlunparse(("https", host, path, "", f"api-version={_AGENTIC_API_VERSION}", ""))


def _conversation_url(base_url: str, conversation_id: str | None = None) -> str:
    parsed = urlparse(base_url)
    path = parsed.path.rstrip("/") + "/conversations"
    if conversation_id:
        path += f"/{quote(conversation_id, safe='')}"
    return urlunparse(parsed._replace(path=path))


class DirectClient:
    """Stateful Direct-to-Engine conversation for one agent."""

    def __init__(self, environment_id: str, agent_identifier: str) -> None:
        if _UUID_RE.match(agent_identifier):
            logger.warning(
                "agent_identifier looks like a GUID — D2E needs the agent SCHEMA name "
                "(e.g. cr123_myagent). Direct calls may fail."
            )
        self.settings = ConnectionSettings(
            environment_id=environment_id, agent_identifier=agent_identifier
        )
        self._base_url = agentic_runtime_url(environment_id, agent_identifier)
        self._client: CopilotClient | None = None
        self._conversation_id: str | None = None
        self._token: str | None = None

    async def start(self) -> None:
        """Acquire a token and open a fresh conversation."""
        token = await asyncio.to_thread(get_token, "powerplatform", False)
        self._token = token
        self._client = CopilotClient(self.settings, token)
        url = _conversation_url(self._base_url)
        logger.debug("Direct endpoint: {}", url)

        try:
            async for activity in self._client.post_request(
                url,
                {"emitStartConversationEvent": True},
                {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": CopilotClient.APPLICATION_JSON_TYPE,
                    "Accept": CopilotClient.EVENT_STREAM_TYPE,
                },
            ):
                conv = getattr(activity, "conversation", None)
                if conv and getattr(conv, "id", None):
                    self._conversation_id = conv.id
        except Exception as e:
            body = await self._fetch_error_body(url, token)
            raise RuntimeError(f"Direct start_conversation failed: {e}. {body}") from e

        # Many agents return the conversation id only in the x-ms-conversationid response header
        # (the SDK stores it as _current_conversation_id) and never attach a conversation object to
        # an activity — the opening event is just a "typing" indicator. Fall back to that.
        if not self._conversation_id:
            self._conversation_id = getattr(self._client, "_current_conversation_id", "") or None

        if not self._conversation_id:
            raise RuntimeError("Direct start_conversation returned no conversation id")
        logger.debug("Direct conversation started: {}", self._conversation_id)

    async def ask(self, text: str) -> ChannelResponse:
        """Send one user message; collect the whole turn into a ChannelResponse."""
        if not self._client or not self._conversation_id or not self._token:
            raise RuntimeError("DirectClient.start() must be called before ask()")

        messages: list[str] = []
        citations: list[str] = []
        cards: list[dict] = []
        suggested: list[str] = []
        activities: list[dict] = []

        activity = Activity(
            type=ActivityTypes.message,
            text=text,
            conversation=ConversationAccount(id=self._conversation_id),
        )
        data = ExecuteTurnRequest(activity=activity).model_dump(
            mode="json", by_alias=True, exclude_unset=True
        )
        url = _conversation_url(self._base_url, self._conversation_id)
        async for reply in self._client.post_request(
            url,
            data,
            {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": CopilotClient.APPLICATION_JSON_TYPE,
                "Accept": CopilotClient.EVENT_STREAM_TYPE,
            },
        ):
            activities.append(
                {
                    "type": str(reply.type),
                    "text": getattr(reply, "text", None),
                    "name": getattr(reply, "name", None),
                }
            )

            if reply.type == ActivityTypes.message and getattr(reply, "text", None):
                messages.append(reply.text)

            for ent in getattr(reply, "entities", None) or []:
                data = ent.model_dump() if hasattr(ent, "model_dump") else dict(ent)
                url = data.get("url") or data.get("@id") or data.get("name")
                if url:
                    citations.append(str(url))

            for att in getattr(reply, "attachments", None) or []:
                content = getattr(att, "content", None)
                if isinstance(content, dict):
                    cards.append(content)

            sa = getattr(reply, "suggested_actions", None)
            if sa and getattr(sa, "actions", None):
                suggested.extend(a.title for a in sa.actions if getattr(a, "title", None))

            if reply.type == ActivityTypes.end_of_conversation:
                break

        text_joined = "\n".join(messages)
        return ChannelResponse(
            channel="direct",
            text=text_joined,
            messages=messages,
            raw=text_joined,
            is_html=False,
            citations=citations,
            cards=cards,
            suggested=suggested,
            activities=activities,
        )

    @staticmethod
    async def _fetch_error_body(url: str, token: str) -> str:
        """Re-issue the start request with httpx to surface the error body the SDK hides."""
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(
                    url,
                    json={"emitStartConversationEvent": True},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream",
                    },
                )
                return f"HTTP {r.status_code}: {r.text[:500]}"
        except Exception as e:  # pragma: no cover - diagnostic only
            return f"(could not fetch error body: {e})"


async def ask_once(environment_id: str, agent_identifier: str, text: str) -> ChannelResponse:
    """Convenience: fresh conversation, single turn."""
    client = DirectClient(environment_id, agent_identifier)
    await client.start()
    return await client.ask(text)


if __name__ == "__main__":
    import sys

    env_id = os.environ["COPILOT_ENVIRONMENT_ID"]
    schema = os.environ["COPILOT_AGENT_SCHEMA"]
    msg = sys.argv[1] if len(sys.argv) > 1 else "Hello"
    resp = asyncio.run(ask_once(env_id, schema, msg))
    print(resp.text)
