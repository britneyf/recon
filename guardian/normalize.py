"""Clinical-entity normalization used to join the three sources.

Deliberately simple: the reconciliation join only needs to be good enough to
*bundle* evidence per entity. The Judge sees the full transcript, note, and
chart, so it does the real fuzzy matching and can recover from a mis-bucket.
"""

from __future__ import annotations

import re

# Dose / form / frequency tokens that are not part of the drug name.
_STOP_TOKENS = {
    "mg", "mcg", "ml", "g", "unit", "units", "oral", "tablet", "tablets",
    "capsule", "capsules", "cap", "caps", "tab", "tabs", "solution", "suspension",
    "injection", "injectable", "daily", "po", "of", "the", "a", "an",
    "extended", "release", "er", "xr", "sr", "hr", "actuat", "actuation",
    # salt / form descriptors — strip so the drug name is the key
    "hcl", "hydrochloride", "sodium", "potassium", "succinate", "tartrate",
    "mononitrate", "besylate", "maleate", "sulfate", "citrate", "acetate",
    "phosphate", "fumarate", "bitartrate",
}


def normalize_med(name: str) -> str:
    """Reduce a medication label to a joinable drug-name key.

    'Hydrochlorothiazide 25 MG Oral Tablet'      -> 'hydrochlorothiazide'
    'Acetaminophen 325 MG Oral Tablet [Tylenol]' -> 'acetaminophen'
    'amLODIPine 2.5 MG Oral Tablet'              -> 'amlodipine'
    """
    if not name:
        return ""
    s = name.lower()
    s = re.sub(r"\[.*?\]", " ", s)          # drop brand brackets
    s = re.sub(r"[^a-z0-9 ]", " ", s)       # strip punctuation
    tokens = [t for t in s.split() if t and not t.isdigit() and t not in _STOP_TOKENS]
    # The generic drug name is (almost) always the leading alphabetic token.
    return tokens[0] if tokens else s.strip()


def brand_aliases(name: str) -> list[str]:
    """Brand names carried in brackets, e.g. '[Tylenol]' -> ['tylenol']."""
    return [b.strip().lower() for b in re.findall(r"\[(.*?)\]", name or "")]


def med_keys(name: str) -> set[str]:
    """All keys a medication label can match on (generic + any brand)."""
    keys = {normalize_med(name)}
    keys.update(brand_aliases(name))
    return {k for k in keys if k}
