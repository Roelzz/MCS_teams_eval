"""Unit tests for ui.services.config_io (env, agents.json, testsets)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ui.services import config_io


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config_io, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(config_io, "ENV_EXAMPLE_PATH", tmp_path / ".env.example")
    monkeypatch.setattr(config_io, "AGENTS_PATH", tmp_path / "agents.json")
    monkeypatch.setattr(config_io, "TESTSETS_DIR", tmp_path / "testsets")
    return tmp_path


# ── .env ──────────────────────────────────────────────────────────────────────
def test_env_round_trip(sandbox: Path) -> None:
    config_io.write_env({"LOG_LEVEL": "DEBUG", "DEFAULT_REPEATS": "5"})
    assert config_io.read_env() == {"LOG_LEVEL": "DEBUG", "DEFAULT_REPEATS": "5"}


def test_env_update_and_clear(sandbox: Path) -> None:
    config_io.write_env({"A": "1", "B": "2"})
    config_io.write_env({"A": ""})  # clear value, keep key
    config_io.write_env({"B": None})  # remove key entirely
    env = config_io.read_env()
    assert env["A"] == ""
    assert "B" not in env


def test_effective_env_overlays_template(sandbox: Path) -> None:
    config_io.ENV_EXAMPLE_PATH.write_text("LOG_LEVEL=INFO\nJUDGE_MODEL=openai/gpt-4o\n")
    config_io.write_env({"LOG_LEVEL": "DEBUG"})
    merged = config_io.effective_env()
    assert merged["LOG_LEVEL"] == "DEBUG"  # .env wins
    assert merged["JUDGE_MODEL"] == "openai/gpt-4o"  # template default preserved


# ── agents.json ────────────────────────────────────────────────────────────────
def _valid_agent() -> dict:
    return {
        "description": "test",
        "direct": {"environment_id": "env-1", "agent_identifier": "cr_agent"},
        "teams": {"chat_link": "19:abc@unq.gbl.spaces"},
    }


def test_save_and_read_agent(sandbox: Path) -> None:
    config_io.save_agent("a1", _valid_agent())
    agents = config_io.read_agents()
    assert "a1" in agents
    assert agents["a1"]["direct"]["agent_identifier"] == "cr_agent"
    assert "chat_id" not in agents["a1"]["teams"]  # None dropped


def test_save_agent_rejects_missing_direct(sandbox: Path) -> None:
    bad = _valid_agent()
    del bad["direct"]["environment_id"]
    with pytest.raises(ValueError, match="environment_id"):
        config_io.save_agent("a1", bad)


def test_save_agent_rejects_no_teams_target(sandbox: Path) -> None:
    bad = _valid_agent()
    bad["teams"] = {}
    with pytest.raises(ValueError, match="chat_link"):
        config_io.save_agent("a1", bad)


def test_save_agent_rejects_bad_chat_link(sandbox: Path) -> None:
    bad = _valid_agent()
    bad["teams"] = {"chat_link": "https://example.com/not-a-chat"}
    with pytest.raises(ValueError):
        config_io.save_agent("a1", bad)


def test_delete_agent(sandbox: Path) -> None:
    config_io.save_agent("a1", _valid_agent())
    config_io.delete_agent("a1")
    assert config_io.read_agents() == {}


# ── testsets ────────────────────────────────────────────────────────────────────
def test_save_read_list_testset_string_input(sandbox: Path) -> None:
    data = {"name": "smoke", "cases": [{"id": "c1", "input": "hi", "tags": ["t"]}]}
    config_io.save_testset("smoke", data)
    assert config_io.list_testsets() == ["smoke"]
    loaded = config_io.read_testset("smoke")
    assert loaded["cases"][0]["input"] == "hi"
    assert loaded["name"] == "smoke"


def test_save_testset_multi_turn(sandbox: Path) -> None:
    data = {
        "name": "multi",
        "cases": [
            {
                "id": "c1",
                "input": [
                    {"role": "user", "content": "first"},
                    {"role": "user", "content": "second"},
                ],
            }
        ],
    }
    config_io.save_testset("multi", data)
    loaded = config_io.read_testset("multi")
    assert [t["content"] for t in loaded["cases"][0]["input"]] == ["first", "second"]


def test_save_testset_rejects_duplicate_ids(sandbox: Path) -> None:
    data = {
        "name": "dup",
        "cases": [{"id": "x", "input": "a"}, {"id": "x", "input": "b"}],
    }
    with pytest.raises(ValueError, match="unique"):
        config_io.save_testset("dup", data)


def test_save_testset_rejects_empty_case_id(sandbox: Path) -> None:
    data = {"name": "bad", "cases": [{"id": "  ", "input": "a"}]}
    with pytest.raises(ValueError):
        config_io.save_testset("bad", data)


def test_delete_testset(sandbox: Path) -> None:
    data = {"name": "smoke", "cases": [{"id": "c1", "input": "hi"}]}
    config_io.save_testset("smoke", data)
    config_io.delete_testset("smoke")
    assert config_io.list_testsets() == []


def test_save_testset_drops_none_overrides(sandbox: Path) -> None:
    data = {"name": "s", "cases": [{"id": "c1", "input": "hi"}]}
    config_io.save_testset("s", data)
    raw = json.loads((sandbox / "testsets" / "s.json").read_text())
    assert "repeats" not in raw["cases"][0]
    assert "agreement_threshold" not in raw["cases"][0]


# ── testset import / sanitize ─────────────────────────────────────────────────
def test_sanitize_testset_name() -> None:
    assert config_io.sanitize_testset_name("  my set ") == "my set"
    assert config_io.sanitize_testset_name("../../etc/passwd") == "etcpasswd"
    assert config_io.sanitize_testset_name("a/b\\c") == "abc"
    assert config_io.sanitize_testset_name("ok_name-1") == "ok_name-1"


def test_import_testset_happy(sandbox: Path) -> None:
    raw = json.dumps({"name": "imported", "cases": [{"id": "c1", "input": "hello"}]}).encode()
    name = config_io.import_testset("whatever.json", raw)
    assert name == "imported"
    assert "imported" in config_io.list_testsets()
    back = config_io.read_testset("imported")
    assert back["cases"][0]["id"] == "c1"


def test_import_testset_falls_back_to_filename(sandbox: Path) -> None:
    # no "name" in the payload → derive from the uploaded filename stem
    raw = json.dumps({"cases": [{"id": "c1", "input": "hi"}]}).encode()
    name = config_io.import_testset("from-file.json", raw)
    assert name == "from-file"


def test_import_testset_sanitizes_name(sandbox: Path) -> None:
    raw = json.dumps({"name": "../evil", "cases": [{"id": "c1", "input": "hi"}]}).encode()
    name = config_io.import_testset("x.json", raw)
    assert name == "evil"
    assert (config_io.TESTSETS_DIR / "evil.json").exists()


def test_import_testset_bad_json(sandbox: Path) -> None:
    with pytest.raises(ValueError, match="Not valid JSON"):
        config_io.import_testset("x.json", b"{not json")


def test_import_testset_invalid_schema(sandbox: Path) -> None:
    # missing required case id → validation error bubbles up as ValueError
    raw = json.dumps({"name": "bad", "cases": [{"input": "hi"}]}).encode()
    with pytest.raises(ValueError):
        config_io.import_testset("x.json", raw)


# ── Evaluate Agent CSV import / export ────────────────────────────────────────
_CSV = (
    "Test Case ID,Turn,Query,Expected Response\n"
    "id-a,1,How many days?,Five working days.\n"
    'id-a,2,What must a posting include?,"Role title, grade, and location."\n'
    "id-b,1,Who chairs the panel?,The Hiring Manager.\n"
)


def test_parse_testset_csv_groups_turns() -> None:
    data = config_io.parse_testset_csv("HR Policy.csv", _CSV)
    assert data["name"] == "HR Policy"
    assert [c["id"] for c in data["cases"]] == ["id-a", "id-b"]
    case_a = data["cases"][0]
    assert [t["content"] for t in case_a["input"]] == [
        "How many days?",
        "What must a posting include?",
    ]
    assert case_a["input"][1]["expected"] == "Role title, grade, and location."
    assert data["cases"][1]["input"][0]["expected"] == "The Hiring Manager."


def test_parse_testset_csv_sorts_by_turn() -> None:
    csv_unordered = "Test Case ID,Turn,Query,Expected Response\nx,2,second,b\nx,1,first,a\n"
    data = config_io.parse_testset_csv("t.csv", csv_unordered)
    assert [t["content"] for t in data["cases"][0]["input"]] == ["first", "second"]


def test_testset_csv_round_trip() -> None:
    data = config_io.parse_testset_csv("rt.csv", _CSV)
    cleaned = config_io.validate_testset(data)
    out = config_io.testset_to_csv(cleaned)
    reparsed = config_io.parse_testset_csv("rt.csv", out)
    assert [c["id"] for c in reparsed["cases"]] == [c["id"] for c in data["cases"]]
    for a, b in zip(data["cases"], reparsed["cases"]):
        assert [t["content"] for t in a["input"]] == [t["content"] for t in b["input"]]
        assert [t["expected"] for t in a["input"]] == [t["expected"] for t in b["input"]]


def test_testset_to_csv_has_header_and_quotes_commas() -> None:
    data = config_io.parse_testset_csv("q.csv", _CSV)
    out = config_io.testset_to_csv(data)
    lines = out.splitlines()
    assert lines[0] == "Test Case ID,Turn,Query,Expected Response"
    # a field containing a comma must be quoted
    assert '"Role title, grade, and location."' in out


def test_testset_to_csv_string_input_uses_target() -> None:
    data = {"name": "s", "cases": [{"id": "c1", "input": "hello?", "target": "hi there"}]}
    out = config_io.testset_to_csv(data)
    assert out.splitlines()[1] == "c1,1,hello?,hi there"


def test_testset_to_csv_target_falls_back_to_last_turn() -> None:
    data = {
        "name": "s",
        "cases": [
            {
                "id": "c1",
                "input": [
                    {"role": "user", "content": "q1"},
                    {"role": "user", "content": "q2"},
                ],
                "target": "final",
            }
        ],
    }
    rows = config_io.testset_to_csv(data).splitlines()
    assert rows[1] == "c1,1,q1,"
    assert rows[2] == "c1,2,q2,final"


def test_looks_like_csv_detects_by_extension_and_header() -> None:
    assert config_io._looks_like_csv("x.csv", "anything")
    assert config_io._looks_like_csv("noext", "Test Case ID,Turn,Query,Expected Response\n")
    assert not config_io._looks_like_csv("x.json", '{"name": "j", "cases": []}')


def test_import_testset_csv_by_extension(sandbox: Path) -> None:
    name = config_io.import_testset("HR Policy.csv", _CSV.encode())
    assert name == "HR Policy"
    assert "HR Policy" in config_io.list_testsets()
    back = config_io.read_testset("HR Policy")
    assert back["cases"][0]["id"] == "id-a"
    assert back["cases"][0]["input"][0]["expected"] == "Five working days."


def test_import_testset_csv_by_header_without_extension(sandbox: Path) -> None:
    name = config_io.import_testset("export", _CSV.encode())
    assert name == "export"
    assert config_io.read_testset("export")["cases"][1]["id"] == "id-b"


def test_import_testset_csv_strips_bom(sandbox: Path) -> None:
    raw = ("\ufeff" + _CSV).encode("utf-8")
    name = config_io.import_testset("bom.csv", raw)
    assert config_io.read_testset(name)["cases"][0]["id"] == "id-a"
