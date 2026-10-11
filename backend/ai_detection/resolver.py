from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

try:
    from rapidfuzz import fuzz, process
except ImportError:  # pragma: no cover
    fuzz = None
    process = None

from ai_detection.config import COMPONENT_CATALOG_PATH, DEVICE_CATALOG_PATH

logger = logging.getLogger("ewaste.ai.resolver")


def normalize_string(text: str) -> str:
    """Normalize text by lowering, removing punctuation, and stemming basic plurals."""
    if not text:
        return ""
    text = text.lower().replace("_", " ").replace("-", " ")
    # remove punctuation
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = text.split()
    stemmed = []
    for token in tokens:
        if len(token) > 4 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        stemmed.append(token)
    return " ".join(stemmed)


@dataclass
class CatalogResolutionResult:
    resolved: bool
    resolved_id: str | None = None
    resolved_type: Literal["component", "device"] | None = None
    display_name: str | None = None
    resolution_confidence: float = 0.0
    match_tier: str | None = None  # exact_id, exact_name, exact_alias, normalized_alias, fuzzy, unresolved
    reason: str | None = None


class CatalogResolver:
    """
    Resolves an AI-predicted label against canonical catalog files.
    Resolution order:
    1. Exact canonical ID
    2. Exact display name
    3. Exact alias
    4. Normalized alias
    5. Fuzzy matching using RapidFuzz
    6. Unresolved
    """

    def __init__(
        self,
        component_catalog_path: Path = COMPONENT_CATALOG_PATH,
        device_catalog_path: Path = DEVICE_CATALOG_PATH,
        fuzzy_threshold: float = 75.0,
    ):
        self.component_catalog_path = Path(component_catalog_path)
        self.device_catalog_path = Path(device_catalog_path)
        self.fuzzy_threshold = fuzzy_threshold

        self.components: list[dict[str, Any]] = []
        self.devices: list[dict[str, Any]] = []
        self._load_catalogs()

    def _load_catalogs(self) -> None:
        if not self.component_catalog_path.exists():
            raise FileNotFoundError(f"Component catalog not found at {self.component_catalog_path}")
        with open(self.component_catalog_path, "r", encoding="utf-8") as f:
            self.components = json.load(f)

        if not self.device_catalog_path.exists():
            raise FileNotFoundError(f"Device catalog not found at {self.device_catalog_path}")
        with open(self.device_catalog_path, "r", encoding="utf-8") as f:
            self.devices = json.load(f)

        # Build indexing dictionaries
        # 1. Canonical IDs: normalized_id -> (type, canonical_id, display_name)
        self.id_map: dict[str, tuple[Literal["component", "device"], str, str]] = {}
        # 2. Display names: normalized_name -> (type, canonical_id, display_name)
        self.display_map: dict[str, tuple[Literal["component", "device"], str, str]] = {}
        # 3. Exact raw aliases: alias.lower() -> (type, canonical_id, display_name)
        self.exact_alias_map: dict[str, tuple[Literal["component", "device"], str, str]] = {}
        # 4. Normalized aliases: normalized_alias -> (type, canonical_id, display_name)
        self.normalized_alias_map: dict[str, tuple[Literal["component", "device"], str, str]] = {}
        # 5. Flat list of all phrases for fuzzy search: (phrase, type, canonical_id, display_name)
        self.search_phrases: list[tuple[str, Literal["component", "device"], str, str]] = []

        # Index components
        for comp in self.components:
            cid = comp["id"]
            name = comp.get("display_name", cid)
            aliases = comp.get("aliases", [])

            norm_id = normalize_string(cid)
            norm_name = normalize_string(name)

            self.id_map[norm_id] = ("component", cid, name)
            self.display_map[norm_name] = ("component", cid, name)
            self.search_phrases.append((name, "component", cid, name))
            self.search_phrases.append((cid.replace("_", " "), "component", cid, name))

            for alias in aliases:
                self.exact_alias_map[alias.lower().strip()] = ("component", cid, name)
                norm_a = normalize_string(alias)
                self.normalized_alias_map[norm_a] = ("component", cid, name)
                self.search_phrases.append((alias, "component", cid, name))

        # Index devices
        for dev in self.devices:
            did = dev["device_id"]
            name = dev.get("device_name", did)
            aliases = dev.get("aliases", [])

            norm_id = normalize_string(did)
            norm_name = normalize_string(name)

            self.id_map[norm_id] = ("device", did, name)
            self.display_map[norm_name] = ("device", did, name)
            self.search_phrases.append((name, "device", did, name))
            self.search_phrases.append((did.replace("_", " "), "device", did, name))

            for alias in aliases:
                self.exact_alias_map[alias.lower().strip()] = ("device", did, name)
                norm_a = normalize_string(alias)
                self.normalized_alias_map[norm_a] = ("device", did, name)
                self.search_phrases.append((alias, "device", did, name))

    def resolve(self, label: str) -> CatalogResolutionResult:
        if not label or not label.strip():
            return CatalogResolutionResult(
                resolved=False,
                match_tier="unresolved",
                reason="Empty or null label provided for resolution",
            )

        cleaned_raw = label.strip().lower()
        normalized = normalize_string(label)

        # 1. Exact Canonical ID match
        # Check raw exact ID match (e.g. "arduino_uno" or "led")
        if cleaned_raw in self.id_map:
            rtype, cid, dname = self.id_map[cleaned_raw]
            return CatalogResolutionResult(
                resolved=True,
                resolved_id=cid,
                resolved_type=rtype,
                display_name=dname,
                resolution_confidence=1.0,
                match_tier="exact_id",
            )
        if normalized in self.id_map:
            rtype, cid, dname = self.id_map[normalized]
            return CatalogResolutionResult(
                resolved=True,
                resolved_id=cid,
                resolved_type=rtype,
                display_name=dname,
                resolution_confidence=1.0,
                match_tier="exact_id",
            )

        # 2. Exact Display Name match
        if normalized in self.display_map:
            rtype, cid, dname = self.display_map[normalized]
            return CatalogResolutionResult(
                resolved=True,
                resolved_id=cid,
                resolved_type=rtype,
                display_name=dname,
                resolution_confidence=0.99,
                match_tier="exact_name",
            )

        # 3. Exact Alias match
        if cleaned_raw in self.exact_alias_map:
            rtype, cid, dname = self.exact_alias_map[cleaned_raw]
            return CatalogResolutionResult(
                resolved=True,
                resolved_id=cid,
                resolved_type=rtype,
                display_name=dname,
                resolution_confidence=0.97,
                match_tier="exact_alias",
            )

        # 4. Normalized Alias match
        if normalized in self.normalized_alias_map:
            rtype, cid, dname = self.normalized_alias_map[normalized]
            return CatalogResolutionResult(
                resolved=True,
                resolved_id=cid,
                resolved_type=rtype,
                display_name=dname,
                resolution_confidence=0.94,
                match_tier="normalized_alias",
            )

        # 5. Fuzzy matching using RapidFuzz
        if fuzz:
            # We search across all known candidate phrases using token_sort_ratio
            best_match: tuple[str, Literal["component", "device"], str, str] | None = None
            best_score = 0.0

            # Compare against all catalog entries
            for phrase, rtype, cid, dname in self.search_phrases:
                score = float(fuzz.token_sort_ratio(normalized, normalize_string(phrase)))
                if score > best_score:
                    best_score = score
                    best_match = (phrase, rtype, cid, dname)

            if best_match and best_score >= self.fuzzy_threshold:
                _, rtype, cid, dname = best_match
                conf = round(best_score / 100.0, 2)
                return CatalogResolutionResult(
                    resolved=True,
                    resolved_id=cid,
                    resolved_type=rtype,
                    display_name=dname,
                    resolution_confidence=conf,
                    match_tier="fuzzy",
                )

        # 6. Unresolved
        return CatalogResolutionResult(
            resolved=False,
            match_tier="unresolved",
            resolution_confidence=0.0,
            reason=f"Label '{label}' could not be matched to any supported component or device in the catalog",
        )
