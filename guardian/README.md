# Reconciliation Guardian

A faithfulness auditor for ambient clinical notes. It cross-references the visit
**transcript**, the generated **note**, and the structured **FHIR chart**, and
surfaces *only* the discrepancies a clinician would act on — optimizing for
**precision, not recall** (a guardian that cries wolf gets muted in a week).

## Architecture (proposer → verifier)

```
transcript ─┐
note ───────┤─▶ EXTRACT (Claude)  ─▶ claim ledgers ─┐
FHIR chart ─┘   parse directly (no LLM) ─▶ chart ────┤
                                                     ▼
                        NORMALIZE (RxNorm resolver, code) — kills synonym FPs
                                                     ▼
                                   RECONCILE (code) ─▶ candidate groups (noisy)
                                                     ▼
                          JUDGE (Claude): materiality gate → require citations
                                       → self-verify absence
                                                     ▼
                          CITATION GATE (code): validate every anchor against the
                          real source; discard anything unverifiable
                                                     ▼
                          dedupe → severity-ranked review cards  +  suppressed panel
```

| module | role |
|--------|------|
| `data.py` | dataset accessor + **deterministic** FHIR parse (meds, conditions, observations, immunizations, procedures, reports). Never LLM-parse FHIR. |
| `normalize.py` / `resolve.py` | drug-name normalization + **RxNorm/brand entity resolver** — synonyms (`Norvasc`↔amlodipine, `Tylenol`↔acetaminophen) unify before the Judge, so they never become candidates. |
| `agent.py` | Claude calls (`claude-opus-4-8`, adaptive thinking, structured JSON outputs). |
| `prompts.py` | extraction + judge prompts and schemas. |
| `reconcile.py` | the proposer — bundles evidence per entity into candidate groups. |
| `anchors.py` | the **citation gate** — a deterministic post-Judge filter that verifies every cited span exists in the source and discards fabricated/unsupported flags. |
| `pipeline.py` | orchestration + stage streaming + dedup. |
| `screen.py` | zero-API discrepancy screener across all 25 records. |
| `__main__.py` | CLI. |

The three principles are enforced, not just prompted: **no citation, no surface**
(`anchors.py`, in code), **materiality gate before type** (`prompts.JUDGE_SYSTEM`),
**self-verify on absence** (Judge + gate requires it for types A/B).

## Run it

Requires an Anthropic credential. Export `ANTHROPIC_API_KEY`, or drop it in a
`.env` at the workspace root (auto-loaded; a real exported var always wins):

```bash
cp .env.example .env    # paste your key   (.env is gitignored)

# Live-demo commands:
python -m guardian 10 --demo   # THE CATCH: one real Major med-rec discrepancy + suppressed panel
python -m guardian 12 --demo   # THE RESTRAINT: correct silence on an initiation visit

python -m guardian 10                 # cards only
python -m guardian 10 --show-suppressed
python -m guardian 13 --json          # raw JSON
```

### Which record catches what

- **Record 10** — genuine **type-C (Major)**: the longitudinal med list carries
  **simvastatin** (a duplicate to the active atorvastatin) and an **old metoprolol
  50 mg ER** dose as active; the visit discontinues both but no FHIR stop resource
  is written → stale chart / duplicate-therapy risk. Fully cited + self-verified.
- **Record 12** — the Guardian correctly **stays silent**. (The original build spec
  called this a Critical, but the data doesn't support it: the four `medication_labels`
  meds are same-day *initiation* orders, and the note says therapy was "never started."
  Flagging it would train the system to alert on every initiation visit — the precision
  killer we refuse.)
- **Record 13** — correctly **silent**: the note faithfully documents the cannabis
  and domestic-abuse disclosures, and the flu vaccine is captured as an Immunization.

## Eval (`python -m evals.run`)

Reports three metrics, headlining precision, over a hand-labeled gold set plus an
overlay of injected note hallucinations (with a benign hard-negative). Injections
are applied to an in-memory copy of the note — the dataset on disk is never mutated.

- **Precision @ surfaced** — of everything flagged, what fraction was real.
- **Benign-suppression rate** — of known-benign differences, how many it stayed silent on.
- **Hallucination-detection rate** — of injected hallucinations, how many it caught.

Edit `evals/gold.json` and `evals/injections.json` to extend the set.

## Web UI — `recon` (clinician-facing)

A light-themed review console backed by this pipeline lives in [`../recon/`](../recon/).
It shows the transcript, the note, and the chart's active med list side by side,
runs the audit, and renders severity-ranked findings whose citations highlight
the exact transcript turn and chart medication on click — with Accept / Dismiss
(it proposes; the clinician decides). Results are cached so the demo is instant
and deterministic.

```bash
python -m recon --build    # once: precompute the demo audits into recon/cache/
python -m recon            # serve http://localhost:8000
```

## Verify the deterministic half without a key

```bash
python -m guardian.selftest   # FHIR parse, RxNorm resolver, citation gate — no API calls
python -m guardian.screen     # discrepancy-surface ranking across all 25 records
```
