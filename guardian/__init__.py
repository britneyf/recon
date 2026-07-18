"""Reconciliation Guardian — a faithfulness auditor for ambient clinical notes.

Cross-references the visit transcript, the generated note, and the structured
FHIR chart, and surfaces only the discrepancies a clinician would act on.

Pipeline: EXTRACT (Claude) + parse FHIR -> RECONCILE (code) -> JUDGE (Claude).
"""

DEFAULT_DATASET = "data/synthetic-ambient-fhir-25.jsonl"
DEFAULT_MODEL = "claude-opus-4-8"
