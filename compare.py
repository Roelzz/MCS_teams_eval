"""Channel response model, normalizers, and the three comparison layers.

Comparators
-----------
1. normalized text  — strip HTML -> plain, unescape, collapse whitespace, lowercase, exact equality
2. semantic         — a judge model decides whether two answers mean the same thing
3. HTML/structured  — HTML-derived body text + structured parts (citations, cards, suggested)

Also hosts the judge helpers used for semantic comparison and (optional) quality grading, both
built on Inspect AI's model layer so the framework manages the judge provider, retries, and logging.
"""

import html as html_lib
import os
import re

from bs4 import BeautifulSoup
from inspect_ai.model import Model, get_model
from loguru import logger
from pydantic import BaseModel, Field

_WS_RE = re.compile(r"\s+")
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")
_GRADE_RE = re.compile(r"GRADE:\s*([CI])", re.IGNORECASE)


class ChannelResponse(BaseModel):
    """Normalized response from one channel for one turn (a prompt + all its bot messages)."""

    channel: str
    text: str = ""
    messages: list[str] = Field(default_factory=list)
    raw: str = ""
    is_html: bool = False
    citations: list[str] = Field(default_factory=list)
    cards: list[dict] = Field(default_factory=list)
    suggested: list[str] = Field(default_factory=list)
    activities: list[dict] = Field(default_factory=list)
    error: str | None = None


# ── Normalizers / extraction ─────────────────────────────────────────────────


def html_to_text(content: str) -> str:
    """Convert HTML (or plain text) to readable plain text."""
    if not content:
        return ""
    if "<" in content and ">" in content:
        text = BeautifulSoup(content, "html.parser").get_text(" ")
    else:
        text = content
    return _WS_RE.sub(" ", html_lib.unescape(text)).strip()


def normalize_text(s: str) -> str:
    """Aggressive normalization for exact text comparison."""
    s = html_to_text(s)
    s = _ZERO_WIDTH_RE.sub("", s)
    s = _WS_RE.sub(" ", s).strip().lower()
    return s


def extract_html_links(content: str) -> list[str]:
    """Best-effort citation extraction from HTML: anchor hrefs."""
    if not content or "<a" not in content.lower():
        return []
    soup = BeautifulSoup(content, "html.parser")
    return [a.get("href", "").strip() for a in soup.find_all("a") if a.get("href")]


def _card_texts(cards: list[dict]) -> set[str]:
    """Flatten adaptive-card-ish dicts into a set of their string leaves."""
    out: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            t = node.strip()
            if t:
                out.add(t.lower())

    for c in cards:
        walk(c)
    return out


# ── Layer 1: normalized text ─────────────────────────────────────────────────


def compare_normalized(a: ChannelResponse, b: ChannelResponse) -> bool:
    return normalize_text(a.text) == normalize_text(b.text)


# ── Layer 3: HTML / structured ───────────────────────────────────────────────


def compare_structured(
    a: ChannelResponse,
    b: ChannelResponse,
    *,
    citations: bool = True,
    cards: bool = True,
    suggested: bool = True,
) -> dict:
    """Compare HTML-derived body + structured parts. Returns per-part booleans + overall match.

    Parts that are disabled (or absent in both responses) are reported as None and excluded
    from the overall match decision.
    """
    result: dict[str, object] = {}

    body_match = normalize_text(a.raw) == normalize_text(b.raw)
    result["body"] = body_match
    parts = [body_match]

    def set_compare(enabled: bool, va: set, vb: set) -> bool | None:
        if not enabled or (not va and not vb):
            return None
        return va == vb

    cit = set_compare(citations, set(map(str.lower, a.citations)), set(map(str.lower, b.citations)))
    result["citations"] = cit
    if cit is not None:
        parts.append(cit)

    crd = set_compare(cards, _card_texts(a.cards), _card_texts(b.cards))
    result["cards"] = crd
    if crd is not None:
        parts.append(crd)

    sug = set_compare(suggested, set(map(str.lower, a.suggested)), set(map(str.lower, b.suggested)))
    result["suggested"] = sug
    if sug is not None:
        parts.append(sug)

    result["match"] = all(parts)
    return result


