"""Unit tests for DirectClient agentic-runtime routing and conversation handling."""

import pytest
from microsoft_agents.activity import ActivityTypes

import direct_client
from direct_client import DirectClient, agentic_runtime_url

ENVIRONMENT_ID = "2dd2ec79-3f5b-e241-b733-f7e34196b913"
SCHEMA_NAME = "rrs_d2e_5Oj_0g"
BASE_URL = (
    "https://2dd2ec793f5be241b733f7e34196b9.13.environment.api.powerplatform.com"
    "/copilotstudio/agenticruntime/3p/dataverse-backed/authenticated/bots/rrs_d2e_5Oj_0g"
)
START_URL = f"{BASE_URL}/conversations?api-version=1"


def test_agentic_runtime_url_matches_working_direct_connect_endpoint():
    assert agentic_runtime_url(ENVIRONMENT_ID, SCHEMA_NAME) == f"{BASE_URL}?api-version=1"


class _TypingActivity:
    def __init__(self) -> None:
        self.type = "typing"
        self.conversation = None  # no conversation object — like the real "typing-1" event


class _ConvActivity:
    class _Conv:
        id = "conv-from-activity"

    type = "message"
    conversation = _Conv()


class _MessageActivity:
    type = ActivityTypes.message
    text = "agent reply"
    name = None
    entities = []
    attachments = []
    suggested_actions = None


class _FakeHeaderOnlyClient:
    APPLICATION_JSON_TYPE = "application/json"
    EVENT_STREAM_TYPE = "text/event-stream"

    def __init__(self, settings, token) -> None:
        self.settings = settings
        self._token = token
        self._current_conversation_id = ""
        self.calls: list[tuple[str, dict, dict]] = []

    async def post_request(self, url: str, data: dict, headers: dict):
        self.calls.append((url, data, headers))
        self._current_conversation_id = "conv-from-header"
        yield _TypingActivity()


class _FakeActivityClient(_FakeHeaderOnlyClient):
    async def post_request(self, url: str, data: dict, headers: dict):
        self.calls.append((url, data, headers))
        yield _ConvActivity()


class _FakeConversationClient(_FakeHeaderOnlyClient):
    async def post_request(self, url: str, data: dict, headers: dict):
        self.calls.append((url, data, headers))
        if url == START_URL:
            self._current_conversation_id = "conv/from-header"
            yield _TypingActivity()
        else:
            yield _MessageActivity()


class _FakeNoIdClient(_FakeHeaderOnlyClient):
    async def post_request(self, url: str, data: dict, headers: dict):
        self.calls.append((url, data, headers))
        return
        yield  # pragma: no cover - makes this an (empty) async generator


@pytest.mark.asyncio
async def test_start_uses_agentic_runtime_and_header_conversation_id(monkeypatch):
    monkeypatch.setattr(direct_client, "get_token", lambda *a, **k: "tok")
    monkeypatch.setattr(direct_client, "CopilotClient", _FakeHeaderOnlyClient)
    d = DirectClient(ENVIRONMENT_ID, SCHEMA_NAME)

    await d.start()

    assert d._conversation_id == "conv-from-header"
    assert d._client.calls == [
        (
            START_URL,
            {"emitStartConversationEvent": True},
            {
                "Authorization": "Bearer tok",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
        )
    ]


@pytest.mark.asyncio
async def test_start_prefers_activity_conversation_id(monkeypatch):
    monkeypatch.setattr(direct_client, "get_token", lambda *a, **k: "tok")
    monkeypatch.setattr(direct_client, "CopilotClient", _FakeActivityClient)
    d = DirectClient(ENVIRONMENT_ID, SCHEMA_NAME)

    await d.start()

    assert d._conversation_id == "conv-from-activity"


@pytest.mark.asyncio
async def test_ask_uses_agentic_runtime_conversation_url_and_execute_turn_payload(monkeypatch):
    monkeypatch.setattr(direct_client, "get_token", lambda *a, **k: "tok")
    monkeypatch.setattr(direct_client, "CopilotClient", _FakeConversationClient)
    d = DirectClient(ENVIRONMENT_ID, SCHEMA_NAME)
    await d.start()

    response = await d.ask("Hello")

    assert response.text == "agent reply"
    assert d._client.calls[1] == (
        f"{BASE_URL}/conversations/conv%2Ffrom-header?api-version=1",
        {
            "activity": {
                "type": "message",
                "text": "Hello",
                "conversation": {"id": "conv/from-header"},
            }
        },
        {
            "Authorization": "Bearer tok",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )


@pytest.mark.asyncio
async def test_start_raises_when_no_conversation_id(monkeypatch):
    monkeypatch.setattr(direct_client, "get_token", lambda *a, **k: "tok")
    monkeypatch.setattr(direct_client, "CopilotClient", _FakeNoIdClient)
    d = DirectClient(ENVIRONMENT_ID, SCHEMA_NAME)

    with pytest.raises(RuntimeError, match="no conversation id"):
        await d.start()
