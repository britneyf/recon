"""Deterministic discrepancy-surface screener (no API calls).

Ranks all records by cheap structural signals that indicate where a real
transcript/note/FHIR discrepancy is *likely* to live, so the expensive LLM
Guardian can be pointed at the few strongest candidates instead of all 25.

Signals per record:
  - stale_surface : meds carried active on the longitudinal list but NOT
                    (re)ordered this visit -> where type-C stale-chart lives,
                    IF the visit also establishes a stop/lapse.
  - stop_language : transcript mentions of stopping/lapsing a med -> the other
                    half a real type-C needs.
  - reordered_all : True when every longitudinal med is re-ordered today (the
                    record-12 trap: looks stale, actually all new starts).
"""

from __future__ import annotations

import re

from . import DEFAULT_DATASET
from .data import load_records, parse_chart
from .normalize import normalize_med

_STOP_PATTERNS = [
    r"ran out", r"stopped (?:taking|refill)", r"stopped\b", r"quit\b",
    r"no longer (?:taking|on)", r"haven'?t been taking", r"off (?:my|the|it)",
    r"not taking", r"lapsed", r"discontinued", r"gave up on", r"skip(?:ped|ping)",
]
_STOP_RE = re.compile("|".join(_STOP_PATTERNS), re.IGNORECASE)


def screen_record(record: dict) -> dict:
    chart = parse_chart(record)
    long_meds = {normalize_med(m): m for m in chart["longitudinal_medication_labels"]}
    ordered_today = {normalize_med(o["text"]) for o in chart["visit_medications"]
                     if o.get("is_new_today")}
    stale = {k: v for k, v in long_meds.items() if k not in ordered_today}

    stop_hits = _STOP_RE.findall(record.get("transcript", ""))

    return {
        "title": chart["visit_title"],
        "n_long_meds": len(long_meds),
        "n_ordered_today": len(ordered_today),
        # meds on the active list that today's visit did NOT touch:
        "stale_surface": sorted(stale.keys()),
        "n_stale_surface": len(stale),
        "reordered_all": bool(long_meds) and set(long_meds) <= ordered_today,
        "stop_language": len(stop_hits),
        "n_visit_conditions": len(chart["visit_conditions"]),
    }


def _score(s: dict) -> tuple:
    # Prioritize: a real stale-chart needs BOTH untouched active meds AND
    # transcript stop-language. Records where everything was reordered today
    # (the record-12 trap) score low.
    combo = min(s["n_stale_surface"], 3) * (2 if s["stop_language"] else 1)
    return (combo, s["n_stale_surface"], s["stop_language"])


def screen_all(dataset: str = DEFAULT_DATASET) -> list[dict]:
    rows = []
    for i, rec in enumerate(load_records(dataset)):
        s = screen_record(rec)
        s["index"] = i
        rows.append(s)
    rows.sort(key=_score, reverse=True)
    return rows


def main() -> int:
    rows = screen_all()
    print(f"{'idx':>3}  {'stale':>5} {'stop':>4} {'reordAll':>8}  title")
    print("-" * 78)
    for s in rows:
        flag = "  <-- all reordered (rec-12 trap)" if s["reordered_all"] else ""
        print(f"{s['index']:>3}  {s['n_stale_surface']:>5} {s['stop_language']:>4} "
              f"{str(s['reordered_all']):>8}  {(s['title'] or '')[:44]}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
