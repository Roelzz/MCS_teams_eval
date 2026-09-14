"""Safe read/write helpers for the UI: .env, agents.json, testsets/*.json.

Everything is dict/list based (JSON-friendly for Reflex state). Pydantic models are
used only to validate on save, raising ValueError with a readable message.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

from dotenv import dotenv_values, set_key, unset_key
from pydantic import BaseModel, ValidationError, field_validator

from teams_client import parse_chat_id

ENV_PATH = Path(".env")
ENV_EXAMPLE_PATH = Path(".env.example")
AGENTS_PATH = Path("agents.json")
TESTSETS_DIR = Path("testsets")


# ── .env ─────────────────────────────────────────────────────────────────────
def read_env() -> dict[str, str]:
    """Current values from .env (empty dict if the file is absent)."""
    if not ENV_PATH.exists():
        return {}
    return {k: (v or "") for k, v in dotenv_values(ENV_PATH).items()}


def read_env_template() -> dict[str, str]:
    """Default keys/values from .env.example (the canonical key list)."""
    if not ENV_EXAMPLE_PATH.exists():
        return {}
    return {k: (v or "") for k, v in dotenv_values(ENV_EXAMPLE_PATH).items()}


def effective_env() -> dict[str, str]:
    """Template defaults overlaid with whatever is set in .env."""
    merged = read_env_template()
    merged.update(read_env())
    return merged


def write_env(updates: dict[str, str]) -> None:
    """Persist key/value pairs into .env, creating it if needed.

    An empty string clears the value but keeps the key; ``None`` removes the key.
    """
    ENV_PATH.touch(exist_ok=True)
    path = str(ENV_PATH)
    for key, value in updates.items():
        if value is None:
            unset_key(path, key)
        else:
            set_key(path, key, value, quote_mode="never")


# ── agents.json ──────────────────────────────────────────────────────────────
class _DirectCfg(BaseModel):
    environment_id: str
    agent_identifier: str

    @field_validator("environment_id", "agent_identifier")
    @classmethod
    def _required(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v.strip()


class _TeamsCfg(BaseModel):
    chat_link: str | None = None
    chat_id: str | None = None


class _AgentCfg(BaseModel):
    description: str = ""
    direct: _DirectCfg
    teams: _TeamsCfg


def read_agents() -> dict[str, dict]:
    if not AGENTS_PATH.exists():
        return {}
    return json.loads(AGENTS_PATH.read_text())


def _write_agents(agents: dict[str, dict]) -> None:
    AGENTS_PATH.write_text(json.dumps(agents, indent=2) + "\n")


def validate_agent(data: dict[str, Any]) -> dict[str, Any]:
    """Validate one agent config dict; returns the cleaned dict or raises ValueError."""
    try:
        cfg = _AgentCfg.model_validate(data)
    except ValidationError as exc:  # noqa: PERF203
        raise ValueError(_first_error(exc)) from exc

    raw = cfg.teams.chat_id or cfg.teams.chat_link
    if not raw:
        raise ValueError("Teams config needs 'chat_link' or 'chat_id'")
    parse_chat_id(raw)  # raises ValueError on a malformed link/id
    return cfg.model_dump(exclude_none=True)


def save_agent(name: str, data: dict[str, Any]) -> None:
    name = name.strip()
    if not name:
        raise ValueError("Agent name must not be empty")
    cleaned = validate_agent(data)
    agents = read_agents()
    agents[name] = cleaned
    _write_agents(agents)


def delete_agent(name: str) -> None:
    agents = read_agents()
    if agents.pop(name, None) is not None:
        _write_agents(agents)


# ── testsets/*.json ──────────────────────────────────────────────────────────
class _Turn(BaseModel):
    role: str = "user"
    content: str
    expected: str = ""


class _TestCase(BaseModel):
    id: str
    input: str | list[_Turn]
    target: str = ""
    tags: list[str] = []
    repeats: int | None = None
    agreement_threshold: float | None = None

    @field_validator("id")
    @classmethod
    def _id_required(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("case id must not be empty")
        return v.strip()


class _Testset(BaseModel):
    name: str
    cases: list[_TestCase]


def list_testsets() -> list[str]:
    if not TESTSETS_DIR.exists():
        return []
    return sorted(p.stem for p in TESTSETS_DIR.glob("*.json"))


def read_testset(name: str) -> dict:
    return json.loads((TESTSETS_DIR / f"{name}.json").read_text())


def validate_testset(data: dict[str, Any]) -> dict[str, Any]:
    try:
        ts = _Testset.model_validate(data)
    except ValidationError as exc:
        raise ValueError(_first_error(exc)) from exc
    ids = [c.id for c in ts.cases]
    if len(ids) != len(set(ids)):
        raise ValueError("case ids must be unique within a testset")
    return ts.model_dump(exclude_none=True)


def save_testset(name: str, data: dict[str, Any]) -> None:
    name = name.strip()
    if not name:
        raise ValueError("Testset name must not be empty")
    cleaned = validate_testset(data)
    cleaned["name"] = name
    TESTSETS_DIR.mkdir(exist_ok=True)
    (TESTSETS_DIR / f"{name}.json").write_text(json.dumps(cleaned, indent=2) + "\n")


def delete_testset(name: str) -> None:
    (TESTSETS_DIR / f"{name}.json").unlink(missing_ok=True)


_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9 _-]+")


def sanitize_testset_name(name: str) -> str:
    """Strip path separators / unsafe chars so an imported name can't escape TESTSETS_DIR."""
    cleaned = _NAME_SAFE_RE.sub("", (name or "").strip()).strip()
    return cleaned


