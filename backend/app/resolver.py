from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

try:
    from rapidfuzz import fuzz, process
except ImportError:  # pragma: no cover - exercised only in minimal developer environments
    fuzz = None
    process = None

from app.catalog import Catalogs
from app.validation import normalize_alias


def normalize_text(value: str) -> str:
    normalized = normalize_alias(value)
    words = []
    for word in normalized.split():
        if len(word) > 4 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        words.append(word)
    return " ".join(words)


def _ratio(a: str, b: str) -> float:
    if fuzz:
        return float(fuzz.WRatio(a, b))
    return SequenceMatcher(None, a, b).ratio() * 100


@dataclass
class Resolution:
    status: str
    matched_id: str | None = None
    kind: str | None = None
    confidence: float = 0.0
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    suggestions: list[dict[str, Any]] = field(default_factory=list)


class Resolver:
    def __init__(self, catalogs: Catalogs):
        self.catalogs = catalogs
        self._rows: dict[tuple[str, str], dict[str, Any]] = {}
        self._keys: dict[tuple[str, str], set[str]] = {("component", "id"): set(), ("device", "id"): set()}
        for component in catalogs.components:
            key = ("component", component["id"])
            self._rows[key] = component
            self._keys[("component", "id")].add(normalize_text(component["id"].replace("_", " ")))
        for device in catalogs.devices:
            key = ("device", device["device_id"])
            self._rows[key] = device
            self._keys[("device", "id")].add(normalize_text(device["device_id"].replace("_", " ")))

    def _display(self, kind: str, row: dict[str, Any]) -> str:
        return row["display_name"] if kind == "component" else row["device_name"]

    def _all_candidates(self, normalized: str, kind: str | None) -> list[dict[str, Any]]:
        source = self.catalogs.aliases.get(normalized, ())
        return [dict(candidate) for candidate in source if kind is None or candidate["kind"] == kind]

    def _candidate_score(self, name: str, candidate: dict[str, Any]) -> float:
        return _ratio(normalize_text(name), normalize_text(candidate["display_name"])) / 100

    def _as_alternatives(self, name: str, candidates: list[dict[str, Any]], preferred: tuple[str, str] | None = None) -> list[dict[str, Any]]:
        scored = []
        for candidate in candidates:
            score = self._candidate_score(name, candidate)
            pair = (candidate["kind"], candidate["id"])
            scored.append({"matched_id": candidate["id"], "display_name": candidate["display_name"], "kind": candidate["kind"],
                           "preferred": pair == preferred, "confidence": round(score, 3)})
        return sorted(scored, key=lambda item: (not item["preferred"], -item["confidence"], item["kind"], item["matched_id"]))

    def resolve(self, name: str, kind: str | None = None) -> Resolution:
        normalized = normalize_text(name)
        # Exact canonical id has highest priority. Cross-catalog ID collisions stay explicit.
        id_matches = []
        for (candidate_kind, cid), row in self._rows.items():
            if kind is not None and candidate_kind != kind:
                continue
            if normalized == normalize_text(cid.replace("_", " ")):
                id_matches.append({"id": cid, "kind": candidate_kind, "display_name": self._display(candidate_kind, row)})
        if id_matches:
            return self._resolve_candidates(name, id_matches, "exact", 1.0)

        exact_alias = self._all_candidates(normalized, kind)
        if exact_alias:
            # Determine which stored phrase matched so canonical-name equality can be classified as exact.
            canonical = [c for c in exact_alias if normalize_text(c["display_name"]) == normalized]
            if canonical:
                exact_alias = canonical + [c for c in exact_alias if c not in canonical]
                status = "exact"
            else:
                status = "alias"
            return self._resolve_candidates(name, exact_alias, status, None)

        # Token-set matching catches minor word order differences without fuzzy acceptance.
        query_tokens = set(normalized.split())
        token_matches: list[dict[str, Any]] = []
        for alias, candidates in self.catalogs.aliases.items():
            if not alias or set(alias.split()) != query_tokens:
                continue
            token_matches.extend(dict(c) for c in candidates if kind is None or c["kind"] == kind)
        if token_matches:
            dedup = {(c["kind"], c["id"]): c for c in token_matches}
            return self._resolve_candidates(name, list(dedup.values()), "alias", None)

        pool = [c for candidates in self.catalogs.aliases.values() for c in candidates if kind is None or c["kind"] == kind]
        unique = {(c["kind"], c["id"]): c for c in pool}
        scores = [(self._candidate_score(name, candidate), candidate) for candidate in unique.values()]
        scores.sort(key=lambda item: (-item[0], item[1]["kind"], item[1]["id"]))
        if not scores:
            return Resolution(status="unrecognized")
        best_score, best = scores[0]
        fuzzy_suggestions = self._as_alternatives(name, [candidate for score, candidate in scores[:5] if score >= 0.50])
        # WRatio intentionally tolerates extra words. Avoid auto-accepting a generic part
        # when a query adds an unmatched modifier (for example, "flux capacitor").
        query_tokens = set(normalized.split())
        best_row = self._rows[(best["kind"], best["id"])]
        best_phrases = [self._display(best["kind"], best_row), best["id"].replace("_", " "), *best_row.get("aliases", [])]
        has_unmatched_modifier = any(
            (tokens := set(normalize_text(phrase).split()))
            and tokens < query_tokens and tokens <= query_tokens
            for phrase in best_phrases
        )
        if has_unmatched_modifier and best_score >= 0.88:
            best_score = 0.87
        if best_score >= 0.88:
            return Resolution(status="fuzzy", matched_id=best["id"], kind=best["kind"], confidence=round(best_score, 3), alternatives=self._as_alternatives(name, [c for _, c in scores[1:4]]))
        if best_score >= 0.70:
            return Resolution(status="suggested", matched_id=best["id"], kind=best["kind"], confidence=round(best_score, 3), suggestions=fuzzy_suggestions)
        return Resolution(status="unrecognized", confidence=round(best_score, 3), suggestions=fuzzy_suggestions)

    def _resolve_candidates(self, name: str, candidates: list[dict[str, Any]], status: str, confidence: float | None) -> Resolution:
        dedup = {(c["kind"], c["id"]): c for c in candidates}
        choices = list(dedup.values())
        choices.sort(key=lambda c: (-self._candidate_score(name, c), c["kind"], c["id"]))
        if len({c["kind"] for c in choices}) > 1:
            preferred = (choices[0]["kind"], choices[0]["id"])
            return Resolution(status="ambiguous", matched_id=choices[0]["id"], kind=choices[0]["kind"],
                              confidence=round(confidence if confidence is not None else 1.0, 3),
                              alternatives=self._as_alternatives(name, choices, preferred))
        chosen = choices[0]
        score = confidence if confidence is not None else 1.0
        return Resolution(status=status, matched_id=chosen["id"], kind=chosen["kind"], confidence=round(score, 3),
                          alternatives=self._as_alternatives(name, choices[1:]))
