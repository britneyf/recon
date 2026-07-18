"""recon — a clinician-facing review console for the Reconciliation Guardian.

A light-themed web app that audits an ambient note against the transcript and the
structured FHIR chart, and surfaces only the discrepancies worth a clinician's
attention — with every finding cited back to its source, clickably.

Backed by the real Guardian pipeline (guardian.*). Results are cached to
recon/cache/ so the live demo is instant and deterministic.
"""
