"""Orchestration: EXTRACT + parse FHIR -> RECONCILE -> JUDGE -> ranked cards."""

from __future__ import annotations

import re
from typing import Any

from . import DEFAULT_DATASET
from .agent import GuardianAgent
from .anchors import enforce
from .data import get_record, parse_chart
from .reconcile import reconcile
from .resolve import EntityResolver

_SEVERITY_RANK = {"Critical": 0, "Major": 1, "Minor": 2}


def _evidence_tokens(f: dict) -> set[str]:
    """Significant words across a finding's title + citations, for dedup."""
    text = " ".join(filter(None, [
        f.get("title"), f.get("fhir_citation"),
        f.get("note_citation"), f.get("transcript_citation"),
    ])).lower()
    return set(re.findall(r"[a-z]{4,}", text))


def _dedupe(findings: list[dict]) -> list[dict]:
    """Collapse findings of the same type whose evidence substantially overlaps.

    The three candidate groups can each surface the same underlying discrepancy
    (e.g. a stale med shows up in both `medications` and `note_assertions`).
    Keep the richer-rationale copy. Jaccard > 0.5 on same-type evidence tokens.
    """
    kept: list[dict] = []
    for f in sorted(findings, key=lambda x: len(x.get("rationale", "")), reverse=True):
        sig = _evidence_tokens(f)
        duplicate = False
        for k in kept:
            if k.get("type") != f.get("type"):
                continue
            ksig = _evidence_tokens(k)
            union = len(sig | ksig) or 1
            if len(sig & ksig) / union > 0.5:
                duplicate = True
                break
        if not duplicate:
            kept.append(f)
    return kept

_GROUP_DESCRIPTIONS = {
    "medications": (
        "Full medication reconciliation. For each drug compare THREE states: (a) the "
        "longitudinal chart status (present in medication_labels = carried active), "
        "(b) this visit's FHIR orders (authoredOn == visit date = a new start/restart "
        "today), and (c) what the transcript and note say about the patient's ACTUAL "
        "recent use. Flag: stale-chart / med-list mismatches (C) — a longitudinal med "
        "carried active with NO this-visit order that the visit says should be stopped/"
        "discontinued/duplicate; uncaptured orders (D) — an order with no matching FHIR "
        "resource; and note-hallucinated meds (A). SUPPRESS a med that is re-ordered today "
        "(is_new_today) — today's order explains its active status, so it is a legitimate "
        "start, not stale. Also suppress coded/dose synonyms. Consolidate shared causes."
    ),
    "dropped_disclosures": (
        "Patient-reported items, symptoms, and safety disclosures from the transcript, "
        "with whether the note appears to cover them. Flag genuine dropped disclosures "
        "(type B), especially safety ones. Suppress items the note covers via paraphrase."
    ),
    "note_assertions": (
        "Medication / order / condition claims asserted by the NOTE, each tagged with "
        "whether the transcript or FHIR support it. Evaluate EVERY candidate whose "
        "supported_in_transcript and supported_in_fhir are both false — do not skip any. "
        "For each such unsupported note-asserted MEDICATION START, self-verify by searching "
        "the transcript and chart; if you find no support, surface it as a type-A "
        "hallucination (a drug no one prescribed in the room is a safety event). Suppress "
        "only when the claim is a coded synonym or faithful paraphrase of something that IS "
        "supported (e.g. 'low-dose antihypertensive regimen' when lisinopril/amlodipine were "
        "started)."
    ),
}

# Focused, single-candidate prompt for the recall-critical type-A path. Judging
# each unsupported note-asserted med/order in its own pass (instead of batched
# with dozens of others) makes the hallucination catch reliable rather than
# occasionally slipping past among a long candidate list.
_HALLUCINATION_FOCUS = (
    "This is a SINGLE note-asserted new medication or order that the reconciler found "
    "UNSUPPORTED by both the transcript and the FHIR chart. Focus only on it. Decide: is "
    "it a genuine type-A hallucination — a med/order asserted in the note that no one "
    "established in the room and the chart does not contain — or is it benign (a coded "
    "synonym or a faithful paraphrase of something that IS supported elsewhere)? "
    "Self-verify FIRST: search the full transcript and the chart for the entity and any "
    "synonym before you decide. If you find no support, surface it as type A — a "
    "hallucinated MEDICATION is Critical (a drug the patient may take that no one "
    "prescribed), a hallucinated non-med order/referral is Major. If it is actually "
    "supported or is a faithful paraphrase, suppress it (material=false)."
)


