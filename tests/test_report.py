"""Unit tests for report.py multi-agent additions: agent column + rows_for_logs.

Uses lightweight duck-typed log/sample fakes (report._rows only reads a handful of attrs),
so no real Inspect eval is needed.
"""

from __future__ import annotations

from types import SimpleNamespace

import report


def _score(value: str, metadata: dict):
    return SimpleNamespace(value=value, metadata=metadata)


def _sample(sid: str, agent, *, match: bool = True):
    diff_meta = {
        "prompt": f"p-{sid}",
        "details": [
            {
                "direct_text": "hello there",
                "teams_text": "hello there" if match else "different reply",
                "layers": {"normalized": match, "semantic": None, "structured": match},
            }
        ],
        "agreement": {"agreement": 1.0 if match else 0.0},
    }
    scores = {"channel_diff": _score("C" if match else "I", diff_meta)}
    metadata = {"channels": {"agent": agent, "prompt": f"p-{sid}", "runs": []}}
    return SimpleNamespace(id=sid, input=f"p-{sid}", scores=scores, metadata=metadata)


def _log(task: str, samples: list):
    return SimpleNamespace(eval=SimpleNamespace(task=task), samples=samples)


def test_rows_uses_sample_agent() -> None:
    log = _log("channel_parity-Demo ING", [_sample("a", "Demo ING")])
    rows = report._rows(log)
    assert rows[0]["agent"] == "Demo ING"


def test_rows_agent_falls_back_to_task_name() -> None:
    # no agent in sample metadata → derive from the task name (strip channel_parity- prefix)
    log = _log("channel_parity-FallbackBot", [_sample("a", None)])
    rows = report._rows(log)
    assert rows[0]["agent"] == "FallbackBot"


def test_rows_for_logs_concatenates_and_keeps_agent(monkeypatch) -> None:
    logs = {
        "logs/a.eval": _log(
            "channel_parity-A", [_sample("1", "A"), _sample("2", "A", match=False)]
        ),
        "logs/b.eval": _log("channel_parity-B", [_sample("1", "B")]),
    }
    monkeypatch.setattr(report, "read_eval_log", lambda loc: logs[loc])
    rows = report.rows_for_logs(["logs/a.eval", "logs/b.eval"])
    assert [r["agent"] for r in rows] == ["A", "A", "B"]
    assert [r["result"] for r in rows] == ["MATCH", "DIFFER", "MATCH"]


def test_rows_for_logs_skips_unreadable(monkeypatch) -> None:
    def fake_read(loc: str):
        if loc == "bad":
            raise OSError("cannot read")
        return _log("channel_parity-A", [_sample("1", "A")])

    monkeypatch.setattr(report, "read_eval_log", fake_read)
    rows = report.rows_for_logs(["bad", "good"])
    assert [r["agent"] for r in rows] == ["A"]  # broken log skipped, others survive


def test_csv_includes_agent_column(tmp_path) -> None:
    rows = report._rows(_log("channel_parity-A", [_sample("1", "A")]))
    out = tmp_path / "o.csv"
    report._write_csv(rows, out)
    header = out.read_text().splitlines()[0].split(",")
    assert "agent" in header


def test_html_includes_agent_header_and_value(tmp_path) -> None:
    rows = report._rows(_log("channel_parity-Demo ING", [_sample("1", "Demo ING")]))
    out = tmp_path / "o.html"
    report._write_html(rows, out, "t")
    doc = out.read_text()
    assert "<th>agent</th>" in doc
    assert "Demo ING" in doc


def _multi_turn_sample(sid: str, agent: str):
    """A 2-turn conversation graded per turn (turn 1 matches, turn 2 differs)."""
    diff_meta = {
        "prompt": "q2",
        "details": [
            {
                "turn": 1,
                "query": "q1",
                "expected": "a1",
                "direct_text": "a1 reply",
                "teams_text": "a1 reply",
                "layers": {"normalized": True, "semantic": None, "structured": True},
                "agg": {"passed": True, "agreement": 1.0},
            },
            {
                "turn": 2,
                "query": "q2",
                "expected": "a2",
                "direct_text": "a2 direct",
                "teams_text": "a2 teams (different)",
                "layers": {"normalized": False, "semantic": None, "structured": False},
                "agg": {"passed": False, "agreement": 0.0},
            },
        ],
        "agreement": {"agreement": 0.5},
    }
    quality_meta = {
        "turns": [
            {"turn": 1, "direct_ok": True, "teams_ok": True},
            {"turn": 2, "direct_ok": True, "teams_ok": False},
        ]
    }
    scores = {
        "channel_diff": _score("I", diff_meta),
        "quality": _score("I", quality_meta),
    }
    metadata = {"channels": {"agent": agent, "prompt": "q2", "runs": []}}
    return SimpleNamespace(id=sid, input="q2", scores=scores, metadata=metadata)


def test_rows_expand_one_row_per_turn() -> None:
    log = _log("channel_parity-HR", [_multi_turn_sample("c1", "HR")])
    rows = report._rows(log)
    assert len(rows) == 2  # one row per turn, not per sample
    assert [r["turn"] for r in rows] == ["1", "2"]
    assert [r["prompt"] for r in rows] == ["q1", "q2"]
    assert [r["expected"] for r in rows] == ["a1", "a2"]
    assert [r["result"] for r in rows] == ["MATCH", "DIFFER"]
    # per-turn quality reflects each turn's own grade in both channels
    assert rows[0]["quality"].startswith("pass")
    assert rows[1]["quality"].startswith("fail")
    assert "teams=I" in rows[1]["quality"]


def test_csv_has_turn_and_expected_columns(tmp_path) -> None:
    rows = report._rows(_log("channel_parity-HR", [_multi_turn_sample("c1", "HR")]))
    out = tmp_path / "o.csv"
    report._write_csv(rows, out)
    header = out.read_text().splitlines()[0].split(",")
    assert "turn" in header
    assert "expected" in header
