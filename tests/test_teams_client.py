import pytest

from teams_client import TeamsClient, parse_chat_id

RAW_ID = (
    "19:6173da01-d84e-4dbd-beef-12fadce152f1_737b962c-9bd0-4967-b71d-c96be4b90ec5@unq.gbl.spaces"
)


def test_parse_chat_id_from_link():
    link = (
        "https://teams.cloud.microsoft/l/chat/"
        + RAW_ID
        + "/conversations?context=%7B%22a%22%3A1%7D"
    )
    assert parse_chat_id(link) == RAW_ID


def test_parse_chat_id_url_encoded():
    encoded = RAW_ID.replace(":", "%3A").replace("@", "%40")
    link = f"https://teams.cloud.microsoft/l/chat/{encoded}/conversations?context=x"
    assert parse_chat_id(link) == RAW_ID


def test_parse_chat_id_raw_passthrough():
    assert parse_chat_id(RAW_ID) == RAW_ID


def test_parse_chat_id_invalid():
    with pytest.raises(ValueError):
        parse_chat_id("https://example.com/not-a-chat")


def test_from_config_accepts_link_or_id():
    assert TeamsClient.from_config({"chat_id": RAW_ID}).chat_id == RAW_ID
    link = f"https://teams.cloud.microsoft/l/chat/{RAW_ID}/conversations?x=1"
    assert TeamsClient.from_config({"chat_link": link}).chat_id == RAW_ID
    with pytest.raises(ValueError):
        TeamsClient.from_config({})


def test_is_bot_detection():
    bot = {"from": {"application": {"displayName": "Demo"}, "user": None}}
    human = {"from": {"user": {"displayName": "Roel"}, "application": None}}
    assert TeamsClient._is_bot(bot) is True
    assert TeamsClient._is_bot(human) is False
    assert TeamsClient._is_bot({}) is False


def test_build_response_concatenates_and_strips_html():
    client = TeamsClient(RAW_ID)
    collected = {
        "2": {"createdDateTime": "2026-06-15T09:33:02Z", "body": {"content": "<p>world</p>"}},
        "1": {"createdDateTime": "2026-06-15T09:33:01Z", "body": {"content": "<p>hello</p>"}},
    }
    resp = client._build_response(collected)
    assert resp.channel == "teams"
    assert resp.is_html is True
    # ordered by createdDateTime: hello then world
    assert resp.messages == ["hello", "world"]
    assert resp.text == "hello\nworld"
    assert "<p>hello</p>" in resp.raw


def test_build_response_empty_is_error():
    resp = TeamsClient(RAW_ID)._build_response({})
    assert resp.error is not None
    assert resp.text == ""


def test_parse_attachment_json_string_and_dict():
    assert TeamsClient._parse_attachment({"content": '{"a": 1}'}) == {"a": 1}
    assert TeamsClient._parse_attachment({"content": {"b": 2}}) == {"b": 2}
    assert TeamsClient._parse_attachment({"content": "not json"}) is None


def test_card_action_titles():
    card = {"actions": [{"title": "Yes"}, {"title": "No"}, {"noTitle": True}]}
    assert TeamsClient._card_action_titles(card) == ["Yes", "No"]


def test_citations_extracted_from_anchor_in_build():
    client = TeamsClient(RAW_ID)
    collected = {
        "1": {
            "createdDateTime": "2026-06-15T09:33:01Z",
            "body": {"content": '<a href="https://src.example/doc">doc</a>'},
        }
    }
    resp = client._build_response(collected)
    assert "https://src.example/doc" in resp.citations


def test_turn_wait_reads_env(monkeypatch):
    monkeypatch.setenv("TEAMS_TURN_WAIT_S", "7.5")
    assert TeamsClient(RAW_ID).turn_wait == 7.5
    monkeypatch.delenv("TEAMS_TURN_WAIT_S", raising=False)
    assert TeamsClient(RAW_ID).turn_wait == 10.0


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.text = ""

    def json(self):
        return self._payload


class _FakeClient:
    """Records POST/GET order; returns a sent message then one bot reply."""

    def __init__(self, events, *_a, **_k):
        self.events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *_a, **_k):
        self.events.append(("post",))
        return _FakeResp({"id": "sent1", "createdDateTime": "2026-06-15T09:00:00Z"})

    async def get(self, *_a, **_k):
        self.events.append(("get",))
        return _FakeResp(
            {
                "value": [
                    {
                        "id": "bot1",
                        "createdDateTime": "2026-06-15T09:00:05Z",
                        "from": {"application": {"displayName": "Demo"}, "user": None},
                        "body": {"content": "<p>hi</p>"},
                    }
                ]
            }
        )


async def test_ask_waits_turn_wait_before_first_poll(monkeypatch):
    monkeypatch.setenv("TEAMS_TURN_WAIT_S", "5")
    monkeypatch.setenv("TEAMS_QUIET_WINDOW_S", "0")
    monkeypatch.setenv("TEAMS_POLL_INTERVAL_S", "0")
    monkeypatch.setenv("TEAMS_RATE_DELAY_S", "0")

    events: list = []
    monkeypatch.setattr(
        "teams_client.httpx.AsyncClient", lambda *a, **k: _FakeClient(events, *a, **k)
    )
    monkeypatch.setattr("teams_client.get_token", lambda *a, **k: "tok")

    async def fake_sleep(d):
        events.append(("sleep", d))

    monkeypatch.setattr("teams_client.asyncio.sleep", fake_sleep)

    client = TeamsClient(RAW_ID)
    resp = await client.ask("hello")
    assert resp.text == "hi"

    first_get = next(i for i, e in enumerate(events) if e[0] == "get")
    wait_idx = next(i for i, e in enumerate(events) if e == ("sleep", 5.0))
    post_idx = next(i for i, e in enumerate(events) if e[0] == "post")
    # the turn-wait sleep happens after the POST and before any poll/GET
    assert post_idx < wait_idx < first_get
    assert not any(e[0] == "get" for e in events[:wait_idx])
