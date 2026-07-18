"""CLI: python -m guardian [record] [--show-suppressed] [--demo] [--json]

Runs the full vertical slice on one record and prints severity-ranked cards,
plus (optionally) the benign differences the Guardian considered and dismissed.

Recommended live-demo commands:
  python -m guardian 10 --demo   # the catch: one real Major med-rec discrepancy
  python -m guardian 12 --demo   # the restraint: correct silence on an initiation visit
"""

from __future__ import annotations

import argparse
import json
import sys

from . import DEFAULT_DATASET
from .data import get_record
from .pipeline import audit_record

_SEV_ICON = {"Critical": "🔴", "Major": "🟠", "Minor": "🟡"}


def _print_card(f: dict) -> None:
    icon = _SEV_ICON.get(f["severity"], "•")
    print(f"\n{icon}  {f['severity'].upper()}  (type {f['type']})  —  {f['title']}")
    print(f"    Why it matters: {f['rationale']}")
    for label, text in (("transcript", f.get("transcript_citation")),
                        ("note", f.get("note_citation")),
                        ("FHIR", f.get("fhir_citation"))):
        if text:
            print(f"    ▸ {label}: {text}")
    if f.get("self_verification"):
        print(f"    ✓ self-verify: {f['self_verification']}")
    if f.get("proposed_resolution"):
        print(f"    → resolution: {f['proposed_resolution']}")


def _print_suppressed(result: dict) -> None:
    benign = [f for f in result.get("suppressed", []) if f.get("suppressed_reason")]
    dropped = result.get("dropped", [])
    if not benign and not dropped:
        print("\n(no suppressed candidates recorded for this record)")
        return
    print("\n" + "-" * 70)
    print("SUPPRESSED — considered and dismissed (why the Guardian stays quiet):")
    print("-" * 70)
    for f in benign:
        title = f.get("title") or f.get("_group", "candidate")
        print(f"  ✗ {title}")
        print(f"      reason: {f['suppressed_reason']}")
    for f in dropped:
        print(f"  ✗ {f.get('title', 'candidate')}  (type {f.get('type', '?')})")
        print(f"      dropped: {f['_drop_reason']} — no evidence, no alert")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="guardian", description=__doc__)
    parser.add_argument("index", nargs="?", type=int, default=12,
                        help="record index to audit (default: 12)")
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--show-suppressed", action="store_true",
                        help="also show the benign differences that were dismissed")
    parser.add_argument("--demo", action="store_true",
                        help="scripted demo framing; implies --show-suppressed")
    parser.add_argument("--inject", action="append", metavar="SPAN", default=[],
                        help="append a fake line to the note before auditing (repeatable) "
                             "— for the live 'watch it catch a hallucination' beat")
    parser.add_argument("--json", action="store_true",
                        help="emit the raw result as JSON instead of cards")
    args = parser.parse_args(argv)

    if args.demo and not args.json:
        print("\n" + "=" * 70)
        print("DEMO — Medication reconciliation. The scribe produced a clean-looking")
        print("note. The Guardian cross-checks it against the conversation and the")
        print("structured chart, and surfaces only what a clinician would act on.")
        print("=" * 70 + "\n")

    # --inject: splice fake note lines in-memory (source dataset never touched),
    # so a live demo can show the Guardian catch a planted hallucination.
    record = None
    if args.inject:
        record = get_record(args.dataset, args.index)
        added = "\n".join(f"- {span}" for span in args.inject)
        record["note"] = record["note"].rstrip() + "\n\n" + added + "\n"
        if not args.json:
            print(f"[injected {len(args.inject)} planted line(s) into the note "
                  f"— not spoken in the visit, not in the chart]\n")

    result = audit_record(args.index, dataset=args.dataset,
                          verbose=not args.json, record=record)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    surfaced = result["surfaced"]
    print("\n" + "=" * 70)
    print(f"RECONCILIATION GUARDIAN — record {result['record_index']}: "
          f"{result['visit_title']}")
    print(f"Extracted {result['ledger_sizes']['transcript']} transcript + "
          f"{result['ledger_sizes']['note']} note claims; "
          f"{len(result.get('suppressed', []))} judged benign, "
          f"{len(result.get('dropped', []))} dropped for missing evidence.")
    print("=" * 70)

    if not surfaced:
        print("\n✓  No clinically actionable discrepancies detected — "
              "ready for clinician sign-off.")
    else:
        print(f"\n{len(surfaced)} card(s) to review, severity-ranked:")
        for f in surfaced:
            _print_card(f)

    if args.show_suppressed or args.demo:
        _print_suppressed(result)

    if args.demo:
        print("\n" + "-" * 70)
        print("The same engine handles four more discrepancy classes "
              "(note hallucination, dropped disclosure, uncaptured order, AVS drift).")
        print("It proposes; a human decides. It never writes to the chart.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
