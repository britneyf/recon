"""Eval harness — `python -m evals.run`.

Runs the Guardian over a hand-labeled gold set of natural discrepancies plus an
overlay of injected note hallucinations (with a benign hard-negative), and
reports three metrics, headlining precision:

  1. Precision @ surfaced        = real discrepancies flagged / total flagged
  2. Benign-suppression rate     = benign differences correctly NOT flagged / all benign
  3. Hallucination-detection rate = injected hallucinations caught / injected total

Injections are applied to an in-memory copy of the note; the dataset on disk is
never modified.
"""

from __future__ import annotations

import argparse
import copy
import json
import os

from guardian import DEFAULT_DATASET
from guardian.agent import GuardianAgent
from guardian.data import get_record
from guardian.pipeline import audit_record

_HERE = os.path.dirname(__file__)


def _load(name: str) -> dict:
    with open(os.path.join(_HERE, name)) as f:
        return json.load(f)["records"]


def apply_injections(record: dict, injections: list[dict]) -> dict:
    """Append injected spans to the note (in-memory copy — never mutate source)."""
    rec = copy.deepcopy(record)
    added = "\n".join(f"- {inj['span']}" for inj in injections)
    rec["note"] = rec["note"].rstrip() + "\n\n" + added + "\n"
    return rec


def _mentions(finding: dict, entity: str) -> bool:
    hay = " ".join(filter(None, [
        finding.get("title"), finding.get("rationale"),
        finding.get("note_citation"), finding.get("transcript_citation"),
    ])).lower()
    return all(tok in hay for tok in entity.lower().split())


def _cited(f: dict) -> bool:
    return bool(f.get("note_citation") or f.get("transcript_citation")
               or f.get("fhir_citation"))


def run(dataset: str = DEFAULT_DATASET, verbose: bool = True) -> dict:
    gold = _load("gold.json")
    injections = _load("injections.json")
    agent = GuardianAgent()

    rows = []            # per-item table
    surfaced_total = 0
    surfaced_true = 0    # true positives among everything surfaced
    hall_total = hall_caught = 0
    benign_total = benign_suppressed = 0

    def log(m):
        if verbose:
            print(m, flush=True)

    # --- Natural gold records (unmodified) ---
    for idx, spec in sorted(gold.items(), key=lambda kv: int(kv[0])):
        log(f"\n▶ natural  record {idx} (expected: {spec['expected']})")
        result = audit_record(int(idx), dataset=dataset, agent=agent, verbose=False)
        surfaced = result["surfaced"]
        surfaced_total += len(surfaced)
        if spec["expected"] == "clean":
            # any surfaced finding here is a false positive
            predicted = "clean" if not surfaced else f"{len(surfaced)} flagged"
            correct = not surfaced
            rows.append((f"rec{idx} natural", "clean", predicted,
                         "-", "PASS" if correct else "FAIL"))
        else:
            want = set(spec.get("types", []))
            matched = [f for f in surfaced if f.get("type") in want]
            surfaced_true += len(matched)
            # extra surfaced findings not of an expected type count against precision
            predicted = ("+".join(f["type"] for f in surfaced) or "none")
            rows.append((f"rec{idx} natural", "+".join(want) or "discrepancy",
                         predicted, "yes" if matched and _cited(matched[0]) else "no",
                         "PASS" if matched else "FAIL"))

    # --- Injected records ---
    for idx, injs in sorted(injections.items(), key=lambda kv: int(kv[0])):
        log(f"\n▶ injected record {idx} ({len(injs)} injection(s))")
        modified = apply_injections(get_record(dataset, int(idx)), injs)
        result = audit_record(int(idx), dataset=dataset, agent=agent,
                              verbose=False, record=modified)
        surfaced = result["surfaced"]
        for inj in injs:
            hit = next((f for f in surfaced if _mentions(f, inj["entity"])), None)
            if inj["label"] == "hallucination":
                hall_total += 1
                caught = hit is not None
                if caught:
                    hall_caught += 1
                    surfaced_true += 1
                    surfaced_total += 1
                rows.append((f"rec{idx}/{inj['id']} {inj['entity']}", "catch (hallucination)",
                             "caught" if caught else "MISSED",
                             "yes" if caught and _cited(hit) else "no",
                             "PASS" if caught else "FAIL"))
            else:  # benign hard-negative
                benign_total += 1
                suppressed = hit is None
                if suppressed:
                    benign_suppressed += 1
                else:
                    surfaced_total += 1  # a benign flagged is a false positive
                rows.append((f"rec{idx}/{inj['id']} {inj['entity']}", "suppress (benign)",
                             "suppressed" if suppressed else "FLAGGED (false +)",
                             "-", "PASS" if suppressed else "FAIL"))

    precision = surfaced_true / surfaced_total if surfaced_total else 1.0
    benign_rate = benign_suppressed / benign_total if benign_total else 1.0
    recall = hall_caught / hall_total if hall_total else 1.0

    return {
        "rows": rows,
        "precision_at_surfaced": (precision, surfaced_true, surfaced_total),
        "benign_suppression_rate": (benign_rate, benign_suppressed, benign_total),
        "hallucination_recall": (recall, hall_caught, hall_total),
    }


def _print_report(r: dict) -> None:
    print("\n" + "=" * 78)
    print("RECONCILIATION GUARDIAN — EVAL REPORT")
    print("=" * 78)
    print(f"\n{'item':<34} {'expected':<22} {'predicted':<18} {'cited':<5} result")
    print("-" * 78)
    for item, exp, pred, cited, res in r["rows"]:
        print(f"{item[:33]:<34} {exp[:21]:<22} {pred[:17]:<18} {cited:<5} {res}")

    p, pn, pd = r["precision_at_surfaced"]
    b, bn, bd = r["benign_suppression_rate"]
    rc, rn, rd = r["hallucination_recall"]
    print("\n" + "-" * 78)
    print("METRICS")
    print("-" * 78)
    print(f"  Precision @ surfaced        {p:5.0%}   ({pn}/{pd} surfaced were real)   ← headline")
    print(f"  Benign-suppression rate     {b:5.0%}   ({bn}/{bd} benign correctly not flagged)")
    print(f"  Hallucination-detection     {rc:5.0%}   ({rn}/{rd} injected hallucinations caught)")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.run", description=__doc__)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = run(dataset=args.dataset, verbose=not args.json)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
