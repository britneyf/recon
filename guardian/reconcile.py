"""RECONCILE — the proposer. Align entries across the three sources and emit
noisy candidate mismatch groups. Deliberately over-inclusive: the Judge does
the clinical-judgment filtering. This is plain code, no LLM.

We emit three candidate groups that map onto the taxonomy spine:
  - medications      -> types C (stale chart) and D (uncaptured order), plus med-A
  - dropped_disclosures -> type B
  - note_assertions  -> type A (note hallucination)
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .resolve import EntityResolver


def _claim_keys(resolver: EntityResolver, claim: dict) -> set[str]:
    """Joinable keys for an extracted claim (RxNorm/brand-unified)."""
    return resolver.keys_for(claim.get("entity", ""))


def build_medication_bundle(resolver: EntityResolver, chart: dict,
                            transcript_claims: list[dict],
                            note_claims: list[dict]) -> dict[str, Any]:
    """One holistic view of the medication picture, keyed by drug name.

    Bundling every med into a single candidate lets the Judge emit ONE
    'reconcile the med list' card rather than N per-drug cards, and reason about
    stale-vs-new-start in one pass (build spec §5 demo).
    """
    table: dict[str, dict] = defaultdict(lambda: {
        "longitudinal_chart_label": None,   # carried active on the longitudinal record
        "visit_orders": [],                 # this visit's MedicationRequest activity
        "transcript_claims": [],
        "note_claims": [],
    })

    for label in chart.get("longitudinal_medication_labels", []):
        for k in resolver.keys_for(label):
            table[k]["longitudinal_chart_label"] = label

    for order in chart.get("visit_medications", []):
        for k in resolver.keys_for(order.get("text", "")):
            table[k]["visit_orders"].append(order)

    for claim in transcript_claims:
        if claim.get("category") == "medication":
            for k in _claim_keys(resolver, claim):
                table[k]["transcript_claims"].append(claim)

    for claim in note_claims:
        if claim.get("category") == "medication":
            for k in _claim_keys(resolver, claim):
                table[k]["note_claims"].append(claim)

    # Collapse to a clean per-drug list.
    meds = []
    for key, v in table.items():
        meds.append({"drug_key": key, **v})
    meds.sort(key=lambda m: m["drug_key"])
    return {"medications": meds}


def build_disclosure_candidates(resolver: EntityResolver,
                                transcript_claims: list[dict],
                                note_claims: list[dict]) -> list[dict]:
    """Transcript patient-reported / safety / condition items and whether the
    note appears to cover them. Type B lives here."""
    note_by_key: dict[str, list[dict]] = defaultdict(list)
    for c in note_claims:
        for k in _claim_keys(resolver, c):
            note_by_key[k].append(c)

    out = []
    for c in transcript_claims:
        if c.get("category") in ("patient_reported", "safety", "condition"):
            keys = _claim_keys(resolver, c)
            covered = any(k in note_by_key for k in keys)
            out.append({
                "transcript_claim": c,
                "appears_in_note": covered,
                "note_matches": [m for k in keys for m in note_by_key.get(k, [])],
            })
    return out


def build_note_assertion_candidates(resolver: EntityResolver, chart: dict,
                                    transcript_claims: list[dict],
                                    note_claims: list[dict]) -> list[dict]:
    """Note claims (meds / orders / conditions with a start/order action) and
    whether the transcript or FHIR appears to support them. Type A lives here."""
    tx_by_key: dict[str, list[dict]] = defaultdict(list)
    for c in transcript_claims:
        for k in _claim_keys(resolver, c):
            tx_by_key[k].append(c)

    fhir_keys: set[str] = set()
    for order in chart.get("visit_medications", []):
        fhir_keys |= resolver.keys_for(order.get("text", ""))
    for cond in chart.get("visit_conditions", []):
        fhir_keys |= resolver.keys_for(cond.get("text", ""))
    for label in chart.get("longitudinal_medication_labels", []):
        fhir_keys |= resolver.keys_for(label)

    out = []
    for c in note_claims:
        if c.get("category") in ("medication", "order", "condition") and \
                c.get("action") in ("start", "order", "mention", "continue"):
            keys = _claim_keys(resolver, c)
            out.append({
                "note_claim": c,
                "supported_in_transcript": any(k in tx_by_key for k in keys),
                "supported_in_fhir": any(k in fhir_keys for k in keys),
                "transcript_matches": [m for k in keys for m in tx_by_key.get(k, [])],
            })
    return out


def reconcile(chart: dict, transcript_claims: list[dict],
              note_claims: list[dict],
              resolver: EntityResolver | None = None) -> dict[str, Any]:
    """Produce the three candidate groups the Judge will evaluate."""
    resolver = resolver or EntityResolver(chart)
    return {
        "medications": build_medication_bundle(
            resolver, chart, transcript_claims, note_claims),
        "dropped_disclosures": build_disclosure_candidates(
            resolver, transcript_claims, note_claims),
        "note_assertions": build_note_assertion_candidates(
            resolver, chart, transcript_claims, note_claims),
    }
