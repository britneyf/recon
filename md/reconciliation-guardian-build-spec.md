# Reconciliation Guardian — Build Spec

**A faithfulness auditor for ambient clinical notes.**
Cross-references the visit transcript, the generated note, and the structured FHIR chart, and surfaces *only* the discrepancies a clinician would actually act on.

> One line for judges: *"Ambient AI writes the note. Nobody checks whether the note, the conversation, and the chart still agree. We built the agent that does — and it's tuned to earn a clinician's trust by staying quiet unless something is really wrong."*

---

## 1. The problem, precisely

Every ambient-scribe deployment now has three sources of truth that drift apart inside a single visit:

1. **The transcript** — what was actually said.
2. **The note** — what the scribe wrote down.
3. **The FHIR chart** — what the record structurally claims is true (active meds, active conditions, orders).

They disagree constantly, in three directions:

- **The note hallucinates.** It asserts a plan item, diagnosis, or med change that was never said in the room. (The dangerous one — it's an AI-introduced error with a clinician's name on it.)
- **The chart is stale.** The med list still shows a drug the patient just said they stopped; a stopped med reads "active."
- **The conversation is dropped.** A patient-reported supplement, a symptom, or a safety disclosure is in the transcript and nowhere else.

Today a human catches these — sometimes, weeks later, if at all. This agent catches them at the point of care, before the note is signed.

**Why this is a real agent and not a diff tool:** a naïve string diff between three sources produces *hundreds* of differences per visit, virtually all benign — the note paraphrases, the chart uses coded synonyms, structured data reads differently than narrative. The entire product is the clinical-judgment layer that separates "stopped med still shows active" (surface it) from "note said *hypertension*, chart coded *essential hypertension (disorder)*" (suppress it). That judgment is multi-step reasoning over evidence, with self-verification. That's the agent.

**North star: precision, not recall.** A guardian that cries wolf gets muted in a week. Every design decision below optimizes for *never surfacing a benign discrepancy*, even at the cost of missing a marginal one. We would rather ship a system that flags 4 real things and stays silent than one that flags 40 things and buries the 4.

---

## 2. What it catches (the discrepancy taxonomy)

This taxonomy is the clinical core. It's also your demo narration and your eval label set.

| # | Type | Direction | Example | Default severity |
|---|------|-----------|---------|------------------|
| A | **Note hallucination** | note ⊄ (transcript ∪ FHIR) | Note says "started atorvastatin"; transcript and chart have no such thing | Critical / Major |
| B | **Dropped disclosure** | transcript ⊄ note | Patient reports a supplement, a symptom, or a safety concern; note omits it | Critical (safety) / Major |
| C | **Stale chart / med-list mismatch** | FHIR ⊄ conversation | Chart lists a med as `active` that the visit establishes as stopped/lapsed | Critical / Major |
| D | **Uncaptured order** | conversation ⊄ FHIR | Doctor starts a med / orders a lab in the room; no matching FHIR resource written | Major |
| E | **AVS drift** | AVS ⊄ note A&P | After-visit summary asserts a plan item the note's Assessment & Plan doesn't support | Major / Minor |

**Explicitly suppressed as benign (this list is as important as the one above):**

- Paraphrase and rewording ("BP a little high" → "blood pressure elevated").
- Coded synonyms ("the ACE inhibitor" ↔ `lisinopril 10 MG Oral Tablet` ↔ SNOMED/RxNorm code).
- Unit normalization and clinically-insignificant rounding.
- Narrative-vs-structured formatting differences.
- Items intentionally deferred and documented as such.

**Severity rubric — the one question that decides everything:**
*"Would a clinician change an action, an order, or a safety decision if they saw this?"*
Yes, and it's a safety issue → **Critical.** Yes, but it's completeness/billing → **Major.** No → **suppress.**

---

## 3. Architecture

A proposer → verifier pipeline. Cheap, broad extraction proposes candidates; expensive, careful reasoning confirms and ranks. This is deliberately the same shape as good agent design (generate candidates, then a separate pass that critiques and gates them).

```
                         ┌────────────────────────────────────────┐
   transcript ──────────►│  EXTRACT: transcript → claim ledger     │
                         │  (meds start/stop/continue, dx,          │
   note ────────────────►│   follow-ups, patient-reported items,    │
                         │   safety disclosures)                    │
   FHIR chart ──────────►│  NORMALIZE: FHIR + note → same ledger    │
   (patient_context +    │  keyed by clinical entity                │
    encounter_fhir)      └───────────────────┬────────────────────┘
                                             │  unified ledger
                                             ▼
                         ┌────────────────────────────────────────┐
                         │  RECONCILE: align entries across the 3  │
                         │  sources; emit raw candidate mismatches  │
                         └───────────────────┬────────────────────┘
                                             │  candidates (noisy)
                                             ▼
                         ┌────────────────────────────────────────┐
                         │  JUDGE (the agent):                     │
                         │   for each candidate →                  │
                         │    • materiality: act on it? (Y/N)      │
                         │    • type (A–E) + severity              │
                         │    • REQUIRE evidence citation from     │
                         │      each source, or drop it            │
                         │    • self-verify hallucination flags    │
                         │      (second read to find support       │
                         │       before confirming absence)        │
                         └───────────────────┬────────────────────┘
                                             │  confirmed, cited, ranked
                                             ▼
                         ┌────────────────────────────────────────┐
                         │  REVIEW QUEUE (human-in-the-loop)       │
                         │   severity-ranked cards, each with:     │
                         │   transcript line + note span + FHIR    │
                         │   field, and a proposed resolution      │
                         │   [Accept] [Dismiss] [Edit]             │
                         └────────────────────────────────────────┘
```

