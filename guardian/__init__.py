"""Reconciliation Guardian — conversation-to-chart reconciliation for ambient notes.

Cross-references the visit transcript, the generated note, and the structured
FHIR chart, and surfaces only the discrepancies worth clinician review. It
proposes clarification; it never resolves a conflict or writes to the chart.

Pipeline: EXTRACT (Claude) + parse FHIR -> RECONCILE (code) -> JUDGE (Claude).
"""

DEFAULT_DATASET = "data/synthetic-ambient-fhir-25.jsonl"
DEFAULT_MODEL = "claude-opus-4-8"