# ── Layer 2: semantic (judge) ────────────────────────────────────────────────


def get_judge_model() -> Model:
    """Resolve the judge model from JUDGE_MODEL env, else the active task model."""
    name = os.getenv("JUDGE_MODEL")
    return get_model(name) if name else get_model()


_SEMANTIC_TEMPLATE = """You are comparing two answers produced by the SAME AI agent through two \
different channels. Decide whether they convey the SAME MEANING to an end user. Ignore formatting, \
greetings, ordering, and minor wording differences. Focus on substantive content, facts, and intent.

[Channel A — {a_channel}]
{a}

[Channel B — {b_channel}]
{b}

Briefly explain, then end with exactly one line:
GRADE: C   (if they convey the same meaning)
GRADE: I   (if they differ in meaning)
"""


async def semantic_equivalent(
    a: ChannelResponse, b: ChannelResponse, model: Model | None = None
) -> tuple[bool, str]:
    """Ask the judge if two channel answers mean the same. Returns (equivalent, explanation)."""
    model = model or get_judge_model()
    prompt = _SEMANTIC_TEMPLATE.format(
        a_channel=a.channel,
        b_channel=b.channel,
        a=html_to_text(a.text) or "[empty]",
        b=html_to_text(b.text) or "[empty]",
    )
    output = await model.generate(prompt)
    completion = output.completion or ""
    m = _GRADE_RE.search(completion)
    if not m:
        logger.warning("Judge returned no GRADE marker; treating as not-equivalent")
        return False, completion
    return m.group(1).upper() == "C", completion


_QUALITY_TEMPLATE = """You are grading an AI agent's answer against the criteria for a correct \
response.

[Question]
{question}

[Criteria for a correct answer]
{target}

[Agent answer]
{answer}

Briefly explain, then end with exactly one line:
GRADE: C   (if the answer satisfies the criteria)
GRADE: I   (if it does not)
"""


async def grade_quality(
    question: str, answer: str, target: str, model: Model | None = None
) -> tuple[bool, str]:
    """Grade a single answer against target criteria. Returns (correct, explanation)."""
    model = model or get_judge_model()
    prompt = _QUALITY_TEMPLATE.format(
        question=question, target=target, answer=html_to_text(answer) or "[empty]"
    )
    output = await model.generate(prompt)
    completion = output.completion or ""
    m = _GRADE_RE.search(completion)
    if not m:
        return False, completion
    return m.group(1).upper() == "C", completion


# ── Match policy & repeat aggregation ────────────────────────────────────────


def match_from_layers(layers: dict, policy: list[str]) -> bool:
    """Decide overall match for one repeat given enabled layers and a policy.

    policy is a list among {"normalized", "semantic", "structured"}; the sample matches if ANY
    of the policy layers that were actually evaluated returns True.
    """
    considered = [layers[name] for name in policy if layers.get(name) is not None]
    return any(considered) if considered else False


def aggregate(repeats: list[dict], threshold: float, policy: list[str]) -> dict:
    """Aggregate per-repeat layer results into agreement fractions and a pass/fail.

    Each item in `repeats` is a dict of layer -> bool|None.
    """
    n = len(repeats)
    per_layer: dict[str, float | None] = {}
    for layer in ("normalized", "semantic", "structured"):
        vals = [r[layer] for r in repeats if r.get(layer) is not None]
        per_layer[layer] = (sum(1 for v in vals if v) / len(vals)) if vals else None

    matches = [match_from_layers(r, policy) for r in repeats]
    agreement = (sum(1 for m in matches if m) / n) if n else 0.0
    return {
        "repeats": n,
        "agreement": agreement,
        "threshold": threshold,
        "passed": agreement >= threshold,
        "per_layer_agreement": per_layer,
        "per_repeat_match": matches,
    }