**The three principles baked into the JUDGE step (this is your precision story):**

1. **No citation, no surface.** Every flag must point to a specific transcript line, a specific note span, *and* the specific FHIR field it conflicts with. If the agent can't produce all the anchors, it drops the candidate. This alone kills most false positives.
2. **Materiality gate before type.** The agent decides *"is this actionable?"* before it decides *what kind* of problem it is. Benign items exit here.
3. **Self-verification on absence claims.** Hallucination (type A) and omission (type B) are claims that something is *missing*. Absence is hard to prove, so the agent does a dedicated second pass that actively searches the other sources for support before confirming the flag. This is the single biggest guard against embarrassing false "hallucination!" alerts.

---

## 4. Data contract (exact paths in this dataset)

Everything you need is in each JSONL record. Field paths, verified against the data:

| What you need | Path in record |
|---|---|
| Visit title / type / date | `metadata.visit_title`, `metadata.visit_type`, `metadata.date` |
| Transcript (speaker-labeled) | `transcript` (string; `DR:` / `PT:` / `NURSE:` / `FAMILY:`) |
| Generated note (SOAP markdown) | `note` |
| After-visit summary | `after_visit_summary` (+ `after_visit_summary_provenance`) |
| **Chart active meds** | `patient_context.longitudinal_summary.medication_labels` (list of RxNorm-style strings) |
| **Chart active conditions** | `patient_context.longitudinal_summary.condition_labels` (SNOMED display strings) |
| Whole-chart resource counts | `patient_context.longitudinal_summary.resource_counts` |
| **This visit's FHIR resources** | `encounter_fhir.related_resources.{Condition, Observation, MedicationRequest, Procedure, DiagnosticReport, Immunization, ...}` |
| Med resource — name + code | `MedicationRequest[].medicationCodeableConcept.text` and `.coding[].code` (RxNorm) |
| Med resource — status | `MedicationRequest[].status` (`active` / `stopped` / …), `.authoredOn` |
| Condition — clinical status | `Condition[].clinicalStatus.coding[].code`, `.code.text` (SNOMED display) |
| Observation — value | `Observation[].valueQuantity` / `.component` (vitals, labs) |

Two normalization notes that save you time:

- `medication_labels` gives you human strings; `MedicationRequest.coding[].code` gives you RxNorm codes. Use the code as the join key where present, fall back to normalized string match otherwise. This is how you tie *"lisinopril"* said in the room to the coded chart entry and avoid synonym false positives.
- The chart's `medication_labels` reflects the **longitudinal** record, while `encounter_fhir.related_resources.MedicationRequest` is **this visit's** new orders. The gap between them is exactly where "stale chart" (type C) and "uncaptured order" (type D) live.

---

## 5. The demo — record 12, no planting required

**Record index 12: "General exam — hypertension treatment initiation and chronic low back pain"** (patient Julius Renner, 36M). This record contains a real, naturally-occurring Critical discrepancy — you do not have to fabricate it.

The setup, straight from the data:

- **Chart `medication_labels`:** `Hydrochlorothiazide 25 MG`, `Acetaminophen 325 MG [Tylenol]`, `lisinopril 10 MG`, `amLODIPine 2.5 MG` — all carried as active.
- **Transcript, verbatim from the patient:** he ran out of everything months ago and stopped refilling after an insurance change. He is currently on nothing.
- **The note agrees with the patient:** it documents "no current medications after prior prescriptions lapsed."
- **So: the note and transcript agree the patient is on nothing, but the structured chart still lists four active meds.** That's a Critical type-C mismatch that would silently corrupt any downstream med-reconciliation, interaction check, or refill.

**Live demo flow (3 minutes):**

1. **Show the note.** It's clean, well-written, plausible. "This is what your scribe produced. Looks perfect, right?"
2. **Run the Guardian.** It surfaces **one Critical card**: chart lists 4 active antihypertensives/analgesics; patient states in-visit that all were stopped months ago; note confirms. Proposed resolution: reconcile med list (mark lapsed) before signing. Evidence: the transcript line, the note's medication statement, the four `medication_labels` entries.
3. **The contrast beat — prove precision.** In the same visit the doctor *starts* lisinopril / amlodipine / HCTZ today, and the note paraphrases the conversation heavily. Show the Guardian **staying silent** on all of that — the new starts are captured, the paraphrases are benign. "Forty-plus surface differences in this note. It flagged one. That's the product."
4. **The hallucination beat (optional plant, clearly labeled as a plant for judges).** Inject one line into the note — a med or plan item never spoken — and re-run. It flags **Major: unsupported plan item**, cites the absence in both transcript and chart, and shows its self-verification pass ("searched transcript for atorvastatin: not found"). This is the "watch it catch the hallucination" moment the judges came for.

