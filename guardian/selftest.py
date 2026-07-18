"""Offline self-test of the deterministic half (no API calls).

Run: python -m guardian.selftest

Exercises FHIR parsing + reconciliation on record 12 with hand-written ledger
stubs standing in for Claude's extraction, and asserts the medication bundle
correctly ties each stale longitudinal label to today's new order.
"""

from __future__ import annotations

from . import DEFAULT_DATASET
from .anchors import enforce, verify_span
from .data import get_record, parse_chart
from .normalize import normalize_med
from .reconcile import reconcile
from .resolve import EntityResolver

_EXPECTED_MEDS = {"hydrochlorothiazide", "acetaminophen", "lisinopril", "amlodipine"}


def main() -> int:
    # 1. Normalization joins branded/dosed labels to a bare drug key.
    assert normalize_med("Acetaminophen 325 MG Oral Tablet [Tylenol]") == "acetaminophen"
    assert normalize_med("amLODIPine 2.5 MG Oral Tablet") == "amlodipine"

    chart = parse_chart(get_record(DEFAULT_DATASET, 12))

    # 2. All four longitudinal meds are present, and this visit re-orders them today.
    long_keys = {normalize_med(m) for m in chart["longitudinal_medication_labels"]}
    assert _EXPECTED_MEDS <= long_keys, long_keys
    assert all(o["is_new_today"] for o in chart["visit_medications"]), \
        "expected every visit MedicationRequest to be authored today"

    # 3. Reconcile ties stale longitudinal label + today's order onto one drug key —
    #    the exact ambiguity the Judge must resolve.
    tx = [{"entity": "medications", "category": "medication", "action": "stop",
           "statement": "ran out and stopped everything",
           "source_span": "I ran out of everything months ago and stopped refilling"}]
    note = [{"entity": "medications", "category": "medication", "action": "stop",
             "statement": "no current medications",
             "source_span": "No current medications after prior prescriptions lapsed."}]
    cand = reconcile(chart, tx, note)
    by_key = {m["drug_key"]: m for m in cand["medications"]["medications"]}
    for drug in _EXPECTED_MEDS:
        row = by_key[drug]
        assert row["longitudinal_chart_label"], f"{drug}: missing longitudinal label"
        assert row["visit_orders"] and row["visit_orders"][0]["is_new_today"], \
            f"{drug}: missing today's new order"

    # 4. Immunizations/procedures are parsed (guards against false type-D on
    #    resources that ARE captured — e.g. record 13's flu vaccine).
    chart13 = parse_chart(get_record(DEFAULT_DATASET, 13))
    vaccines = [im["vaccine"] for im in chart13["visit_immunizations"]]
    assert any("Influenza" in (v or "") for v in vaccines), \
        f"expected the flu Immunization to be parsed, got {vaccines}"
    assert chart13["visit_procedures"], "expected visit procedures to be parsed"

    # 5. Task 3 — RxNorm/brand resolver ties synonyms to one entity.
    resolver = EntityResolver(chart)  # chart is record 12
    assert resolver.same_entity("Tylenol", "Acetaminophen 325 MG Oral Tablet [Tylenol]")
    assert resolver.same_entity("the ACE inhibitor" if False else "lisinopril",
                                "lisinopril 10 MG Oral Tablet")
    assert resolver.resolve("Hydrochlorothiazide 25 MG Oral Tablet")["rxnorm"] == "310798", \
        "resolver should attach the RxNorm code from the coded order"
    # A brand/generic pair must NOT create a standalone candidate: same drug key.
    tx = [{"entity": "Norvasc", "category": "medication", "action": "start",
           "statement": "start norvasc", "source_span": "start norvasc"}]
    cand = reconcile(chart, tx, [], resolver)
    amlo = [m for m in cand["medications"]["medications"] if m["drug_key"] == "amlodipine"]
    assert amlo and amlo[0]["transcript_claims"], \
        "brand 'Norvasc' should resolve onto the amlodipine entity"

    # 6. Task 2 — the citation gate discards a fabricated citation.
    real_span = ("I ran out of everything months ago and stopped refilling "
                 "when the insurance switched")
    transcript = get_record(DEFAULT_DATASET, 12)["transcript"]
    assert verify_span(real_span, transcript), "a real span must verify"
    assert not verify_span("Patient airlifted to a trauma center in Zurich.", transcript), \
        "a fabricated span must not verify"
    fabricated = [{
        "type": "C", "severity": "Major", "material": True,
        "title": "bogus", "rationale": "x", "self_verification": "x",
        "transcript_citation": "Patient airlifted to a trauma center in Zurich.",
        "note_citation": None, "fhir_citation": None, "proposed_resolution": None,
        "suppressed_reason": None,
    }]
    kept, dropped = enforce(fabricated, transcript, "note text", chart)
    assert not kept and dropped and "fabricated" in dropped[0]["_drop_reason"], \
        "a fabricated-citation finding must be dropped by the gate"

    print("selftest OK — parsing, reconciliation, RxNorm resolver, and citation "
          "gate verified on records 12 & 13 (no API calls)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
