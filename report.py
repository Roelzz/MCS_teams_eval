"""Side-by-side parity report from Inspect eval logs.

Reads the latest (or a given) Inspect eval log and writes a usable CSV + HTML report:
prompt | Direct | Teams | normalized? | semantic? | structured? | agreement | result | quality.

    uv run python report.py                 # latest log -> reports/
    uv run python report.py --log logs/...eval --out-dir reports
"""

import csv
import difflib
import html
import webbrowser
from pathlib import Path

import typer
from dotenv import load_dotenv
from inspect_ai.log import list_eval_logs, read_eval_log
from loguru import logger

from compare import ChannelResponse, compare_structured

load_dotenv()

app = typer.Typer(add_completion=False)

COLUMNS = [
    "id",
    "turn",
    "agent",
    "prompt",
    "expected",
    "direct",
    "teams",
    "normalized",
    "semantic",
    "structured",
    "agreement",
    "result",
    "quality",
]


def _layer(layers: dict, name: str) -> str:
    v = layers.get(name)
    return "—" if v is None else ("yes" if v else "no")


def _word_diff(a: str, b: str) -> str:
    """Word-level diff: red = only in Direct, green = only in Teams. Returns safe HTML."""
    aw = (a or "").split()
    bw = (b or "").split()
    if not aw and not bw:
        return "<span class='na'>—</span>"
    sm = difflib.SequenceMatcher(a=aw, b=bw)
    out: list[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out.append(html.escape(" ".join(aw[i1:i2])))
        elif tag == "delete":
            out.append(f"<span class='del'>{html.escape(' '.join(aw[i1:i2]))}</span>")
        elif tag == "insert":
            out.append(f"<span class='ins'>{html.escape(' '.join(bw[j1:j2]))}</span>")
        elif tag == "replace":
            out.append(
                f"<span class='del'>{html.escape(' '.join(aw[i1:i2]))}</span> "
                f"<span class='ins'>{html.escape(' '.join(bw[j1:j2]))}</span>"
            )
    return " ".join(p for p in out if p) or "<span class='na'>—</span>"


def _layers_summary(agg: dict) -> str:
    """Compact per-layer agreement, e.g. 'norm 3/3 · sem — · struct 2/3'."""
    pla = (agg or {}).get("per_layer_agreement") or {}
    n = (agg or {}).get("repeats") or 1
    parts = []
    for name, label in (("normalized", "norm"), ("semantic", "sem"), ("structured", "struct")):
        v = pla.get(name)
        parts.append(f"{label} —" if v is None else f"{label} {round(v * n)}/{n}")
    return " · ".join(parts)


def _structured_breakdown(runs: list[dict]) -> str:
    """Per-part chips (citations/cards/suggested) for the first repeat, with match flags."""
    if not runs:
        return ""
    run0 = runs[0]
    try:
        d = ChannelResponse.model_validate(run0.get("direct") or {})
        t = ChannelResponse.model_validate(run0.get("teams") or {})
    except Exception:
        return ""
    res = compare_structured(d, t)
    chips: list[str] = []

    def chip(label: str, key: str, da: list, tb: list) -> None:
        v = res.get(key)
        if v is None:
            return
        cls = "ok" if v else "bad"
        sign = "=" if v else "≠"
        chips.append(f"<span class='chip {cls}'>{label} {len(da)}{sign}{len(tb)}</span>")

    chip("cite", "citations", d.citations, t.citations)
    chip("card", "cards", d.cards, t.cards)
    chip("sugg", "suggested", d.suggested, t.suggested)
    return " ".join(chips)


def _errors(first: dict) -> str:
    e = []
    if first.get("direct_error"):
        e.append(f"direct: {first['direct_error']}")
    if first.get("teams_error"):
        e.append(f"teams: {first['teams_error']}")
    return " | ".join(e)


def _log_agent(log) -> str:
    """Best-effort agent name for a whole log (task name fallback)."""
    task = getattr(getattr(log, "eval", None), "task", "") or ""
    prefix = "channel_parity-"
    return task[len(prefix) :] if task.startswith(prefix) else task


def _rows(log) -> list[dict]:
    rows: list[dict] = []
    log_agent = _log_agent(log)
    for sample in log.samples or []:
        scores = sample.scores or {}
        diff = scores.get("channel_diff")
        meta = (diff.metadata if diff else None) or {}
        details = meta.get("details") or []
        agg_case = meta.get("agreement") or {}
        channels = (sample.metadata or {}).get("channels") or {}
        agent = channels.get("agent") or log_agent

        # per-turn quality lookup (turn number -> {direct_ok, teams_ok})
        quality = scores.get("quality")
        q_by_turn: dict = {}
        if quality is not None and quality.value != "N":
            for qt in (quality.metadata or {}).get("turns", []):
                q_by_turn[qt.get("turn")] = qt

        for idx, det in enumerate(details):
            turn_no = det.get("turn", idx + 1)
            layers = det.get("layers") or {}
            turn_agg = det.get("agg") or agg_case
            direct_text = det.get("direct_text", "")
            teams_text = det.get("teams_text", "")

            passed = turn_agg.get("passed")
            if passed is None:  # fall back to the sample-level verdict (fakes / legacy logs)
                passed = bool(diff and diff.value == "C")

            qt = q_by_turn.get(turn_no)
            if qt is not None:
                q = "pass" if (qt.get("direct_ok") and qt.get("teams_ok")) else "fail"
                q += (
                    f" (direct={'C' if qt.get('direct_ok') else 'I'},"
                    f"teams={'C' if qt.get('teams_ok') else 'I'})"
                )
            else:
                q = "—"

            if det.get("direct") is not None and det.get("teams") is not None:
                structured_html = _structured_breakdown(
                    [{"direct": det["direct"], "teams": det["teams"]}]
                )
            else:
                structured_html = ""

            rows.append(
                {
                    "id": str(sample.id),
                    "turn": str(turn_no),
                    "agent": agent,
                    "prompt": det.get("query") or meta.get("prompt") or str(sample.input),
                    "expected": det.get("expected", ""),
                    "direct": direct_text,
                    "teams": teams_text,
                    "normalized": _layer(layers, "normalized"),
                    "semantic": _layer(layers, "semantic"),
                    "structured": _layer(layers, "structured"),
                    "agreement": f"{turn_agg.get('agreement', 0):.2f}" if turn_agg else "—",
                    "result": "MATCH" if passed else "DIFFER",
                    "quality": q,
                    # HTML-only extras (ignored by the CSV writer):
                    "diff_html": _word_diff(direct_text, teams_text),
                    "layers_summary": _layers_summary(turn_agg),
                    "structured_html": structured_html,
                    "errors": _errors(det),
                }
            )
    return rows


def rows_for_logs(locations: list[str]) -> list[dict]:
    """Concatenate report rows across several eval logs (one per agent in a multi-agent run).

    Each row already carries its own ``agent`` field, so the combined table stays attributable.
    Logs that can't be read are skipped (a broken agent shouldn't hide the others).
    """
    out: list[dict] = []
    for loc in locations:
        if not loc:
            continue
        try:
            log = read_eval_log(loc)
        except Exception as exc:  # pragma: no cover - depends on filesystem state
            logger.warning(f"Could not read eval log {loc}: {exc}")
            continue
        out.extend(_rows(log))
    return out


def _write_csv(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _write_html(rows: list[dict], path: Path, title: str) -> None:
    matched = sum(1 for r in rows if r["result"] == "MATCH")
    total = len(rows)
    differ = total - matched

    def cell(text: str) -> str:
        return f"<td><div class='wrap'>{html.escape(str(text))}</div></td>"

    def flag(v: str) -> str:
        cls = {"yes": "ok", "no": "bad", "—": "na"}.get(v, "na")
        return f"<td class='{cls} center'>{v}</td>"

    body = []
    for r in rows:
        result_cls = "ok" if r["result"] == "MATCH" else "bad"
        struct = f"<div class='chips'>{r['structured_html']}</div>" if r["structured_html"] else ""
        err = f"<div class='err'>{html.escape(r['errors'])}</div>" if r["errors"] else ""
        body.append(
            f"<tr data-result='{r['result']}'>"
            f"<td class='mono'>{html.escape(r['id'])}</td>"
            f"<td class='center small'>{html.escape(str(r.get('turn', '')))}</td>"
            f"<td class='small'>{html.escape(str(r.get('agent', '')))}</td>"
            f"{cell(r['prompt'])}"
            f"{cell(r.get('expected', ''))}"
            f"<td><div class='wrap'>{html.escape(r['direct'])}</div></td>"
            f"<td><div class='wrap'>{html.escape(r['teams'])}</div>{struct}</td>"
            f"<td><div class='wrap diff'>{r['diff_html']}</div>{err}</td>"
            f"{flag(r['normalized'])}{flag(r['semantic'])}{flag(r['structured'])}"
            f"<td class='center small'>{html.escape(r['layers_summary'])}"
            f"<br><b>{r['agreement']}</b></td>"
            f"<td class='center {result_cls}'>{r['result']}</td>"
            f"<td class='center'>{html.escape(r['quality'])}</td>"
            "</tr>"
        )

    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:24px;color:#1b1b1f}}
 h1{{font-size:20px}} .summary{{margin:8px 0 6px;font-size:15px}}
 .legend{{margin-left:12px;font-size:12px;color:#555}}
 .filters{{margin:10px 0 16px}}
 .filters button{{font:inherit;font-size:13px;padding:5px 12px;margin-right:6px;
   border:1px solid #c8c8ce;border-radius:6px;background:#fafafc;cursor:pointer}}
 .filters button:hover{{background:#eee}}
 table{{border-collapse:collapse;width:100%;font-size:13px}}
 th,td{{border:1px solid #d8d8de;padding:8px;vertical-align:top;text-align:left}}
 th{{background:#f3f3f5;position:sticky;top:0;z-index:1}}
 .wrap{{max-width:320px;max-height:160px;overflow:auto;white-space:pre-wrap}}
 .diff{{max-width:360px}}
 .mono{{font-family:ui-monospace,Menlo,monospace;white-space:nowrap}}
 .center{{text-align:center}} .small{{font-size:11px;color:#444}}
 .ok{{color:#0a7d27;font-weight:600}} .bad{{color:#c01c28;font-weight:600}} .na{{color:#8a8a8f}}
 .del{{color:#c01c28;background:#fde8ea;text-decoration:line-through}}
 .ins{{color:#0a7d27;background:#e6f4ea}}
 .chips{{margin-top:6px}}
 .chip{{display:inline-block;font-size:11px;padding:1px 6px;margin:1px;
   border-radius:10px;border:1px solid}}
 .chip.ok{{border-color:#9ad3a8;background:#e6f4ea}}
 .chip.bad{{border-color:#e6a6ad;background:#fde8ea}}
 .err{{margin-top:6px;font-size:11px;color:#c01c28}}
</style></head><body>
<h1>{html.escape(title)}</h1>
<div class="summary">Channels matched on <b>{matched}/{total}</b> turn responses.
 <span class="legend"><span class='del'>red</span> = only in Direct ·
 <span class='ins'>green</span> = only in Teams</span></div>
<div class="filters">
 <button onclick="flt('all')">All ({total})</button>
 <button onclick="flt('DIFFER')">Differences ({differ})</button>
 <button onclick="flt('MATCH')">Matches ({matched})</button>
</div>
<table><thead><tr>
<th>id</th><th>turn</th><th>agent</th><th>prompt</th><th>expected</th>
<th>Direct</th><th>Teams</th><th>Difference</th>
<th>norm</th><th>sem</th><th>struct</th><th>agreement</th><th>result</th><th>quality</th>
</tr></thead><tbody>
{"".join(body)}
</tbody></table>
<script>
function flt(k){{document.querySelectorAll('tr[data-result]').forEach(function(r){{
  r.style.display=(k==='all'||r.dataset.result===k)?'':'none';}});}}
</script>
</body></html>"""
    path.write_text(doc)


@app.command()
def main(
    log: str = typer.Option(None, help="Path to an Inspect .eval log. Default: latest in ./logs."),
    log_dir: str = typer.Option("./logs", help="Where to look for logs when --log is omitted."),
    out_dir: str = typer.Option("reports", help="Output directory for CSV/HTML."),
    open_report: bool = typer.Option(False, "--open", help="Open the HTML report in a browser."),
) -> None:
    if log:
        log_path = log
    else:
        logs = list_eval_logs(log_dir)
        if not logs:
            raise typer.Exit(code=1)
        log_path = logs[0].name

    logger.info("Reading log: {}", log_path)
    eval_log = read_eval_log(log_path, resolve_attachments=True)
    rows = _rows(eval_log)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(str(log_path)).stem
    csv_path = out / f"{stem}.csv"
    html_path = out / f"{stem}.html"
    _write_csv(rows, csv_path)
    title = f"Channel parity — {eval_log.eval.task} ({len(rows)} turn responses)"
    _write_html(rows, html_path, title)

    matched = sum(1 for r in rows if r["result"] == "MATCH")
    print(f"Channels matched on {matched}/{len(rows)} turn responses")
    print(f"CSV : {csv_path}")
    print(f"HTML: {html_path}")
    if open_report:
        webbrowser.open(html_path.resolve().as_uri())


if __name__ == "__main__":
    app()