Runner-up demo records if you want variety: **index 10** (geriatric, 9 chart meds — richest reconciliation surface) and **index 13** (psychosocial screening *with a safety disclosure* — best type-B "dropped disclosure" story).

---

## 6. Build plan (one day, two people)

**Stack — deliberately minimal.** Python + the Claude API for extraction and judgment (structured JSON outputs); a thin FastAPI backend; a plain React or static HTML review queue. No Streamlit (banned, and it hides your agent behind a toy). No database — JSON files on disk are fine for 25 records. The **agent is the product**; the UI is just the surface that proves it. Keep it that way in the pitch, because "a dashboard as the main feature" is a disqualifier.

Suggested split: **Person A** owns extraction + reconciliation + judge (the agent); **Person B** owns the review-queue UI, the demo script, and the eval harness.

| Hours | Milestone |
|---|---|
| 0–1 | Load JSONL, build a record accessor, dump the three sources for record 12 side by side. Confirm the paths in §4. |
| 1–3 | **Extraction pass.** Prompt Claude to convert transcript → structured claim ledger (entity, action: start/stop/continue/report, source span). Same for the note. FHIR is parsed directly, not via LLM. |
| 3–4 | **Reconciliation.** Join the three ledgers by normalized entity (RxNorm code where present). Emit raw candidate mismatches. Expect it to be noisy — that's correct at this stage. |
| 4–6 | **The Judge.** The materiality gate + type/severity + mandatory citation + self-verify-on-absence. This is where your time should go. Tune on record 12 until it flags exactly the real thing and suppresses the paraphrases. |
| 6–8 | **Review queue UI.** Severity-sorted cards; each shows transcript line, note span, FHIR field, proposed resolution, Accept/Dismiss. Make the citations *clickable to the source* — that's what makes clinicians trust it. |
| 8–9 | **Eval harness** (see §7) + the hallucination-injection toggle for the demo. |
| 9–10 | Rehearse the 3-minute demo on record 12. Freeze scope. |

---

## 7. Proving it works (eval — lead with precision)

Judges will ask "how do you know it's not just making things up too?" Have numbers ready.

- **Build a small gold set.** Hand-label the naturally-occurring discrepancies across ~6–8 records (record 12's med mismatch, record 13's safety disclosure, etc.), plus **plant ~5 known hallucinations** into notes with an answer key.
- **Report, in this order:**
  1. **Precision @ surfaced** — of everything it flagged, what fraction was real. *This is your headline number.* Target it above recall.
  2. **Benign-suppression rate** — of a set of known-benign differences (paraphrases, synonyms), how many it correctly stayed silent on. This is the number that proves it won't get muted in production.
  3. **Recall on planted hallucinations** — how many of the known injected errors it caught.
- **Show one confusion case on purpose:** a paraphrase it *could* have flagged and didn't. Suppression is a feature, and demonstrating restraint is more convincing than a big recall number.

---

## 8. Scope discipline — what NOT to build

- **Don't auto-fix.** The agent proposes; a human accepts. Autonomous chart-writing turns a safety tool into a liability and invites every "is it safe?" question. The human gate *is* the safety story — keep it loud and visible.
- **Don't build a dashboard.** Analytics across the panel is a trap (and a disqualifier). The deliverable is a per-visit review queue that drives an action.
- **Don't try to catch everything.** Pick meds (type C/D) and note hallucination (type A) as the spine; add safety-disclosure (type B) only if time allows. Depth on two types beats shallow coverage of five.
- **Don't LLM-parse the FHIR.** It's already structured. Parsing it with a model just adds a hallucination surface to the thing meant to *catch* hallucinations.

---

## 9. Stretch goals (only if the core is solid)

- **Confidence-calibrated triage:** route only Critical items to the clinician inline; batch Major/Minor for end-of-day review.
- **Learned suppression:** feed "Dismiss" clicks back as few-shot examples so the guardian gets quieter and more precise per clinician over time.
- **Wire it to the closed-loop order agent** (the sibling idea): the Guardian becomes the verifier that checks proposed orders against the chart before anything is written — a clean proposer/verifier pair that makes the whole thing look like a deliberate safety architecture rather than a point tool.

---

## 10. The judge-inoculation lines

Say these before they're asked:

- *"The hard part isn't finding differences — a diff finds hundreds. The hard part is finding the four that matter and shutting up about the rest. That's why this is an agent, not a script."*
- *"We optimized for precision, because a guardian that cries wolf gets turned off. Here's our benign-suppression number."*
- *"It never writes to the chart. It proposes, a human decides. The human gate is the safety design, not a limitation."*
- *"This is the guardrail the whole ambient-scribe market needs now that everyone's deployed the scribe. We're building on top of the thing you already sell, not competing with it."*
