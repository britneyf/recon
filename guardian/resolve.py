"""Task 3 — deterministic entity normalization (no LLM).

Keyed on RxNorm code where present, falling back to normalized-string match with
a small brand->generic map. This is the mechanism that suppresses synonym /
brand / dose false positives *before* they ever reach the Judge: if "the ACE
inhibitor", "lisinopril ten milligrams", and the coded chart entry all resolve
to the same canonical entity, they never become a candidate discrepancy.

Parsing FHIR with a model would add a hallucination surface to the tool meant to
catch hallucinations (build spec §8), so every resolution here is plain code.
"""

from __future__ import annotations

from .normalize import brand_aliases, normalize_med

# The handful of brand->generic pairs that appear (or plausibly appear, via
# patient speech) in this dataset. Bracketed brands in labels (e.g. "[Tylenol]")
# are already handled by normalize.brand_aliases; this covers spoken brands.
_BRAND_TO_GENERIC = {
    "tylenol": "acetaminophen",
    "advil": "ibuprofen",
    "motrin": "ibuprofen",
    "aleve": "naproxen",
    "lipitor": "atorvastatin",
    "zocor": "simvastatin",
    "norvasc": "amlodipine",
    "prinivil": "lisinopril",
    "zestril": "lisinopril",
    "glucophage": "metformin",
    "cozaar": "losartan",
    "lopressor": "metoprolol",
    "toprol": "metoprolol",
    "nitrostat": "nitroglycerin",
    "crestor": "rosuvastatin",
    "tenormin": "atenolol",
}


class EntityResolver:
    """Resolves any medication mention to a canonical entity for this record."""

    def __init__(self, chart: dict):
        # Map normalized drug name -> RxNorm code, learned from this record's
        # coded MedicationRequests (which carry both text and RxNorm).
        self._name_to_rxnorm: dict[str, str] = {}
        for order in chart.get("visit_medications", []):
            code = order.get("rxnorm")
            if code:
                for k in self._name_keys(order.get("text", "")):
                    self._name_to_rxnorm.setdefault(k, code)

    def _name_keys(self, name: str) -> set[str]:
        keys = {normalize_med(name)}
        keys.update(brand_aliases(name))
        return {self._degeneric(k) for k in keys if k}

    def _degeneric(self, key: str) -> str:
        return _BRAND_TO_GENERIC.get(key, key)

    def resolve(self, name: str) -> dict:
        """Return {'key': canonical-drug-name, 'rxnorm': code|None}."""
        keys = self._name_keys(name)
        rxnorm = next((self._name_to_rxnorm[k] for k in keys
                       if k in self._name_to_rxnorm), None)
        # Prefer a stable canonical key: the shortest (most generic) token.
        key = min(keys, key=len) if keys else normalize_med(name)
        return {"key": key, "rxnorm": rxnorm}

    def same_entity(self, a: str, b: str) -> bool:
        ra, rb = self.resolve(a), self.resolve(b)
        if ra["rxnorm"] and rb["rxnorm"]:
            return ra["rxnorm"] == rb["rxnorm"]
        return bool(self._name_keys(a) & self._name_keys(b))

    def keys_for(self, name: str) -> set[str]:
        """All keys a name can join on (post brand->generic)."""
        return self._name_keys(name)
