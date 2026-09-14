from compare import (
    ChannelResponse,
    aggregate,
    compare_normalized,
    compare_structured,
    html_to_text,
    match_from_layers,
    normalize_text,
)


def test_html_to_text_strips_tags_and_unescapes():
    assert html_to_text("<p>Hello&nbsp;<b>world</b></p>") == "Hello world"
    assert html_to_text("plain text") == "plain text"
    assert html_to_text("") == ""


def test_normalize_text_collapses_and_lowercases():
    assert normalize_text("  <p>I  LOVE\nPancakes</p> ") == "i love pancakes"
    # zero-width chars removed
    assert normalize_text("a\u200bb") == "ab"


def test_compare_normalized_html_vs_plain_match():
    d = ChannelResponse(channel="direct", text="I love pancakes", raw="I love pancakes")
    t = ChannelResponse(
        channel="teams", text="I love pancakes", raw="<p>I love pancakes</p>", is_html=True
    )
    assert compare_normalized(d, t) is True


def test_compare_normalized_mismatch():
    d = ChannelResponse(channel="direct", text="yes")
    t = ChannelResponse(channel="teams", text="no")
    assert compare_normalized(d, t) is False


def test_compare_structured_body_and_parts():
    d = ChannelResponse(
        channel="direct",
        raw="Answer",
        citations=["https://a.com"],
        suggested=["Yes", "No"],
    )
    t = ChannelResponse(
        channel="teams",
        raw="<div>Answer</div>",
        citations=["https://A.com"],
        suggested=["yes", "no"],
        is_html=True,
    )
    result = compare_structured(d, t)
    assert result["body"] is True
    assert result["citations"] is True  # case-insensitive set compare
    assert result["suggested"] is True
    assert result["cards"] is None  # absent in both -> excluded
    assert result["match"] is True


def test_compare_structured_disabled_parts_excluded():
    d = ChannelResponse(channel="direct", raw="x", citations=["a"])
    t = ChannelResponse(channel="teams", raw="x", citations=["b"])
    # citations differ but disabled -> still a match on body alone
    result = compare_structured(d, t, citations=False)
    assert result["citations"] is None
    assert result["match"] is True


def test_match_from_layers_any_policy():
    both = ["normalized", "semantic"]
    assert match_from_layers({"normalized": False, "semantic": True}, both)
    assert not match_from_layers({"normalized": False, "semantic": False}, both)
    # disabled layer (None) is ignored
    assert match_from_layers({"normalized": True, "semantic": None}, ["normalized", "semantic"])
    # no evaluated layers -> False
    assert not match_from_layers({"semantic": None}, ["semantic"])


def test_aggregate_threshold():
    repeats = [
        {"normalized": True, "semantic": None, "structured": True},
        {"normalized": True, "semantic": None, "structured": False},
        {"normalized": False, "semantic": None, "structured": False},
    ]
    agg = aggregate(repeats, threshold=0.66, policy=["normalized"])
    assert agg["repeats"] == 3
    assert round(agg["agreement"], 2) == 0.67
    assert agg["passed"] is True
    assert round(agg["per_layer_agreement"]["normalized"], 2) == 0.67
    assert agg["per_layer_agreement"]["semantic"] is None

    agg2 = aggregate(repeats, threshold=0.9, policy=["normalized"])
    assert agg2["passed"] is False
