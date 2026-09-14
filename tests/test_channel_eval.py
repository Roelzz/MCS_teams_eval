import json

import pytest
from inspect_ai.scorer import CORRECT, INCORRECT, NOANSWER, SampleScore, Score

from channel_eval import (
    _comparator_config,
    get_agent,
    guarded_channel,
    load_testset,
    match_rate,
)
from compare import ChannelResponse


def test_load_testset_string_and_turns(tmp_path):
    ds = load_testset("testsets/smoke.json")
    assert len(ds.samples) == 3
    first = ds.samples[0]
    assert first.metadata["turns"] == [
        {"query": "Tell me you like pancakes please", "expected": ""}
    ]
    assert first.input == "Tell me you like pancakes please"

    multi = {
        "name": "multi",
        "cases": [
            {
                "id": "m1",
                "input": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "ignored"},
                    {"role": "user", "content": "second"},
                ],
            }
        ],
    }
    p = tmp_path / "multi.json"
    p.write_text(json.dumps(multi))
    ds2 = load_testset(str(p))
    assert ds2.samples[0].metadata["turns"] == [
        {"query": "hi", "expected": ""},
        {"query": "second", "expected": ""},
    ]
    assert ds2.samples[0].input == "second"  # last user turn is the display prompt


def test_load_testset_per_turn_expected(tmp_path):
    # CSV-style multi-turn case: each turn carries its own expected response
    data = {
        "name": "perturn",
        "cases": [
            {
                "id": "c1",
                "input": [
                    {"role": "user", "content": "q1", "expected": "a1"},
                    {"role": "user", "content": "q2", "expected": "a2"},
                ],
            }
        ],
    }
    p = tmp_path / "perturn.json"
    p.write_text(json.dumps(data))
    ds = load_testset(str(p))
    assert ds.samples[0].metadata["turns"] == [
        {"query": "q1", "expected": "a1"},
        {"query": "q2", "expected": "a2"},
    ]


def test_load_testset_legacy_target_maps_to_last_turn(tmp_path):
    # legacy multi-turn case with only a case-level target → graded on the final turn
    data = {
        "name": "legacy",
        "cases": [
            {
                "id": "c1",
                "input": [
                    {"role": "user", "content": "q1"},
                    {"role": "user", "content": "q2"},
                ],
                "target": "final answer",
            }
        ],
    }
    p = tmp_path / "legacy.json"
    p.write_text(json.dumps(data))
    ds = load_testset(str(p))
    assert ds.samples[0].metadata["turns"] == [
        {"query": "q1", "expected": ""},
        {"query": "q2", "expected": "final answer"},
    ]


def test_comparator_config_reads_env(monkeypatch):
    monkeypatch.setenv("COMPARE_SEMANTIC", "false")
    monkeypatch.setenv("COMPARE_CARDS", "0")
    monkeypatch.setenv("MATCH_POLICY", "normalized,structured")
    cfg = _comparator_config()
    assert cfg["semantic"] is False
    assert cfg["cards"] is False
    assert cfg["structured"] is True
    assert cfg["policy"] == ["normalized", "structured"]


def test_get_agent_demo_and_missing(tmp_path, monkeypatch):
    import channel_eval

    agents = {
        "demo-agent": {
            "direct": {"environment_id": "e", "agent_identifier": "s"},
            "teams": {"chat_id": "19:abc@unq.gbl.spaces"},
        }
    }
    p = tmp_path / "agents.json"
    p.write_text(json.dumps(agents))
    monkeypatch.setattr(channel_eval, "AGENTS_FILE", str(p))

    agent = get_agent("demo-agent")
    assert "direct" in agent and "teams" in agent
    with pytest.raises(ValueError):
        get_agent("does-not-exist")


def _sample_score(value):
    return SampleScore(
        score=Score(value=value), sample_id="x", sample_metadata={}, scorer="channel_diff"
    )


def test_match_rate_excludes_noanswer():
    compute = match_rate()
    scores = [
        _sample_score(CORRECT),
        _sample_score(CORRECT),
        _sample_score(INCORRECT),
        _sample_score(NOANSWER),  # excluded
    ]
    assert compute(scores) == pytest.approx(2 / 3)
    assert compute([_sample_score(NOANSWER)]) == 0.0


@pytest.mark.asyncio
async def test_guarded_channel_passes_through_success():
    async def ok():
        return ChannelResponse(channel="direct", text="hi")

    resp = await guarded_channel(ok(), "direct", 5.0)
    assert resp.error is None
    assert resp.text == "hi"


@pytest.mark.asyncio
async def test_guarded_channel_times_out():
    import asyncio

    async def hang():
        await asyncio.sleep(10)
        return ChannelResponse(channel="direct", text="never")

    resp = await guarded_channel(hang(), "direct", 0.05)
    assert resp.channel == "direct"
    assert resp.error and "timed out" in resp.error
    assert resp.text == ""


@pytest.mark.asyncio
async def test_guarded_channel_captures_exception():
    async def boom():
        raise RuntimeError("d2e exploded")

    resp = await guarded_channel(boom(), "teams", 5.0)
    assert resp.channel == "teams"
    assert resp.error and "RuntimeError" in resp.error and "d2e exploded" in resp.error