def import_testset(filename: str, raw: bytes | str) -> str:
    """Validate and save an uploaded testset; returns the saved name.

    Accepts either the internal JSON format or the Copilot Studio *Evaluate Agent* CSV
    (``Test Case ID, Turn, Query, Expected Response``). The format is auto-detected from the
    file extension or a recognizable header. Raises ValueError on a parse/validation failure.
    """
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw.lstrip("\ufeff")

    if _looks_like_csv(filename, text):
        data = parse_testset_csv(filename, text)
        name = data["name"]
        if not name:
            raise ValueError("Could not derive a valid testset name from the file.")
        save_testset(name, data)  # validates and writes
        return name

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Testset must be a JSON object with 'name' and 'cases'.")

    name = sanitize_testset_name(str(data.get("name") or "")) or sanitize_testset_name(
        Path(filename).stem
    )
    if not name:
        raise ValueError("Could not derive a valid testset name from the file.")

    data["name"] = name  # ensure the (sanitized/derived) name is present for validation
    save_testset(name, data)  # validates and writes
    return name


# ── Copilot Studio "Evaluate Agent" CSV ──────────────────────────────────────
CSV_COLUMNS = ["Test Case ID", "Turn", "Query", "Expected Response"]


def _looks_like_csv(filename: str, text: str) -> bool:
    """Detect the Evaluate Agent CSV by extension or a 'Test Case ID,Turn,…' header."""
    if filename.lower().endswith(".csv"):
        return True
    stripped = text.lstrip("\ufeff").lstrip()
    if not stripped:
        return False
    header = stripped.splitlines()[0]
    cells = {c.strip().lower() for c in header.split(",")}
    return "test case id" in cells and "turn" in cells


def parse_testset_csv(filename: str, raw: bytes | str) -> dict[str, Any]:
    """Parse a Copilot Studio *Evaluate Agent* CSV into a testset dict.

    Rows sharing a ``Test Case ID`` become one multi-turn case (ordered by ``Turn``); each turn
    carries its own ``Query`` and ``Expected Response``. The testset name comes from the file stem.
    """
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV has no header row.")

    norm = {(name or "").strip().lower(): name for name in reader.fieldnames}
    try:
        col_id, col_turn, col_query = norm["test case id"], norm["turn"], norm["query"]
    except KeyError as exc:
        raise ValueError(
            "CSV must have columns: Test Case ID, Turn, Query, Expected Response."
        ) from exc
    col_expected = norm.get("expected response")

    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for row in reader:
        tcid = (row.get(col_id) or "").strip()
        if not tcid:
            continue
        try:
            turn_no = int((row.get(col_turn) or "").strip() or 0)
        except ValueError:
            turn_no = 0
        turn = {
            "turn": turn_no,
            "query": (row.get(col_query) or "").strip(),
            "expected": (row.get(col_expected) or "").strip() if col_expected else "",
        }
        if tcid not in grouped:
            grouped[tcid] = []
            order.append(tcid)
        grouped[tcid].append(turn)

    if not order:
        raise ValueError("CSV has no data rows.")

    cases: list[dict] = []
    for tcid in order:
        turns = sorted(grouped[tcid], key=lambda r: r["turn"])
        cases.append(
            {
                "id": tcid,
                "input": [
                    {"role": "user", "content": t["query"], "expected": t["expected"]}
                    for t in turns
                ],
            }
        )

    name = sanitize_testset_name(Path(filename).stem) or "imported"
    return {"name": name, "cases": cases}


def testset_to_csv(data: dict[str, Any]) -> str:
    """Serialize a testset dict into the Copilot Studio *Evaluate Agent* CSV format.

    Multi-turn (list) cases emit one row per user turn with that turn's expected response; a
    case-level ``target`` falls back onto the final turn. Single-string cases emit one row.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for case in data.get("cases", []):
        tcid = case.get("id", "")
        target = case.get("target", "") or ""
        inp = case.get("input", "")
        if isinstance(inp, list):
            turns = [t for t in inp if t.get("role", "user") == "user"]
            last = len(turns) - 1
            for i, t in enumerate(turns):
                expected = t.get("expected", "") or (target if i == last else "")
                writer.writerow([tcid, i + 1, t.get("content", ""), expected])
        else:
            writer.writerow([tcid, 1, inp, target])
    return buf.getvalue().rstrip("\n")


# ── helpers ──────────────────────────────────────────────────────────────────
def _first_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    loc = ".".join(str(p) for p in err.get("loc", ()))
    msg = err.get("msg", "invalid value")
    return f"{loc}: {msg}" if loc else msg
