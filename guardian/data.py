"""Dataset accessor + deterministic FHIR parsing.

Per the build spec (§8): do NOT LLM-parse the FHIR. It is already structured;
parsing it with a model just adds a hallucination surface to the tool meant to
*catch* hallucinations. Everything here is plain Python.
"""

from __future__ import annotations

import json
from typing import Any


def load_records(path: str) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def get_record(path: str, index: int) -> dict:
    records = load_records(path)
    if not 0 <= index < len(records):
        raise IndexError(f"record index {index} out of range (0..{len(records) - 1})")
    return records[index]


def _rxnorm_code(coding: list[dict]) -> str | None:
    for c in coding or []:
        if "rxnorm" in (c.get("system") or "").lower():
            return c.get("code")
    return None


def _date_prefix(ts: str | None) -> str | None:
    return ts[:10] if ts else None


def parse_chart(record: dict) -> dict[str, Any]:
    """Deterministically extract the structured chart facts we reconcile against.

    The gap between the *longitudinal* med list and *this visit's* new orders is
    exactly where 'stale chart' (type C) and 'uncaptured order' (type D) live
    (build spec §4).
    """
    meta = record.get("metadata", {})
    ls = record["patient_context"]["longitudinal_summary"]
    rr = record["encounter_fhir"].get("related_resources", {})
    visit_date = _date_prefix(meta.get("date"))

    # This visit's medication orders (new activity, keyed to authoredOn).
    visit_medications = []
    for m in rr.get("MedicationRequest", []):
        cc = m.get("medicationCodeableConcept", {})
        authored = m.get("authoredOn")
        visit_medications.append({
            "text": cc.get("text"),
            "rxnorm": _rxnorm_code(cc.get("coding", [])),
            "status": m.get("status"),
            "authoredOn": authored,
            "is_new_today": _date_prefix(authored) == visit_date,
        })

    # This visit's coded conditions.
    visit_conditions = []
    for c in rr.get("Condition", []):
        clinical = None
        for cs in c.get("clinicalStatus", {}).get("coding", []):
            clinical = cs.get("code")
        visit_conditions.append({
            "text": c.get("code", {}).get("text"),
            "clinicalStatus": clinical,
        })

    # A light selection of this visit's observations (vitals/labs) for context.
    visit_observations = []
    for o in rr.get("Observation", []):
        entry = {"text": (o.get("code") or {}).get("text")}
        if "valueQuantity" in o:
            vq = o["valueQuantity"]
            entry["value"] = f"{vq.get('value')} {vq.get('unit', '')}".strip()
        visit_observations.append(entry)

    # Immunizations, procedures, and reports captured this visit. Parsing these
    # is essential: without them the Judge sees a med/immunization "ordered in
    # the room" with no structured resource and raises a FALSE type-D. These are
    # exactly the resources that make an order 'captured'.
    visit_immunizations = []
    for im in rr.get("Immunization", []):
        visit_immunizations.append({
            "vaccine": (im.get("vaccineCode") or {}).get("text"),
            "status": im.get("status"),
            "date": _date_prefix(im.get("occurrenceDateTime")),
        })

    visit_procedures = []
    for p in rr.get("Procedure", []):
        visit_procedures.append({
            "text": (p.get("code") or {}).get("text"),
            "status": p.get("status"),
        })

    visit_reports = [(d.get("code") or {}).get("text")
                     for d in rr.get("DiagnosticReport", [])]

    return {
        "visit_title": meta.get("visit_title"),
        "visit_type": meta.get("visit_type"),
        "visit_date": visit_date,
        # Longitudinal record — meds/conditions the chart carries as active.
        "longitudinal_medication_labels": ls.get("medication_labels", []),
        "longitudinal_condition_labels": ls.get("condition_labels", []),
        # This encounter's structured FHIR activity.
        "visit_medications": visit_medications,
        "visit_conditions": visit_conditions,
        "visit_observations": visit_observations,
        "visit_immunizations": visit_immunizations,
        "visit_procedures": visit_procedures,
        "visit_reports": visit_reports,
    }
