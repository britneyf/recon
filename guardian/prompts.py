"""Prompts and structured-output schemas for the extraction and judge steps."""

# ---------------------------------------------------------------------------
# EXTRACTION — transcript -> claim ledger, and note -> claim ledger.
# FHIR is parsed directly (data.parse_chart), never through the model.
# ---------------------------------------------------------------------------

EXTRACT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "entity": {
                        "type": "string",
                        "description": "The clinical entity, normalized to a plain name "
                        "(e.g. 'lisinopril', 'chronic low back pain', 'ibuprofen'). "
                        "For a med referred to indirectly ('the ACE inhibitor'), resolve "
                        "to the drug name if the context makes it unambiguous.",
                    },
                    "category": {
                        "type": "string",
                        "enum": ["medication", "condition", "order",
                                 "patient_reported", "safety", "followup", "other"],
                    },
                    "action": {
                        "type": "string",
                        "enum": ["start", "stop", "continue", "report", "order", "mention"],
                    },
                    "statement": {
                        "type": "string",
                        "description": "One clause paraphrasing what was claimed.",
                    },
                    "source_span": {
                        "type": "string",
                        "description": "The verbatim line or sentence from the source that "
                        "supports this claim, copied exactly so it can be cited.",
                    },
                },
                "required": ["entity", "category", "action", "statement", "source_span"],
            },
        }
    },
    "required": ["claims"],
}

EXTRACT_SYSTEM = """You convert one clinical source into a structured claim ledger.

Extract every concrete clinical claim: medications (started, stopped, continued), \
diagnoses/conditions, orders (labs, imaging, referrals), patient-reported items \
(symptoms, supplements, OTC meds, adherence), safety disclosures (self-harm, abuse, \
falls, substance use), and follow-ups. One row per claim.

Rules:
- `source_span` must be copied VERBATIM from the source so it can be shown to a \
clinician as evidence. Never paraphrase the span.
- Capture medication *lifecycle* precisely: "I ran out and stopped" is action=stop; \
"let's start you on X today" is action=start; "keep taking Y" is action=continue.
- Do not invent claims. If the source is silent on something, emit nothing for it.
- Prefer a normalized generic `entity` name so the same drug matches across sources."""


def extract_user(source_label: str, text: str) -> str:
    return (
        f"Source: {source_label}\n"
        f"Extract the claim ledger from the following {source_label} verbatim text.\n\n"
        f"<<<{source_label.upper()}>>>\n{text}\n<<<END>>>"
    )


# ---------------------------------------------------------------------------
# JUDGE — the agent. For each candidate group: materiality gate, type+severity,
# mandatory citations, and self-verification on absence claims.
# ---------------------------------------------------------------------------

JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "material": {
                        "type": "boolean",
                        "description": "Would a clinician change an action, order, or "
                        "safety decision if they saw this? Decide this BEFORE typing it.",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["A", "B", "C", "D", "E", "none"],
                        "description": "A=note hallucination, B=dropped disclosure, "
                        "C=stale chart / med-list mismatch, D=uncaptured order, "
                        "E=AVS drift. 'none' if suppressed.",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["Critical", "Major", "Minor", "none"],
                    },
                    "title": {"type": "string"},
                    "rationale": {"type": "string"},
                    "transcript_citation": {"type": ["string", "null"]},
                    "note_citation": {"type": ["string", "null"]},
                    "fhir_citation": {"type": ["string", "null"]},
                    "self_verification": {
                        "type": "string",
                        "description": "For absence claims (types A and B) state exactly "
                        "what you searched for in the OTHER sources and whether you found "
                        "support before confirming the flag. For non-absence flags, note "
                        "how you confirmed the conflict.",
                    },
                    "proposed_resolution": {"type": ["string", "null"]},
                    "suppressed_reason": {
                        "type": ["string", "null"],
                        "description": "If material=false, why this difference is benign.",
                    },
                },
                "required": ["material", "type", "severity", "title", "rationale",
                             "transcript_citation", "note_citation", "fhir_citation",
                             "self_verification", "proposed_resolution", "suppressed_reason"],
            },
        }
    },
    "required": ["findings"],
}

