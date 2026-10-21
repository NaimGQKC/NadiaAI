"""Track lead-gen engine health over time.

Two layers:
  • Cumulative DB snapshots — the true state of the engine right now (total
    leads, tiers, heir completeness, notarial yield, Zaragoza coverage, contact
    hits). Appended to logs/metrics_history.csv each time this runs.
  • Per-run history — parsed from logs/run_summary_*.json (the daily pipeline's
    own output) so we keep the trend even for runs before this tool existed.

Both are rendered into a human-readable docs/METRICS.md after every snapshot.

    python tools/metrics_snapshot.py            # snapshot now + rebuild METRICS.md
    python tools/metrics_snapshot.py --label "after LLM fix"
    python tools/metrics_snapshot.py --no-snapshot   # just rebuild the doc
"""

import csv
import glob
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from nadia_ai.config import DB_PATH

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "logs" / "metrics_history.csv"
DOC_PATH = ROOT / "docs" / "METRICS.md"

# (key, header, SQL predicate over `leads`) — the engine's vital signs.
METRICS = [
    ("total", "Total", "1=1"),
    ("tier_a", "TierA", "tier='A'"),
    ("tier_b", "TierB", "tier='B'"),
    ("named_heir", "Named heir", "COALESCE(heir_name,'')!=''"),
    ("multi_heir", "Multi-heir", "heir_names_json LIKE '%,%'"),
    ("with_address", "w/ address", "COALESCE(direccion,'')!=''"),
    ("notarial", "Notarial", "sources LIKE '%Notarial%'"),
    ("notarial_heir", "Notarial+heir", "sources LIKE '%Notarial%' AND COALESCE(heir_name,'')!=''"),
    ("zaragoza", "Zaragoza", "region='Zaragoza' OR localidad LIKE '%aragoza%'"),
    ("contact_searched", "Contact searched", "COALESCE(contact_enriched_at,'')!=''"),
    ("contact_found", "Contact found",
     "COALESCE(contact_phone,'')!='' OR COALESCE(contact_email,'')!='' OR COALESCE(contact_profile_url,'')!=''"),
    ("extract_pending", "Extract pending", "ai_extraction_done=0"),
]


def snapshot(conn: sqlite3.Connection) -> dict:
    out = {}
    for key, _hdr, pred in METRICS:
        out[key] = conn.execute(f"SELECT COUNT(*) k FROM leads WHERE {pred}").fetchone()[0]
    # Derived: notarial heir-extraction yield %
    out["notarial_yield"] = round(100 * out["notarial_heir"] / out["notarial"], 1) if out["notarial"] else 0.0
    return out


