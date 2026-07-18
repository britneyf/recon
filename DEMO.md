# Recon — Demo Script & Presentation Guide

**One-liner (open with this, memorized):**
> "Ambient AI writes the clinical note. Nobody checks whether the note, the
> conversation, and the chart still agree. **Recon** is the agent that does —
> and it's tuned to earn a clinician's trust by staying quiet unless something
> is really wrong."

---

## Pre-flight (do this before you present)

- [ ] `python3 -m recon` running; **http://localhost:8000** open in the browser.
- [ ] `.env` has your `ANTHROPIC_API_KEY` (needed only for the Live beat).
- [ ] **Warm the cache:** click through all 4 scenarios once so they load instantly.
- [ ] The three screenshots open in a second tab as a **fallback** if wifi/API dies.
- [ ] Font check: the UI uses Google Fonts — confirm they render (need internet;
      falls back to Georgia/system if offline).
- [ ] Decide the plan: **Live OFF for beats 1–2** (instant, cached real audits),
      **Live ON for beat 3** (proves it's real).

---

## The 3-minute live demo (timed)

### [0:00–0:20] The hook — "silence is the product"
Open on **"The restraint — clean initiation (record 12)."** Show the note tab.
> "This is a real ambient encounter. The scribe wrote this note. Looks clean, right?"

Click **Run Audit**. While the trace streams:
> "Recon extracts every claim from the transcript and the note, normalizes them
> against the chart with RxNorm, reconciles all three sources, then judges each
> candidate."

Result: green **No Clinically Actionable Discrepancies.**
> "Forty-plus surface differences between these three sources — paraphrases, coded
> synonyms, brand-new prescriptions. It flagged **zero.** A naive diff would bury
> you in false alarms. Staying quiet *is* the product."

### [0:20–1:20] The catch — a real med-list error
Switch to **"The catch — stale med list (record 10)."** Click **Run Audit.**
> "Same system, a geriatric medication reconciliation."

One **Major** finding appears. Click it to expand.
> "The chart still lists simvastatin as active — a duplicate to his atorvastatin —
> but in the room the doctor takes it off the list. No structured record captured
> that. So the chart keeps showing a dead drug as live, corrupting the next
> interaction check or refill."

Click the **Transcript** citation → it highlights the exact turn ("Simvastatin comes
off the list today") and the chart chip.
> "Every finding is cited to all three sources and self-verified — **no citation,
> no alert.** And notice what it *didn't* flag: the meds legitimately re-ordered
> today. It knows the difference."

### [1:20–2:20] Watch it catch a hallucination — the money beat
Flip the **Live** toggle **ON**. Switch to **"Injected hallucination — warfarin (record 10)."**
> "These were pre-run so the demo is snappy — but let me prove it's real. I'm
> flipping to Live, so this now calls the model in real time."

Click **Run Live Audit** (~40s — narrate while it runs):
> "We injected a hallucination into the note — a warfarin start, an anticoagulant,
> that nobody said in the room. This is the dangerous failure mode: an
> AI-introduced error with a clinician's name on it. Watch the self-verification
> pass — before it confirms, it searches the transcript and the chart for *any*
> support."

Result: 🔴 **Critical, type A**, with the self-verify text visible.
> "Critical. It found the fabricated drug, cited its absence in both other sources,
> and showed its work. That's the line between an agent and a diff."

### [2:20–2:50] The numbers — lead with precision
Cut to the metrics slide (or `python3 -m evals.run`).
> "And we measured it. On our benchmark — the real discrepancies plus injected
> hallucinations with an answer key — we lead with **precision**, because a
> guardian that cries wolf gets turned off in a week."

Show: **Precision @ surfaced · Benign-suppression rate · Hallucination recall.**
> "100% benign suppression. We would rather miss a marginal finding than ever bury
> the real one under noise."

### [2:50–3:00] Close
> "Recon is the guardrail the ambient-scribe market needs now that everyone's
> deployed the scribe. It builds on the tool you already sell, and the same engine
> generalizes to every AI-generated clinical artifact. It **proposes**; the
> clinician decides. It never writes to the chart."

---

## Slide outline (~5 slides)

1. **Title** + the one-liner.
2. **The problem** — three sources of truth drift inside one visit (transcript /
   note / chart), in three directions (note hallucinates · chart goes stale ·
   conversation dropped). "A human catches these — sometimes, weeks later, if at all."
3. **Why it's an agent, not a diff** — a diff finds *hundreds* of benign
   differences per visit. The product is the clinical-judgment layer. Proposer →
   verifier; mandatory citations; self-verification on absence.
4. **Precision, not recall** — silence-as-a-feature. The one question that decides
   everything: *"Would a clinician change an action, an order, or a safety decision
   if they saw this?"*
5. **Metrics + close** — the three numbers, then the one "this generalizes" sentence.

---

## Judge Q&A — say these *before* they ask (inoculation)

- **"Isn't this just a diff?"** → "A diff finds hundreds of differences. The hard
  part is finding the four that matter and shutting up about the rest. That
  judgment — multi-step reasoning, self-verification, mandatory citations — is the agent."
- **"Won't Abridge/the scribe vendor just build this?"** → "Scribes verify
  note-against-audio. CDI vendors verify note-against-coding-rules, for revenue.
  Nobody ships an *adversarial, transcript-plus-chart, precision-first* faithfulness
  check that hunts contradictions and stays silent on the benign. The one input the
  whole CDI industry lacks is the transcript — we have it."
- **"What if the verifier itself hallucinates?"** → "It can't surface anything it
  can't cite. The citation gate is *deterministic code*, not the model — every
  anchor is validated against the real source text, or the finding is dropped silently."
- **"Who buys it?"** → "OEM into a scribe vendor, or an enterprise verification
  layer for a health system running several clinical AI tools."
- **"Precision vs. recall?"** → "Deliberate. We optimize for precision because alert
  fatigue kills adoption. Recall on the *dangerous* class — hallucinated meds — is
  hardened separately with a per-candidate self-verifying pass."

---

## Technical talking points (if asked how it's built)

- **Proposer → verifier** pipeline. FHIR is parsed **deterministically, never by an
  LLM** — parsing the structured chart with a model would add a hallucination
  surface to the very tool meant to catch hallucinations.
- **RxNorm entity resolver** unifies synonyms/brands/doses *before* the judge, so
  "the ACE inhibitor" / "lisinopril" / the coded entry never become a false positive.
- **Citation gate** — a deterministic post-judge filter; each cited span is verified
  against the real source (longest-common-substring). Fabricated or unverifiable
  citations are discarded, never shown.
- **Per-candidate self-verifying pass** for the recall-critical type-A class (a
  hallucinated medication is a safety event).
- **Model:** Claude Opus 4.8, structured JSON outputs, adaptive thinking.
- **Eval harness** with injected hallucinations + a benign hard-negative and an
  answer key — three metrics, headlining precision.

---

## Honesty notes (for you, not the audience)

- The LLM has run-to-run variance (precision ~80–100%, recall ~75–100% across
  runs). **Present a representative run and frame precision/recall as a design
  choice, not a weakness.** Don't claim a fixed 100% you can't reproduce on stage.
- The demo cache makes the on-screen results deterministic; the **Live** toggle
  proves they're real. Be upfront that "cached" = pre-run *real* audits, not fakes —
  that transparency is a strength, not something to hide.

---

## Commands cheat-sheet

```bash
python3 -m recon                 # the web UI  → http://localhost:8000
python3 -m guardian 10 --demo    # CLI: the catch
python3 -m guardian 12 --demo    # CLI: the restraint
python3 -m guardian 10 --inject "Started warfarin 5 mg daily for atrial fibrillation."
python3 -m evals.run             # the metrics
```
