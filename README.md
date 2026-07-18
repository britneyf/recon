# recon

**A faithfulness auditor for ambient clinical notes.**

Ambient AI writes the note; nobody checks whether the note, the conversation, and
the chart still agree. `recon` cross-references the visit **transcript**, the
generated **note**, and the structured **FHIR chart**, and surfaces *only* the
discrepancies a clinician would act on — optimizing for **precision, not recall**
(a guardian that cries wolf gets muted in a week).

Built for *The Future of Agentic AI in Healthcare* hackathon on Abridge's
synthetic ambient-FHIR corpus.

---

## What it catches

| Type | Discrepancy |
|------|-------------|
| A | **Note hallucination** — the note asserts a med/plan item that's in neither the transcript nor the chart |
| B | **Dropped disclosure** — a patient-reported symptom or safety concern the note omits |
| C | **Stale chart** — the chart carries a med as active that the visit establishes as stopped |
| D | **Uncaptured order** — a med/lab ordered in the room with no matching FHIR resource |
| E | **AVS drift** — the after-visit summary asserts a plan item the note doesn't support |

…and deliberately **suppresses** paraphrase, coded synonyms, unit rounding, and
today's legitimate new orders.

## Architecture — proposer → verifier

```
transcript ─┐
note ───────┤─▶ EXTRACT (Claude)  ─▶ claim ledgers ─┐
FHIR chart ─┘   parse directly (no LLM) ─▶ chart ────┤
                                                     ▼
                        NORMALIZE (RxNorm resolver) — kills synonym false positives
                                                     ▼
                                   RECONCILE (code) ─▶ candidate groups
                                                     ▼
                          JUDGE (Claude): materiality → require citations → self-verify
                                                     ▼
                          CITATION GATE (code): validate every anchor vs. the real source
                                                     ▼
                          dedupe → severity-ranked review queue
```

The three principles are **enforced in code**, not just prompted: *no citation, no
surface* (`guardian/anchors.py`); *materiality gate before type*; *self-verify on
absence* (a per-candidate pass for the recall-critical hallucination class).

## Layout

```
recon/        the clinician-facing web app (light editorial UI) + result cache
guardian/     the agent — extract · normalize · reconcile · judge · citation gate
evals/        eval harness: precision / benign-suppression / hallucination-recall
data/         the synthetic ambient-FHIR dataset (not committed — see below)
DEMO.md       the 3-minute demo script + slide outline + Q&A
```

## Run it

Requires an Anthropic credential. Put it in `.env` (auto-loaded):

```bash
cp .env.example .env          # paste ANTHROPIC_API_KEY

python3 -m recon --build      # once: precompute the demo audits into recon/cache/
python3 -m recon              # serve the web UI → http://localhost:8000

python3 -m guardian 10 --demo # CLI: the catch (a real stale-med finding)
python3 -m guardian 12 --demo # CLI: the restraint (correct silence)
python3 -m evals.run          # the three metrics
```

No key needed for the deterministic checks:

```bash
python3 -m guardian.selftest  # FHIR parse, RxNorm resolver, citation gate
python3 -m guardian.screen    # discrepancy-surface ranking across all records
```

## Dataset

The synthetic ambient-FHIR corpus lives in `data/` and is **not committed** (it's
Abridge's, provided for the hackathon). Place `synthetic-ambient-fhir-25.jsonl`
under `data/` to run against real records. Everything is synthetic — no real
patient data.

## Design

`recon` proposes; the clinician decides. It never writes to the chart — the human
gate *is* the safety story.
