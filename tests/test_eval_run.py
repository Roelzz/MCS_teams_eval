"""Integration: run the Inspect task end-to-end with fake channel clients + a mock judge.

Validates the dual_channel solver + channel_diff scorer wiring without real credentials.
"""

from inspect_ai import eval as inspect_eval

import channel_eval
from compare import ChannelResponse


class FakeDirect:
    def __init__(self, *args, **kwargs):
        pass

    async def start(self):
        pass

    async def ask(self, text):
        return ChannelResponse(channel="direct", text="I love pancakes", raw="I love pancakes")


class FakeTeams:
    def __init__(self, *args, **kwargs):
        pass

    @classmethod
    def from_config(cls, cfg):
        return cls()

    async def reset(self, commands):
        pass

    async def ask(self, text):
        return ChannelResponse(
            channel="teams",
            text="I love pancakes",
            raw="<p>I love pancakes</p>",
            is_html=True,
        )


def test_eval_run_channels_match(monkeypatch, tmp_path):
    monkeypatch.setenv("COMPARE_SEMANTIC", "false")  # deterministic: text-only comparison
    monkeypatch.setattr(channel_eval, "DirectClient", FakeDirect)
    monkeypatch.setattr(channel_eval, "TeamsClient", FakeTeams)
    monkeypatch.setattr(
        channel_eval,
        "get_agent",
        lambda name: {
            "direct": {"environment_id": "e", "agent_identifier": "cr_x"},
            "teams": {"chat_id": "19:abc@unq.gbl.spaces"},
        },
    )

    task = channel_eval.channel_parity(
        agent="x", testset="testsets/smoke.json", quality_grading=False
    )
    logs = inspect_eval(task, model="mockllm/model", log_dir=str(tmp_path), display="none")
    log = logs[0]
    assert log.status == "success"
    assert log.samples
    values = [s.scores["channel_diff"].value for s in log.samples]
    assert all(v == "C" for v in values), values