def append_snapshot(row: dict, label: str) -> None:
    CSV_PATH.parent.mkdir(exist_ok=True)
    cols = ["timestamp", "label"] + [k for k, _, _ in METRICS] + ["notarial_yield"]
    exists = CSV_PATH.exists()
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if not exists:
            w.writeheader()
        w.writerow({"timestamp": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"), "label": label, **row})


def _load_run_summaries() -> list[dict]:
    rows = []
    for p in sorted(glob.glob(str(ROOT / "logs" / "run_summary_*.json"))):
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        rows.append({
            "when": (d.get("started_at") or "")[:16].replace("T", " "),
            "created": d.get("leads_created", 0),
            "merged": d.get("leads_merged", 0),
            "heirs": d.get("heirs_extracted", 0),
            "tier_a": d.get("tier_a", 0),
            "contacts": d.get("contacts_found", 0),
            "boe": d.get("boe_new", 0),
            "esquelas": d.get("esquelas_new", 0),
            "errors": len(d.get("errors", []) or []),
        })
    return rows


def _funnel_section() -> list[str]:
    """Render the conversion funnel from outreach_log (best-effort, never raises).

    This is the pilot's outcome signal: contacted → responded → interested →
    listed. Empty until outreach is logged and outcomes are marked, but it makes
    the funnel a tracked number in the same doc as engine health."""
    lines = ["## Conversion funnel (outreach_log)", ""]
    try:
        conn = sqlite3.connect(DB_PATH, timeout=2)
        try:
            from nadia_ai.outreach_log import funnel, init_outreach_log_schema

            init_outreach_log_schema(conn)
            f = funnel(conn)
        finally:
            conn.close()
    except Exception as e:  # missing DB/table, lock, import — degrade gracefully
        lines.append(f"_Funnel unavailable: {e}_")
        return lines

    if not f["contacted"]:
        lines.append("_No outreach logged yet — funnel is empty._")
        return lines

    lines += [
        "| stage | n | rate |",
        "|---|---|---|",
        f"| contacted | {f['contacted']} | — |",
        f"| responded | {f['responded']} | {f['response_rate'] * 100:.0f}% of contacted |",
        f"| interested | {f['interested']} | {f['interest_rate'] * 100:.0f}% of responded |",
        f"| listed | {f['listed']} | {f['listing_rate'] * 100:.0f}% of interested |",
        f"| opted_out | {f['opted_out']} | — |",
    ]
    return lines


def render_doc() -> None:
    lines = ["# NadiaAI — Engine Metrics", "",
             "_Auto-generated by `tools/metrics_snapshot.py`. Cumulative snapshots are the",
             "true DB state; per-run history is parsed from the pipeline's run summaries._", ""]

    # Cumulative snapshots table
    lines += ["## Cumulative snapshots (DB state over time)", ""]
    if CSV_PATH.exists():
        with CSV_PATH.open(encoding="utf-8") as f:
            snaps = list(csv.DictReader(f))
    else:
        snaps = []
    hdrs = ["timestamp", "label"] + [h for _, h, _ in METRICS] + ["notarial_yield%"]
    keys = ["timestamp", "label"] + [k for k, _, _ in METRICS] + ["notarial_yield"]
    lines.append("| " + " | ".join(hdrs) + " |")
    lines.append("|" + "|".join(["---"] * len(hdrs)) + "|")
    for s in snaps[-30:]:
        lines.append("| " + " | ".join(str(s.get(k, "")) for k in keys) + " |")
    if len(snaps) >= 2:
        first, last = snaps[0], snaps[-1]
        lines += ["", "**Δ since first snapshot:** " + ", ".join(
            f"{h} {int(last[k]) - int(first[k]):+d}"
            for k, _, _ in METRICS for h in [dict((kk, hh) for kk, hh, _ in METRICS)[k]]
            if k in ("named_heir", "multi_heir", "notarial_heir", "tier_a", "zaragoza", "with_address")
        )]

    # Conversion funnel (outreach_log) — the pilot's outcome signal.
    lines += ["", *_funnel_section()]

    # Per-run history — skip no-op runs (interrupted/dup-only) so the trend is legible.
    runs = [r for r in _load_run_summaries()
            if (r["created"] or r["merged"] or r["heirs"] or r["contacts"])]
    lines += ["", "## Per-run history (from run_summary logs — no-op runs hidden)", ""]
    lines.append("| run started | created | merged | heirs | tierA | contacts | BOE | esquelas | errs |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in runs[-40:]:
        lines.append("| {when} | {created} | {merged} | {heirs} | {tier_a} | {contacts} | {boe} | {esquelas} | {errors} |".format(**r))

    DOC_PATH.parent.mkdir(exist_ok=True)
    DOC_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    label = ""
    if "--label" in sys.argv:
        i = sys.argv.index("--label")
        label = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
    if "--no-snapshot" not in sys.argv:
        conn = sqlite3.connect(DB_PATH)
        row = snapshot(conn)
        conn.close()
        append_snapshot(row, label)
        print("snapshot:", {k: row[k] for k in ("total", "named_heir", "multi_heir", "notarial_heir", "notarial_yield", "zaragoza")})
    render_doc()
    print("wrote", DOC_PATH.relative_to(ROOT))


if __name__ == "__main__":
    main()