def _hallucination_suspects(note_assertions: list[dict]) -> list[dict]:
    """The recall-critical subset: note-asserted new meds/orders unsupported by
    both the transcript and FHIR."""
    out = []
    for c in note_assertions:
        claim = c.get("note_claim", {})
        if (not c.get("supported_in_transcript") and not c.get("supported_in_fhir")
                and claim.get("category") in ("medication", "order")
                and claim.get("action") in ("start", "order")):
            out.append(c)
    return out


def audit_record(index: int, dataset: str = DEFAULT_DATASET,
                 agent: GuardianAgent | None = None,
                 verbose: bool = True, record: dict | None = None) -> dict[str, Any]:
    """Run the full pipeline on one record.

    `record` lets a caller (e.g. the eval harness) pass an in-memory record with
    injected note spans, without mutating the dataset on disk.
    """
    agent = agent or GuardianAgent()
    record = record if record is not None else get_record(dataset, index)
    chart = parse_chart(record)
    transcript = record["transcript"]
    note = record["note"]
    avs = record.get("after_visit_summary", "")

    trace: list[str] = []

    def log(msg: str) -> None:
        trace.append(msg)
        if verbose:
            print(msg, flush=True)

    # Task 5 — stream the pipeline stages so the agentic architecture is visible.
    log(f"● record {index}: {chart['visit_title']}")
    log("● extracting medication + disclosure claims from transcript (Claude)...")
    transcript_claims = agent.extract_ledger("transcript", transcript)
    log(f"    → {len(transcript_claims)} transcript claims")
    log("● extracting asserted claims from note (Claude)...")
    note_claims = agent.extract_ledger("note", note)
    log(f"    → {len(note_claims)} note claims")

    log("● normalizing entities against chart (RxNorm) + reconciling 3 sources...")
    resolver = EntityResolver(chart)
    candidates = reconcile(chart, transcript_claims, note_claims, resolver)
    suspects = _hallucination_suspects(candidates["note_assertions"])
    n_cand = (len(candidates["medications"]["medications"])
              + len(candidates["dropped_disclosures"]) + len(suspects))
    log(f"    → {n_cand} candidate mismatches proposed (noisy)")

    log("● judging candidates: materiality gate → require citations → "
        "self-verification on absence...")
    all_findings: list[dict] = []

    # Batch-judge the med reconciliation picture and the disclosure candidates.
    for group in ("medications", "dropped_disclosures"):
        evidence = candidates[group]
        findings = agent.judge(
            chart, transcript, note, avs,
            candidate_group=group,
            description=_GROUP_DESCRIPTIONS[group],
            evidence=evidence if isinstance(evidence, dict) else {"items": evidence},
        )
        for f in findings:
            f["_group"] = group
        all_findings.extend(findings)
        n_surf = sum(1 for f in findings if f.get("material") and f.get("type") != "none")
        log(f"    → {group}: {len(findings)} evaluated, {n_surf} material")

    # Recall-critical type-A: judge EACH unsupported note-asserted med/order in
    # its own focused pass so a hallucinated drug never slips past the batch.
    if suspects:
        log(f"● hallucination check: {len(suspects)} unsupported note med/order(s) — "
            "one focused self-verifying pass each...")
    for c in suspects:
        entity = c.get("note_claim", {}).get("entity", "claim")
        findings = agent.judge(
            chart, transcript, note, avs,
            candidate_group=f"note_assertion:{entity}",
            description=_HALLUCINATION_FOCUS,
            evidence={"candidate": c},
        )
        for f in findings:
            f["_group"] = "note_assertions"
        all_findings.extend(findings)
        hit = any(f.get("material") and f.get("type") not in (None, "none") for f in findings)
        log(f"    → {entity}: {'FLAGGED type A' if hit else 'supported / benign'}")

    material = [f for f in all_findings
                if f.get("material") and f.get("type") not in (None, "none")
                and f.get("severity") in _SEVERITY_RANK]
    judged_benign = [f for f in all_findings if f not in material]

    # Task 2 — HARD citation gate: validate every cited anchor against the real
    # source. Fabricated / unverifiable citations are discarded, not surfaced.
    log("● citation gate: verifying every anchor against transcript / note / FHIR...")
    kept, dropped = enforce(material, transcript, note, chart)
    log(f"    → {len(kept)} evidence-backed, {len(dropped)} dropped for "
        f"missing/unverifiable citations")

    surfaced = _dedupe(kept)
    surfaced.sort(key=lambda f: _SEVERITY_RANK.get(f["severity"], 99))

    return {
        "record_index": index,
        "visit_title": chart["visit_title"],
        "trace": trace,
        "ledger_sizes": {"transcript": len(transcript_claims), "note": len(note_claims)},
        "surfaced": surfaced,
        "suppressed": judged_benign,   # judged benign by the materiality gate
        "dropped": dropped,            # discarded by the citation gate
    }
