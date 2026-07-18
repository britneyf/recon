"""recon backend — turns a Guardian audit into a clinician-facing view and
serves it over a tiny stdlib HTTP server (no third-party deps).

  python -m recon            # serve on http://localhost:8000
  python -m recon --build    # precompute + cache the demo scenarios, then exit

The view resolves each finding's free-text citations back to specific transcript
turns and chart medications, so the UI can highlight the evidence on click.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from guardian import DEFAULT_DATASET
from guardian.anchors import _longest_block, _norm
from guardian.data import get_record, parse_chart
from guardian.normalize import normalize_med
from guardian.pipeline import audit_record

_HERE = os.path.dirname(__file__)
_CACHE = os.path.join(_HERE, "cache")
_INDEX = os.path.join(_HERE, "index.html")

_SPEAKER = re.compile(r"^(DR|PT|NURSE|RN|MA|FAMILY|CAREGIVER|INTERPRETER):\s*(.*)$")


# --------------------------------------------------------------------------- #
# Source parsing + citation resolution
# --------------------------------------------------------------------------- #
def parse_turns(transcript: str) -> list[dict]:
    turns: list[dict] = []
    cur: dict | None = None
    for line in transcript.splitlines():
        m = _SPEAKER.match(line.strip())
        if m:
            who = m.group(1).lower()
            who = who if who in ("dr", "pt", "nurse", "family") else "other"
            cur = {"n": len(turns) + 1, "who": who, "raw": m.group(1), "t": m.group(2)}
            turns.append(cur)
        elif cur and line.strip():
            cur["t"] += " " + line.strip()
    return turns


def patient_name(record: dict) -> str:
    try:
        nm = record["patient_context"]["patient"]["name"][0]
        given = " ".join(nm.get("given", []))
        return f"{nm.get('family', '')}, {given}".strip(", ")
    except Exception:
        return "Patient"


def chart_meds(chart: dict) -> list[dict]:
    """The chart's active medication list (longitudinal), flagged for whether
    this visit (re)ordered each — the surface med reconciliation runs on."""
    ordered_keys = {normalize_med(o["text"]) for o in chart.get("visit_medications", [])
                    if o.get("is_new_today")}
    rx_by_key = {normalize_med(o["text"]): o.get("rxnorm")
                 for o in chart.get("visit_medications", [])}
    meds = []
    for label in chart.get("longitudinal_medication_labels", []):
        key = normalize_med(label)
        meds.append({
            "label": label,
            "key": key,
            "rx": rx_by_key.get(key),
            "ordered_today": key in ordered_keys,
        })
    return meds


def _match_turns(citation: str | None, turns: list[dict], min_chars: int = 14) -> list[int]:
    if not citation:
        return []
    c = _norm(citation)
    return [t["n"] for t in turns if _longest_block(c, _norm(t["t"])) >= min_chars]


def _match_chips(citation: str | None, meds: list[dict]) -> list[str]:
    if not citation:
        return []
    blob = _norm(citation)
    return [m["key"] for m in meds if m["key"] and m["key"] in blob]


def audit_to_view(record: dict, index: int, result: dict) -> dict:
    chart = parse_chart(record)
    turns = parse_turns(record["transcript"])
    meds = chart_meds(chart)

    findings = []
    for f in result.get("surfaced", []):
        turn_ids = _match_turns(f.get("transcript_citation"), turns)
        chip_keys = _match_chips(f.get("fhir_citation"), meds)
        findings.append({**f, "turn_ids": turn_ids, "chip_keys": chip_keys})

    suppressed = [{"title": f.get("title") or f.get("_group", "candidate"),
                   "reason": f.get("suppressed_reason")}
                  for f in result.get("suppressed", []) if f.get("suppressed_reason")]
    dropped = [{"title": f.get("title", "candidate"), "type": f.get("type"),
                "reason": f.get("_drop_reason")}
               for f in result.get("dropped", [])]

    return {
        "record": index,
        "title": result.get("visit_title"),
        "patient": patient_name(record),
        "visit_type": chart.get("visit_type"),
        "date": (chart.get("visit_date") or "")[:10],
        "transcript": turns,
        "note": record["note"],
        "meds": meds,
        "trace": result.get("trace", []),
        "findings": findings,
        "suppressed": suppressed,
        "dropped": dropped,
        "clean": not findings,
    }


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def _cache_key(index: int, injects: list[str]) -> str:
    if not injects:
        return f"rec{index}_base"
    h = hashlib.sha1("||".join(injects).encode()).hexdigest()[:8]
    return f"rec{index}_{h}"


def _apply_injects(record: dict, injects: list[str]) -> dict:
    if not injects:
        return record
    rec = json.loads(json.dumps(record))
    rec["note"] = rec["note"].rstrip() + "\n\n" + \
        "\n".join(f"- {s}" for s in injects) + "\n"
    return rec


def get_view(index: int, injects: list[str], dataset: str = DEFAULT_DATASET,
             force: bool = False, write: bool = True) -> dict:
    """Return a cached view, or run the pipeline live.

    force=True  -> always run the pipeline (ignore any cache)
    write=False -> do not persist the result (used for on-demand LIVE runs so a
                   fresh/possibly-variant run never clobbers the curated cache)
    """
    os.makedirs(_CACHE, exist_ok=True)
    path = os.path.join(_CACHE, _cache_key(index, injects) + ".json")
    if os.path.exists(path) and not force:
        with open(path) as f:
            return json.load(f)
    base = get_record(dataset, index)
    record = _apply_injects(base, injects)
    result = audit_record(index, dataset=dataset, verbose=True, record=record)
    view = audit_to_view(record, index, result)
    view["injected"] = injects
    view["live"] = not write   # flag genuinely-live (uncached) results in the UI
    if write:
        with open(path, "w") as f:
            json.dump(view, f, indent=2)
    return view


# --------------------------------------------------------------------------- #
# Demo scenarios (precomputed so the live demo is instant + deterministic)
# --------------------------------------------------------------------------- #
DEMO = [
    {"index": 10, "injects": [], "label": "The catch — stale med list (record 10)"},
    {"index": 12, "injects": [], "label": "The restraint — clean initiation (record 12)"},
    {"index": 10, "injects": ["Started warfarin 5 mg daily for atrial fibrillation."],
     "label": "Injected hallucination — warfarin (record 10)"},
    {"index": 12, "injects": ["Started atorvastatin 20 mg daily for hyperlipidemia.",
                              "Referred to cardiology for further evaluation."],
     "label": "Injected hallucination — atorvastatin + referral (record 12)"},
]


def build_cache(force: bool = True) -> None:
    for d in DEMO:
        print(f"\n=== building: {d['label']} ===", flush=True)
        get_view(d["index"], d["injects"], force=force)
    print("\nDemo cache built.")


def refresh_cache(dataset: str = DEFAULT_DATASET) -> None:
    """Re-derive the DETERMINISTIC parts of each cached view (chart med list,
    citation→turn/chip resolution) from the record — without re-running the LLM.
    Fixes deterministic bugs (e.g. a normalizer change) while preserving the
    verified findings, so the demo results don't drift on a rebuild."""
    if not os.path.isdir(_CACHE):
        print("no cache to refresh")
        return
    for fn in sorted(os.listdir(_CACHE)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(_CACHE, fn)
        with open(path) as f:
            view = json.load(f)
        record = _apply_injects(get_record(dataset, view["record"]),
                                view.get("injected", []))
        chart = parse_chart(record)
        turns = parse_turns(record["transcript"])
        meds = chart_meds(chart)
        view["meds"] = meds
        for finding in view.get("findings", []):
            finding["turn_ids"] = _match_turns(finding.get("transcript_citation"), turns)
            finding["chip_keys"] = _match_chips(finding.get("fhir_citation"), meds)
        with open(path, "w") as f:
            json.dump(view, f, indent=2)
        print(f"refreshed {fn}")
    print("Cache refreshed (deterministic parts only; findings preserved).")


# --------------------------------------------------------------------------- #
# Server
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            with open(_INDEX, "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if parsed.path == "/api/scenarios":
            body = json.dumps({"demo": DEMO}).encode()
            return self._send(200, body, "application/json")
        if parsed.path == "/api/audit":
            q = parse_qs(parsed.query)
            index = int(q.get("record", ["10"])[0])
            injects = q.get("inject", [])
            live = q.get("live", ["0"])[0] == "1"        # run fresh, don't cache
            force = live or q.get("force", ["0"])[0] == "1"
            try:
                view = get_view(index, injects, force=force, write=not live)
                return self._send(200, json.dumps(view).encode(), "application/json")
            except Exception as e:  # surface errors to the UI
                return self._send(500, json.dumps({"error": str(e)}).encode(),
                                  "application/json")
        self._send(404, b"not found", "text/plain")


def serve(port: int = 8000) -> None:
    os.makedirs(_CACHE, exist_ok=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"recon → http://localhost:{port}   (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recon")
    parser.add_argument("--build", action="store_true",
                        help="precompute + cache the demo scenarios, then exit")
    parser.add_argument("--refresh", action="store_true",
                        help="re-derive deterministic view parts in the cache "
                             "(no LLM re-run), then exit")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if args.build:
        build_cache()
        return 0
    if args.refresh:
        refresh_cache()
        return 0
    serve(args.port)
    return 0
