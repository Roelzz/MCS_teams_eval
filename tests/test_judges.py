from inspect_ai.model import ModelOutput, get_model

from compare import ChannelResponse, grade_quality, semantic_equivalent


def _judge(text: str):
    return get_model(
        "mockllm/model",
        custom_outputs=[ModelOutput.from_content("mockllm", text)],
        memoize=False,
    )


async def test_semantic_equivalent_true():
    d = ChannelResponse(channel="direct", text="The capital is Paris.")
    t = ChannelResponse(channel="teams", text="Paris is the capital.")
    eq, _ = await semantic_equivalent(d, t, _judge("They match. GRADE: C"))
    assert eq is True


async def test_semantic_equivalent_false():
    d = ChannelResponse(channel="direct", text="yes")
    t = ChannelResponse(channel="teams", text="no")
    eq, _ = await semantic_equivalent(d, t, _judge("Different. GRADE: I"))
    assert eq is False


async def test_semantic_no_marker_defaults_false():
    d = ChannelResponse(channel="direct", text="a")
    t = ChannelResponse(channel="teams", text="b")
    eq, _ = await semantic_equivalent(d, t, _judge("no grade marker here"))
    assert eq is False


async def test_grade_quality_pass_fail():
    ok, _ = await grade_quality("q", "good answer", "criteria", _judge("Meets it. GRADE: C"))
    assert ok is True
    bad, _ = await grade_quality("q", "bad", "criteria", _judge("Nope. GRADE: I"))
    assert bad is False
