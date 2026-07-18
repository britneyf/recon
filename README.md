# recon

### Conversation-to-chart reconciliation for ambient clinical AI

Ambient scribes generate clinical documentation from the patient–clinician conversation. **recon** checks whether the transcript, generated note, after-visit summary, and structured FHIR record still tell the same clinical story.

It identifies a small set of potentially actionable discrepancies and proposes clarification for clinician review. It does not diagnose, modify the chart, or resolve conflicts autonomously.

> **Ambient AI writes the note. recon checks what may need clarification before the encounter is closed.**

Built for **The Future of Agentic AI in Healthcare Hackathon** using Abridge's synthetic ambient-FHIR dataset.

## Example

**Patient says**

> "I stopped taking lisinopril about six months ago."

**FHIR medication list**

> Lisinopril 10 mg — active

**Generated note**

> Current medications include lisinopril.

**recon surfaces**

> **Possible stale medication record**
> The patient reports discontinuing lisinopril, but it remains active in the structured medication list and generated note.
>
> **Suggested clarification:**
> "Can we confirm whether you are currently taking lisinopril?"

The clinician decides whether any update is appropriate.

## Why recon

Ambient documentation systems are optimized to capture and summarize the current encounter. They may not independently reconcile every statement against the patient's longitudinal record or verify consistency across downstream artifacts.

recon adds a review layer between ambient documentation and final chart sign-off:

* compares patient statements with existing chart data;
* detects unsupported or omitted clinical claims;
* normalizes medication and terminology differences;
* requires source evidence for every surfaced discrepancy;
* suppresses benign differences such as paraphrases, coded synonyms, and reasonable unit rounding;
* prioritizes precision to reduce alert fatigue.

## Features

**Reconciliation across four artifacts.** Transcript, generated note, after-visit summary, and FHIR bundle are compared as one clinical story rather than checked in isolation.

**Evidence-gated findings.** Every surfaced discrepancy carries citations into the original sources, and each anchor is re-verified in code against the real text before display. A finding whose citation cannot be located is discarded rather than shown.

**Deterministic terminology normalization.** RxNorm codes, brand-to-generic mapping, and dose normalization collapse "the ACE inhibitor," "lisinopril ten milligrams," and the coded chart entry into one entity — so synonyms never become candidate discrepancies in the first place.

**No model in the FHIR path.** Structured resources are parsed with plain code. Parsing the chart with a model would add a hallucination surface to the tool meant to catch hallucinations.

**Materiality gate before classification.** The system asks whether clarification could affect care before it asks what type of discrepancy this is, which keeps textual noise out of the queue.

**Focused self-verification on absence.** Each unsupported note assertion gets its own dedicated judging pass, because "this claim appears nowhere in the transcript or chart" is the hardest case to establish and the most costly to miss.

**Severity-ranked, deduplicated queue.** Findings describing the same underlying problem are merged and ordered so the most clinically significant item is read first.

**Suggested clarification, not correction.** Output is phrased as a question for the clinician to ask, because recon cannot know whether the patient or the chart is right.

**Inspectable pipeline.** Every run emits a stage-by-stage trace — ledger sizes, candidates proposed, findings judged material, anchors dropped by the citation gate — so the reasoning is auditable rather than opaque.

**Three-bucket output.** Surfaced, suppressed, and dropped are all returned. What the system chose to stay quiet about is as reviewable as what it raised.

**Clinician-facing web UI.** Findings link back to the exact transcript turn and chart entry that support them, highlighted on click.

**Runs without a key for the deterministic layer.** FHIR parsing, terminology resolution, citation anchoring, and discrepancy ranking are all testable offline.

## What it detects

| Type | Discrepancy                      | Example                                                                                              |
| ---- | -------------------------------- | ---------------------------------------------------------------------------------------------------- |
| A    | **Unsupported note claim**       | The note introduces a medication or plan item supported by neither the transcript nor the chart.     |
| B    | **Dropped patient disclosure**   | A patient-reported symptom or safety concern is absent from the note.                                |
| C    | **Potentially stale chart item** | A medication remains active even though the visit indicates it was stopped.                          |
| D    | **Uncaptured order**             | A medication or laboratory order discussed during the encounter has no matching structured resource. |
| E    | **After-visit-summary drift**    | The AVS contains a plan item that is not supported by the note or conversation.                      |

