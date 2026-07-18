"""Task 2 — the citation gate, enforced as a DETERMINISTIC post-Judge filter.

The trust story: the LLM never adjudicates truth — evidence does. A finding may
only reach the review queue if the citations it provides are *actually present*
in the source text/chart. Any fabricated citation, or a finding without the
minimum verifiable evidence for its type, is discarded silently (logged to the
dropped list for the demo, never shown to the clinician as an alert).

This runs in code, not in the prompt: the model is asked for citations, and then
we check them ourselves. That is what makes "no citation, no surface" real.
"""

from __future__ import annotations

import difflib
import json
import re


def _norm(s: str | None) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for robust matching."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()


def _longest_block(a: str, b: str) -> int:
    if not a or not b:
        return 0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sm.find_longest_match(0, len(a), 0, len(b)).size


def verify_span(citation: str | None, source: str, min_chars: int = 20) -> bool:
    """True if `citation` is a real (near-verbatim) span of `source`.

    Citations are often multi-fragment quotes joined by '...'; we accept a match
    on any substantial fragment. A fabricated quote won't share a long
    contiguous run with the source, so the longest-common-block test rejects it.
    """
    if not citation:
        return False
    src = _norm(source)
    fragments = re.split(r"\.\.\.|…", citation)
    for frag in fragments:
        c = _norm(frag)
        if not c:
            continue
        if len(c) < min_chars:
            # short fragment: require an exact (but non-trivial) substring
            if len(c) >= 8 and c in src:
                return True
            continue
        if c in src or _longest_block(c, src) >= min_chars:
            return True
    whole = _norm(citation)
    return _longest_block(whole, src) >= min_chars


def verify_fhir(citation: str | None, chart: dict, min_chars: int = 15) -> bool:
    """True if the FHIR citation references content that exists in the parsed
    chart (a real field name, med label, condition, etc.)."""
    if not citation:
        return False
    blob = _norm(json.dumps(chart))
    return _longest_block(_norm(citation), blob) >= min_chars


# Minimum verified anchors required per discrepancy type. Absence-type findings
# (A note-hallucination, B dropped-disclosure) legitimately cannot cite the
# source the thing is *missing* from, so we require the present-side anchor plus
# a self-verification pass rather than all three.
def gate_finding(finding: dict, transcript: str, note: str,
                 chart: dict) -> tuple[bool, str | None]:
    """Return (keep, drop_reason). keep=True only if evidence checks out."""
    tv = verify_span(finding.get("transcript_citation"), transcript)
    nv = verify_span(finding.get("note_citation"), note)
    fv = verify_fhir(finding.get("fhir_citation"), chart)

    # (1) Any citation the model PROVIDED must verify against its source.
    #     A provided-but-unverifiable citation means the verifier fabricated it.
    for name, provided, ok in (
        ("transcript", finding.get("transcript_citation"), tv),
        ("note", finding.get("note_citation"), nv),
        ("fhir", finding.get("fhir_citation"), fv),
    ):
        if provided and not ok:
            return False, f"fabricated {name} citation (not found in source)"

    have = {"transcript": tv, "note": nv, "fhir": fv}
    t = finding.get("type")

    # (2) Type-specific minimum present anchors.
    if t == "C":
        if not have["fhir"]:
            return False, "type C requires a verified FHIR/chart anchor"
        if not (have["transcript"] or have["note"]):
            return False, "type C requires a verified transcript or note anchor"
    elif t == "A":
        if not have["note"]:
            return False, "type A requires the hallucinated note span, verified"
    elif t == "B":
        if not have["transcript"]:
            return False, "type B requires the transcript disclosure, verified"
    elif t == "D":
        if not (have["transcript"] or have["note"]):
            return False, "type D requires a verified transcript or note anchor"
    elif t == "E":
        if not have["note"]:
            return False, "type E requires a verified note (A&P) anchor"

    # (3) No finding surfaces with zero verifiable evidence.
    if not any(have.values()):
        return False, "no verifiable evidence anchor"

    # (4) Absence claims must show their self-verification pass.
    if t in ("A", "B") and not (finding.get("self_verification") or "").strip():
        return False, "absence claim without a self-verification pass"

    return True, None


def enforce(findings: list[dict], transcript: str, note: str,
            chart: dict) -> tuple[list[dict], list[dict]]:
    """Split findings into (kept, dropped). Dropped carry a `_drop_reason`."""
    kept, dropped = [], []
    for f in findings:
        keep, reason = gate_finding(f, transcript, note, chart)
        if keep:
            kept.append(f)
        else:
            dropped.append({**f, "_drop_reason": reason})
    return kept, dropped
