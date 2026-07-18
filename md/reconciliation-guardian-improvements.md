# Reconciliation Guardian — Improvement Spec (Post-Build)

**For: Claude Code, as the next build task.**
**Context:** the v1 build already exists from `reconciliation-guardian-build-spec.md`. This document is a prioritized set of *improvements* on top of that build, derived from a critique pass. It also marks which items are **build work** (do these) vs **positioning** (a human handles these on a slide — do NOT spend engineering time on them).

---

## 0. Framing guardrail — read this first

A critique doc recommended reframing the whole project as a broad "vendor-agnostic Clinical Consistency Engine / infrastructure platform for all AI-generated clinical artifacts." **Do not rebuild around that framing.** It optimizes for an investor narrative at the direct expense of hackathon Execution and Focus scores. The winning move is the opposite: stay narrow, ship a *finished* medication-reconciliation demo, and let "this generalizes" be a single closing sentence — not the architecture.

Concretely, that means:
- Keep the existing multi-type engine in the codebase, but **cut the demo to medication reconciliation only.**
- Do **not** add order-set / referral-letter / prior-auth / discharge-summary handling. Out of scope.
- Do **not** genericize the pipeline into an "any artifact" abstraction. It costs time and buys nothing scoreable.

The scoring weights that drive every decision below: **Execution 30%, Creativity 25%, Impact 20%, Technical Complexity 20%.** Prioritize work that is *visible in a 3-minute live demo*.

---

## Priority summary

| P | Task | Primarily scores | Est. |
|---|------|------------------|------|
| **P0** | Eval harness with injected hallucinations | Execution, Technical | 2–3h |
| **P0** | Enforce the citation gate as a hard filter | Execution, Technical, Creativity | 1–2h |
| **P1** | Real deterministic entity normalization (RxNorm) | Technical | 2h |
| **P1** | Silent "clean" state + suppression showcase | Creativity, Execution | 1–2h |
| **P1** | Stream the Judge's reasoning to the UI | Technical, Execution | 1h |
| **P1** | Cut demo path to meds only + one taxonomy slide | Execution | 0.5h |
| **—** | Positioning (competitors, buyer, vision) | *slide, not code* | — |

If time is short, **P0 + the suppression showcase** are the two highest-ROI items. Do those before anything else.

---

## P0 — Task 1: Eval harness with injected hallucinations

**Why:** converts an entire scoring axis from "trust us" to measured numbers. Most teams will have zero eval; three real metrics is a decisive Execution + Technical win. This is the highest-value post-build task.

**Build:**

1. **Gold discrepancy set.** Hand-label the real, naturally-occurring discrepancies across these records (indices are 0-based into the JSONL, matching `summary.json` order):
   - **Record 12** (`General exam — hypertension treatment initiation`): chart `medication_labels` lists lisinopril / amlodipine / HCTZ / acetaminophen as active; transcript + note establish the patient stopped all meds months ago. → 1 Critical type-C mismatch. *This is the anchor case.*
   - **Record 10** (`geriatric cardiometabolic follow-up`): 9 chart meds — richest surface for med reconciliation. Label any stale/contradicted meds here.
   - **Record 13** (`psychosocial screening with safety disclosure`): candidate type-B (dropped disclosure) — check whether the note omits a transcript safety item.
   - Add 3–5 more records where you can confidently label at least one discrepancy or confirm "clean."