recon deliberately suppresses:

* equivalent paraphrases;
* recognized medication synonyms;
* coding-system aliases;
* clinically insignificant unit rounding;
* appropriately documented new orders from the current encounter.

## Architecture

```text
  NARRATIVE SOURCES                          STRUCTURED SOURCE
  transcript · note · AVS                    FHIR bundle
          │                                          │
          ▼  [LLM]                                   ▼  [code]
  Claim extraction                           Deterministic parse
  schema-constrained JSON ledgers            no model touches FHIR
          │                                          │
          └──────────────────┬───────────────────────┘
                             ▼  [code]
                    Entity resolution
                    RxNorm codes · brand→generic · dose normalization
                    synonyms collapse here, before anything is judged
                             │
                             ▼  [code]
                    Reconciliation — proposes noisy candidates
                             │
       ┌─────────────────────┼─────────────────────┐
       ▼                     ▼                     ▼
  medication set      dropped disclosures    unsupported note
  (batch judge)         (batch judge)         assertions
                                             (one focused
                                              self-verifying
                                              pass each)
       └─────────────────────┼─────────────────────┘
                             ▼  [LLM]
                    Judge — materiality first, then type,
                    citations required for every claim
                             │
                             ▼  [code]
                    Citation gate — every anchor re-verified
                    against the real transcript / note / chart
                             │
       ┌─────────────────────┼─────────────────────┐
       ▼                     ▼                     ▼
   SURFACED              SUPPRESSED             DROPPED
   deduped, ranked       judged immaterial      citation failed
   by severity           (benign difference)    verification
       │
       ▼
   Clinician review queue — proposals only, never writes
```

`[LLM]` marks the two model-backed steps; `[code]` marks everything deterministic.
The model proposes and explains; code decides what is allowed to reach a clinician.

### Proposer → verifier design

1. **Extract**
   A language model converts narrative artifacts into source-grounded claim ledgers.

2. **Parse**
   FHIR resources are parsed deterministically without an LLM.

3. **Normalize**
   RxNorm and terminology resolution reduce false positives caused by synonyms and coding differences.

4. **Reconcile**
   Deterministic logic groups related claims and generates discrepancy candidates.

5. **Judge**
   A language model evaluates clinical materiality, assigns a discrepancy type, and must provide source citations. Medication and disclosure candidates are judged in batches. Unsupported note assertions — the recall-critical case, where a hallucinated medication must never slip through — instead get one focused, self-verifying pass each.

6. **Validate**
   The citation gate verifies every cited span against the original source before an item can be shown.

7. **Rank**
   Duplicate findings are merged and clinically meaningful items are placed in a severity-ranked review queue.

## Safety design

Three rules are enforced in code:

### No evidence, no alert

Every surfaced discrepancy must contain valid anchors to the original transcript, note, AVS, or FHIR resource.

Implementation: `guardian/anchors.py`

### Materiality before classification

A textual difference is not automatically treated as a clinical discrepancy. The system first determines whether clarification could affect care, documentation, or patient safety.

### Human review is mandatory

recon never:

* writes to the EHR;
* marks the patient or chart as correct;
* changes a medication or allergy;
* submits an order;
* makes a diagnosis.

It proposes clarification. The clinician remains the decision-maker.

## Evaluation

The evaluation harness measures three behaviors:

| Metric                       | Question                                                                                     |
| ---------------------------- | -------------------------------------------------------------------------------------------- |
| **Actionable precision**     | Of the surfaced discrepancies, how many warrant clinician review?                            |
| **Benign suppression**       | Does the system remain silent on paraphrases, synonyms, and harmless formatting differences? |
| **Unsupported-claim recall** | Does it detect clinically meaningful note claims with no support in the transcript or chart? |

