from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from app.config import CATALOG_STRICT, CATALOG_VERSION_FALLBACK, DATA_DIR
from app.exceptions import CatalogCorruptedError, CatalogNotFoundError, CatalogValidationError
from app.validation import _alias_map, validate_catalog_payload

logger = logging.getLogger("ewaste.catalog")


@dataclass(frozen=True)
class Catalogs:
    version: str
    components: tuple[dict[str, Any], ...]
    devices: tuple[dict[str, Any], ...]
    projects: tuple[dict[str, Any], ...]
    substitutions: tuple[dict[str, Any], ...]
    components_by_id: Mapping[str, dict[str, Any]]
    devices_by_id: Mapping[str, dict[str, Any]]
    aliases: Mapping[str, tuple[dict[str, str], ...]]
    exact_project_index: Mapping[str, tuple[str, ...]]
    substitution_index: Mapping[str, tuple[dict[str, Any], ...]]
    capability_index: Mapping[str, tuple[tuple[dict[str, Any], dict[str, Any]], ...]]
    validation_report: dict[str, Any]


def _read(path: Path) -> Any:
    if not path.is_file():
        raise CatalogNotFoundError(path.name, str(path))
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise CatalogCorruptedError(path.name, str(exc)) from exc
    except Exception as exc:
        raise CatalogCorruptedError(path.name, str(exc)) from exc


def _slots(project: dict[str, Any]):
    yield from project.get("requirements", [])
    yield from project.get("optional_requirements", [])


def load_catalogs(data_dir: Path = DATA_DIR, strict: bool = CATALOG_STRICT) -> Catalogs:
    """Load and index catalogs once during app lifespan; no request-time data-file reads."""
    components = _read(data_dir / "component_catalog.json")
    devices = _read(data_dir / "device_salvage_catalog.json")
    projects = _read(data_dir / "project_catalog.json")
    substitution_doc = _read(data_dir / "substitution_catalog.json")
    manifest = _read(data_dir / "catalog_manifest.json")
    substitutions = substitution_doc.get("rows", [])
    report = validate_catalog_payload(components, devices, projects, substitutions)
    if report["errors"] and strict:
        raise CatalogValidationError(report["errors"])
    for warning in report["warnings"]:
        logger.warning(json.dumps({"event": "catalog_validation_warning", "detail": warning}, ensure_ascii=False))
    if report["alias_collision_count"]:
        logger.info(json.dumps({
            "event": "catalog_alias_collisions_detected",
            "count": report["alias_collision_count"],
            "handling": "ambiguous aliases require a kind or user selection",
        }))

    exact: dict[str, set[str]] = {}
    caps: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for project in projects:
        for slot in _slots(project):
            for cid in slot.get("candidate_component_ids", []):
                exact.setdefault(cid, set()).add(project["project_id"])
            caps.setdefault(slot.get("category", ""), []).append((project, slot))
    sub_idx: dict[str, list[dict[str, Any]]] = {}
    for row in substitutions:
        sub_idx.setdefault(row["substitute_id"], []).append(row)

    aliases_raw = _alias_map(components, devices)
    aliases = {key: tuple(rows) for key, rows in aliases_raw.items()}
    return Catalogs(
        version=manifest.get("catalog_version", CATALOG_VERSION_FALLBACK),
        components=tuple(components), devices=tuple(devices), projects=tuple(projects), substitutions=tuple(substitutions),
        components_by_id=MappingProxyType({row["id"]: row for row in components}),
        devices_by_id=MappingProxyType({row["device_id"]: row for row in devices}),
        aliases=MappingProxyType(aliases),
        exact_project_index=MappingProxyType({key: tuple(sorted(value)) for key, value in exact.items()}),
        substitution_index=MappingProxyType({key: tuple(value) for key, value in sub_idx.items()}),
        capability_index=MappingProxyType({key: tuple(value) for key, value in caps.items()}),
        validation_report=report,
    )