2. **Injected hallucination set (the objective benchmark).** Programmatically inject known-false statements into the `note` field of specific records, with an answer key. Each injection must be something *not present in that record's transcript or FHIR*. Suggested injections:
   - Record 12 note → append to Plan: *"Started atorvastatin 20 mg daily for hyperlipidemia."* (never discussed; patient has no statin in chart)
   - Record 12 note → *"Referred to cardiology for further evaluation."* (no referral in transcript)
   - Record 10 note → inject a med dose change that contradicts the FHIR `MedicationRequest`.
   - Record 6 (`new hypertension`) note → *"Patient counseled to begin metformin."* (not in transcript)
   - One "hard negative": inject a *paraphrase* that is technically reworded but faithful, and label it **benign** — the harness must confirm the system does NOT flag it.

   Store injections as a separate overlay (e.g. `evals/injections.json`) mapping `record_id → [{span, label: hallucination|benign, rationale}]`, applied at eval time. Never mutate the source dataset files (they're read-only under `/mnt/user-data/uploads`; copy first if needed).

3. **Metrics — compute and print all three, headline precision:**
   - **Precision @ surfaced** = (real discrepancies flagged) / (total flagged). *Headline number.*
   - **Benign-suppression rate** = (benign differences correctly NOT flagged) / (total benign differences in the set). Directly measures alert-fatigue resistance.
   - **Hallucination-detection rate (recall)** = (injected hallucinations caught) / (injected hallucinations). The objective benchmark.

**Acceptance criteria:**
- `python -m evals.run` prints all three metrics with counts, not just percentages.
- The harness applies injections without modifying source files.
- At least one labeled **benign** case exists and the report shows it was correctly suppressed.
- Output includes a per-item table (record, expected, predicted, cited?) so failures are inspectable.

---

## P0 — Task 2: Enforce the citation gate as a hard filter

**Why:** this is the entire trust story and the answer to "what if the verifier hallucinates?" The LLM must never adjudicate truth — evidence does. Verify this is *actually enforced in code*, not merely requested in a prompt. It's common for the model to be asked for citations while the code still surfaces uncited flags.

**Build:**

1. Locate where confirmed discrepancies are emitted to the review queue.
2. Enforce, as a deterministic post-Judge filter (not inside the prompt): every surfaced discrepancy MUST carry
   - a `transcript_anchor` (specific line/span that exists in the transcript string),
   - a `note_anchor` (specific span that exists in the note),
   - a `fhir_anchor` (specific resource id / field path that exists in `encounter_fhir.related_resources` or `patient_context.longitudinal_summary`).
3. **Validate anchors against the source**, don't trust the model: confirm each cited transcript/note span is actually a substring (or fuzzy-verified span) of the real text, and each FHIR anchor resolves to a real path. If any required anchor is missing or unverifiable → **discard the discrepancy silently.** No citation, no alert.
4. Log discarded candidates to a `dropped/` list (not shown to clinician, but surfaced in the demo — see below).

**Acceptance criteria:**
- A unit test feeds the Judge a fabricated discrepancy with a citation that does NOT appear in the source; the pipeline discards it.
- No item reaches the review queue without three validated anchors.
- The `dropped/` list is queryable (for the demo beat where you show a candidate that was correctly dropped for lack of evidence).

---

## P1 — Task 3: Real deterministic entity normalization

**Why:** answers "where's the technical depth?" and is the actual mechanism that suppresses synonym false positives. Must be real code, not an LLM call — parsing FHIR with a model adds a hallucination surface to the thing meant to catch hallucinations.

**Build:**

1. Parse FHIR resources directly (no LLM):
   - Meds: `MedicationRequest[].medicationCodeableConcept.text` + `.coding[].code` (RxNorm) + `.status`.
   - Chart meds: `patient_context.longitudinal_summary.medication_labels` (strings).
   - Conditions: `Condition[].code.coding[].code` (SNOMED) + `.clinicalStatus.coding[].code`.
2. Build an entity resolver keyed on **RxNorm code where present**, falling back to normalized-string match (lowercase, strip dose/form, generic/brand map for the handful of brands in the data, e.g. Tylenol ↔ acetaminophen).
3. Use the resolver to align the med entity spoken in the transcript ("the ACE inhibitor", "lisinopril ten milligrams") to the coded chart/order entity, so synonym and formatting differences never reach the Judge as candidates.

**Acceptance criteria:**
- Given record 12, the resolver correctly ties transcript-mentioned meds and chart `medication_labels` to the same normalized entities.
- A test asserts that a coding synonym / brand-vs-generic pair produces **zero** candidate discrepancies.
- FHIR parsing path contains no model calls.

---

## P1 — Task 4: Silent "clean" state + suppression showcase

**Why:** single highest-drama, lowest-cost feature. It simultaneously answers three critiques — "isn't this just a diff?", "won't it cause alert fatigue?", and "what's your precision philosophy?" — by *demonstrating* restraint instead of claiming it. Silence-as-a-feature is the most memorable idea in the pitch; make it real.

**Build:**

1. Add an explicit clean state to the review UI:
   `✓ No clinically actionable discrepancies detected — ready for clinician sign-off.`
   Show it only after the full pipeline runs and finds nothing material.
2. Add a **"show suppressed" toggle** that reveals the benign differences the system *considered and dismissed*, each with a one-line reason:
   - paraphrase (note reworded transcript)
   - coding synonym / brand↔generic
   - unit normalization / rounding
   - narrative-vs-structured formatting
   - candidate dropped for missing evidence (from the `dropped/` list in Task 2)
3. For record 12 specifically, ensure the suppressed list is populated (the visit has heavy paraphrase + the new-med starts that should NOT be flagged), so the demo can click through 2–3 real suppressions.

**Acceptance criteria:**
- On a genuinely clean record, the UI shows the clean state (not an empty list).
- On record 12, the review queue shows exactly the Critical med mismatch, and the suppressed panel shows ≥3 dismissed differences each with a reason.

---

## P1 — Task 5: Stream the Judge's reasoning to the UI

**Why:** makes the agentic architecture *visible* (Technical Complexity is unscoreable if invisible) and fills live-demo latency with the most persuasive content you have. This is the moment that proves it's an agent, not a diff.

**Build:**
- Surface the pipeline stages as they execute, as short status lines:
  `extracting med claims from transcript → normalizing against chart (RxNorm) → candidate mismatch found → running self-verification → searching transcript for supporting evidence → confirmed / dropped`.
- Emphasize the **self-verification-on-absence** step for hallucination flags — show it actively searching for support before confirming a "missing" claim.

**Acceptance criteria:**
- The demo run visibly shows ≥5 named stages.
- The self-verification step is distinct and labeled.

---

## P1 — Task 6: Cut the demo path to meds only

**Why:** focus is scored. One memorable takeaway beats five half-shown types.

**Build:**
- Add a demo mode / default record = 12 that walks: clean-looking note → run → one Critical med discrepancy → suppressed panel → (optional) hallucination injection re-run.
- Keep the other discrepancy types (A/B/D/E) in the engine but off the demo path. Represent them as a single static taxonomy slide/asset: "the same engine handles four more discrepancy classes."

**Acceptance criteria:**
- Fresh run with no configuration lands on the record-12 med-rec story.
- Other types are reachable but not on the default demo path.

---

## Positioning — NOT build work (a human owns these on slides)

Do not spend engineering time here. Recorded so nothing is dropped.

- **"Won't Abridge just build this?" / "What's novel?"** → The scribes verify note-against-audio (Abridge ships transcript traceability). CDI vendors — Ambience is furthest along, at point-of-care — verify note-against-coding-rules for *revenue*, surfacing evidence for diagnoses to *add*. Nobody ships an **adversarial, transcript-plus-chart, safety-polarity, precision-first** faithfulness check that hunts *contradictions* and stays silent on the benign. That's the defensible wedge; it survives a domain expert. The one input the entire mature CDI industry lacks is the **transcript** — you have it.
- **"Who buys it?"** → one line: OEM into a scribe vendor, or an enterprise verification layer for a system running multiple clinical AI tools. Nice-to-have at a hackathon, not scored — keep it short.
- **"Long-term vision?"** → exactly one closing sentence gesturing at "the verification layer for AI-generated clinical work," framed as a *horizon*, not as what was built.
- **Impact-at-scale number** → ambient AI reached ~63% of Epic-using US hospitals by mid-2025 and grew since; verify/cite from a current source before it goes on a slide.

---

## Explicit non-goals (don't let scope creep back in)

- No platform/infrastructure genericization.
- No additional artifact types (orders, referrals, prior-auth, discharge).
- No auto-fix / auto-write to the chart — the human gate IS the safety story; keep it loud.
- No dashboard/analytics view — that's a disqualifier category.
- No LLM parsing of FHIR.

---

## Suggested order of work

1. Task 2 (citation gate) — everything downstream depends on it being real.
2. Task 3 (normalization) — kills the false positives the eval will otherwise punish.
3. Task 1 (eval harness) — now measures a system that actually enforces evidence.
4. Task 4 (silent state + suppression) — the demo's emotional core.
5. Task 5 (reasoning stream) + Task 6 (demo cut) — polish for the live run.

If forced to pick two: **Task 1 + Task 4.** They hit your two heaviest criteria (Execution, Creativity) and are the most visible in three minutes.