<!-- EVAL_RESULTS -->

See `evals/` for the evaluation cases, expected outputs, and scoring implementation.

## Repository structure

```text
recon/       Clinician-facing web application and cached demo results
guardian/    Extraction, normalization, reconciliation, judgment, and citation validation
evals/       Evaluation cases and scoring harness
data/        Local synthetic dataset directory; dataset files are not committed
DEMO.md      Three-minute demonstration script, slide outline, and judge Q&A
```

## Tech stack

| Layer | Choice | Notes |
| ----- | ------ | ----- |
| **Language** | Python 3.9+ | |
| **Model** | `claude-opus-4-8` | Adaptive thinking; the judge runs at high effort because correctness is the product |
| **Model interface** | Anthropic Messages API, `anthropic>=0.117` | Structured outputs (`output_config.format`) — every response is schema-valid JSON, so no output parsing or repair |
| **Terminology** | RxNorm codes, with normalized-string and brand→generic fallback | Deterministic; no model involvement |
| **Clinical data** | HL7 FHIR (US Core–style resources) | Parsed with plain Python |
| **Web server** | Python standard library `ThreadingHTTPServer` | No framework, no third-party dependency |
| **Frontend** | Single self-contained `index.html` — vanilla JS and CSS | No build step, no bundler, no framework; Google Fonts with a system-serif fallback |
| **Storage** | JSON files on disk (`recon/cache/`) | Precomputed demo audits; no database |
| **Evaluation** | Standard-library harness in `evals/` | Hand-labeled gold set plus in-memory injected hallucinations |

The only third-party dependency in the entire project is the Anthropic SDK. Everything else is the Python standard library, which keeps the safety-critical logic — parsing, normalization, and the citation gate — free of dependency surface.

## Run locally

### Requirements

* Python 3.9 or newer
* An Anthropic API key
* The hackathon's synthetic ambient-FHIR dataset

### Installation

```bash
git clone https://github.com/britneyf/recon.git
cd recon

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

Add your credential to `.env`:

```text
ANTHROPIC_API_KEY=your_key_here
```

### Add the dataset

Place the following file under `data/`:

```text
data/synthetic-ambient-fhir-25.jsonl
```

The dataset is not committed to this repository because it was provided separately for the hackathon.

### Launch the web application

```bash
python3 -m recon --build
python3 -m recon
```

Open:

```text
http://localhost:8000
```

### Run individual examples

```bash
python3 -m guardian 10 --demo
python3 -m guardian 12 --demo
```

### Run evaluations

```bash
python3 -m evals.run
```

### Run deterministic checks

These checks do not require an API key:

```bash
python3 -m guardian.selftest
python3 -m guardian.screen
```

They validate FHIR parsing, terminology normalization, citation anchors, and discrepancy-surface ranking.

## Dataset and privacy

The project uses a fully synthetic ambient-FHIR corpus. No real patient information is included in this repository.

The current prototype is intended for research and demonstration only. It has not been clinically validated and should not be used to make patient-care decisions.

## Limitations

The prototype currently:

* depends on the completeness and accuracy of the supplied transcript and FHIR resources;
* may miss discrepancies expressed indirectly or across multiple encounters;
* does not establish whether the patient or existing chart is correct;
* does not replace medication reconciliation or clinician chart review;
* has not been evaluated on production EHR data;
* may require organization-specific terminology and workflow configuration.

## Roadmap

* Real-time reconciliation during an ambient encounter
* Clinician feedback and alert-dismissal learning
* Broader terminology support beyond medications
* Configurable materiality thresholds by specialty
* SMART on FHIR integration
* Prospective evaluation with clinicians
* Audit logs for every surfaced and suppressed candidate

## Team

Built by Britney Forsyth for **The Future of Agentic AI in Healthcare Hackathon**.

## Acknowledgments

* Abridge for providing the synthetic ambient-FHIR hackathon corpus
* HL7 FHIR and US Core
* RxNorm and associated terminology resources

## License

Released under the MIT License. See [LICENSE](LICENSE).