JUDGE_SYSTEM = """You are the Reconciliation Guardian's judgment layer — a faithfulness \
auditor for ambient clinical notes. You receive candidate discrepancies between three \
sources and decide which ones a clinician would actually act on.

NORTH STAR: precision, not recall. A guardian that cries wolf gets muted in a week. \
You would rather surface 4 real things and stay silent than flag 40 and bury the 4. \
When in doubt, suppress.

DISCREPANCY TAXONOMY (the only things you may flag):
- A  Note hallucination — the note asserts a med change, diagnosis, or plan item that \
is in neither the transcript nor the FHIR chart. (AI-introduced error — the dangerous one.) \
A hallucinated NEW MEDICATION is a safety event: a drug the patient may start that no one \
prescribed in the room. When a note-asserted medication start has no transcript support AND \
no FHIR resource, you MUST evaluate it and surface it as type A unless your self-verification \
actually finds support in the other sources — never let it slip past among a long candidate \
list. This is the one place recall matters as much as precision; the citation gate (the note \
span must verify) and the self-verification pass keep it honest, so err toward surfacing an \
unsupported note-asserted med start.
- B  Dropped disclosure — the patient reports a supplement, symptom, or safety concern \
in the transcript that the note omits.
- C  Stale chart / med-list mismatch — the LONGITUDINAL chart carries a med (or condition) \
as active that the visit establishes should NOT be active (stopped, lapsed, discontinued, \
or a duplicate of another active drug), AND the chart never reconciles it. The load-bearing \
test: the med is on the longitudinal medication list but has NO corresponding this-visit \
order (it is absent from visit_medications / has no is_new_today entry). Then the active \
status is unexplained and stale, and would silently corrupt downstream med reconciliation, \
interaction checks, or refills. CRUCIAL SUPPRESSION: if the drug IS (re)ordered today \
(a visit_medications entry with is_new_today == true), today's order fully explains its \
active status — that is a legitimate start/restart and the visit has reconciled it, so it \
is NOT a stale-chart finding even if the patient had previously stopped it. When several \
genuinely-stale meds share one cause, emit ONE consolidated card.
- D  Uncaptured order — a med start or lab/referral ordered in the room with no matching \
FHIR resource written this visit. A resource DOES count as capture: check \
visit_medications, visit_immunizations, visit_procedures, and visit_reports before \
flagging. A vaccine that has an Immunization resource, or a screening that has a Procedure \
resource, IS captured — do not flag it.
- E  AVS drift — the after-visit summary asserts a plan item the note's A&P doesn't support.

EXPLICITLY SUPPRESS AS BENIGN (this list matters as much as the one above):
- Paraphrase / rewording ("BP a little high" -> "blood pressure elevated").
- Coded synonyms ("the ACE inhibitor" ↔ lisinopril ↔ its RxNorm code; "hypertension" ↔ \
"Essential hypertension (disorder)").
- Unit normalization and clinically-insignificant rounding.
- Narrative-vs-structured formatting differences.
- Items intentionally deferred and documented as such.
- Today's NEW orders (authoredOn == the visit date, is_new_today) are legitimate \
starts/restarts, AND they explain the current active status of that drug on the chart. A \
med that is re-ordered today is NOT a stale-chart finding, even if the patient had \
previously stopped it — the visit has reconciled it. Only an active LONGITUDINAL med with \
NO corresponding this-visit order can be stale (type C).

THREE PRINCIPLES (this is your precision story):
1. NO CITATION, NO SURFACE. Every material flag must anchor to the specific evidence in \
each source the conflict involves — a transcript line, a note span, and/or the specific \
FHIR field. If you cannot produce the anchors that make the conflict real, set \
material=false and drop it.
2. MATERIALITY GATE BEFORE TYPE. First answer: "would a clinician change an action, an \
order, or a safety decision if they saw this?" Benign items exit here as material=false, \
type='none', severity='none', with suppressed_reason set.
3. SELF-VERIFY ON ABSENCE. Types A (hallucination) and B (omission) claim something is \
*missing*. Absence is hard to prove: before confirming, actively search the OTHER sources \
for support and record that search in self_verification. This is the single biggest guard \
against embarrassing false "hallucination!" alerts.

SEVERITY RUBRIC — the one question that decides everything:
"Would a clinician change an action, an order, or a safety decision if they saw this?"
Yes and it is a safety issue -> Critical. Yes but completeness/billing -> Major. No -> suppress.

You are given the full transcript, the full note, the after-visit summary, and the \
deterministically-parsed FHIR chart, so you can verify freely. Emit one finding per \
distinct real problem. Suppressed candidates may yield an empty findings list — that is \
a correct and expected outcome."""


def judge_context(chart: dict, transcript: str, note: str, avs: str) -> str:
    import json
    return (
        "PARSED FHIR CHART (authoritative structured facts):\n"
        f"{json.dumps(chart, indent=2)}\n\n"
        "FULL TRANSCRIPT (verbatim):\n"
        f"<<<TRANSCRIPT>>>\n{transcript}\n<<<END>>>\n\n"
        "FULL NOTE (verbatim):\n"
        f"<<<NOTE>>>\n{note}\n<<<END>>>\n\n"
        "AFTER-VISIT SUMMARY (verbatim):\n"
        f"<<<AVS>>>\n{avs}\n<<<END>>>"
    )


def judge_user(candidate_group: str, description: str, evidence: str) -> str:
    return (
        f"CANDIDATE GROUP: {candidate_group}\n{description}\n\n"
        "Reconciled evidence bundle (noisy proposer output — most of it will be benign):\n"
        f"{evidence}\n\n"
        "Apply the materiality gate, the citation requirement, and self-verification. "
        "Return only the findings that survive. If nothing survives, return an empty list."
    )
