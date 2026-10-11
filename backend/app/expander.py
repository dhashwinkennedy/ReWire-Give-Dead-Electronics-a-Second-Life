from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.catalog import Catalogs
from app.resolver import Resolver, normalize_text

SUITABILITY_FACTOR = {"high": 1.0, "medium": 0.85, "low": 0.6}
DISPOSAL_NOTE = "Take this battery or unsafe component to a certified e-waste or battery collection point; do not put it in household waste."


@dataclass(frozen=True)
class UnitLot:
    lot_id: str
    component_id: str
    namespace_id: str
    qty: int
    condition_factor: float
    suitability_factor: float
    reuse_suitability: str
    estimated: bool
    extraction_difficulty: str | None
    availability: str | None
    source: str
    notes: tuple[str, ...] = ()
    mass_g_override: float | None = None


@dataclass
class Expansion:
    resolved_inventory: list[dict[str, Any]] = field(default_factory=list)
    unrecognized: list[dict[str, Any]] = field(default_factory=list)
    lots: list[UnitLot] = field(default_factory=list)
    safety_banners: list[str] = field(default_factory=list)
    not_reusable: list[dict[str, Any]] = field(default_factory=list)
    total_input_mass_g: float = 0.0
    items_recognized: int = 0


def _matches_defect(value: str, catalogs: Catalogs) -> str | None:
    normalized = normalize_text(value)
    if normalized in {normalize_text(cid.replace("_", " ")) for cid in catalogs.components_by_id}:
        for cid in catalogs.components_by_id:
            if normalized == normalize_text(cid.replace("_", " ")):
                return cid
    for component in catalogs.components:
        if normalized == normalize_text(component.get("display_name", "")) or normalized in {normalize_text(a) for a in component.get("aliases", [])}:
            return component["id"]
    return None


def _serialize_alt(candidate: dict[str, Any]) -> dict[str, Any]:
    return {"matched_id": candidate["matched_id"], "display_name": candidate["display_name"], "kind": candidate["kind"],
            "preferred": candidate.get("preferred", False), "confidence": candidate.get("confidence", 0.0)}


def expand_inventory(items: list[Any], catalogs: Catalogs, resolver: Resolver) -> Expansion:
    expansion = Expansion()
    for idx, item in enumerate(items):
        resolution = resolver.resolve(item.name, item.kind)
        record: dict[str, Any] = {
            "input": item.name, "status": resolution.status, "matched_id": resolution.matched_id,
            "kind": resolution.kind, "confidence": resolution.confidence,
            "alternatives": [_serialize_alt(candidate) for candidate in resolution.alternatives],
            "units": [], "excluded_defective": [], "skipped_for_safety": [],
            "prompt_user_to_specify_defects": False, "defect_suggestions": [],
            "suggestions": [_serialize_alt(candidate) for candidate in resolution.suggestions],
        }
        if resolution.status in {"unrecognized", "suggested"}:
            expansion.unrecognized.append({"input": item.name, "suggestions": record["suggestions"]})
            expansion.resolved_inventory.append(record)
            continue
        if resolution.status == "ambiguous" or resolution.matched_id is None or resolution.kind is None:
            expansion.resolved_inventory.append(record)
            continue

        expansion.items_recognized += 1
        if resolution.kind == "component":
            component = catalogs.components_by_id[resolution.matched_id]
            quantity = item.quantity
            factor = {"working": 1.0, "unknown": 0.7, "defective": 0.0}[item.condition]
            source = f"component:{component['id']}"
            if factor > 0:
                notes = ["Test this part with a multimeter before use."] if item.condition == "unknown" else []
                expansion.lots.append(UnitLot(f"input-{idx}-{component['id']}", component["id"], f"component:{component['id']}", quantity,
                                              factor, 1.0, "high", False, None, None, source, tuple(notes)))
                record["units"].append({"component_id": component["id"], "qty": quantity, "estimated": False,
                                        "condition_factor": factor, "reuse_suitability": "high", "from_source": source, "notes": notes})
            else:
                entry = {"component_id": component["id"], "qty": quantity, "reason": "User marked standalone component defective.", "disposal_note": DISPOSAL_NOTE}
                record["excluded_defective"].append(entry)
                expansion.not_reusable.append(entry)
            expansion.total_input_mass_g += float(component.get("mass_g", 0)) * quantity
            expansion.resolved_inventory.append(record)
            continue

        device = catalogs.devices_by_id[resolution.matched_id]
        expansion.total_input_mass_g += float(device.get("device_mass_g", 0)) * item.quantity
        safety_level = str(device.get("safety_level", "low")).lower()
        if safety_level == "hazardous" or device.get("hazard_note"):
            banner = f"Safety: {device['device_name']} may contain capacitors that hold lethal charge and mains/high-voltage sections. Hobbyists must not open those sections; only consider intact low-voltage parts. {device.get('hazard_note') or ''}".strip() if safety_level == "hazardous" else f"Safety: {device['device_name']}. {device.get('hazard_note')}"
            if banner not in expansion.safety_banners:
                expansion.safety_banners.append(banner)
        defective_ids = {_matches_defect(value, catalogs) for value in (item.defective_components or [])}
        defective_ids.discard(None)
        if item.condition == "defective" and not item.defective_components:
            record["prompt_user_to_specify_defects"] = True
            record["defect_suggestions"] = list(device.get("common_failure_components", []))
        for entry_no, salvage in enumerate(device.get("salvageable_components", [])):
            cid = salvage["component_id"]
            quantity = int(salvage.get("typical_quantity", 1)) * item.quantity
            if salvage.get("do_not_salvage"):
                exclusion = {"component_id": cid, "qty": quantity, "reason": salvage.get("note", "Excluded for safety."), "disposal_note": DISPOSAL_NOTE}
                record["skipped_for_safety"].append(exclusion)
                expansion.not_reusable.append(exclusion)
                continue
            # Battery cells from hazardous devices and batteries from defective devices are never reused.
            if cid == "battery" and (item.condition == "defective" or safety_level == "hazardous"):
                reason = "User-marked defective; battery from this device is never reused." if item.condition == "defective" else "Battery from a hazardous device is excluded pending qualified inspection."
                exclusion = {"component_id": cid, "qty": quantity, "reason": reason, "disposal_note": DISPOSAL_NOTE}
                record["excluded_defective"].append(exclusion)
                expansion.not_reusable.append(exclusion)
                continue
            if item.condition == "defective" and item.defective_components:
                factor = 0.0 if cid in defective_ids else 1.0
                condition_note = "User-marked defective component." if factor == 0 else "Device is defective; verify this part after extraction."
            elif item.condition == "defective":
                factor = 0.5
                condition_note = "Device is defective; inspect and test this part after extraction."
            elif item.condition == "unknown":
                factor = 0.7
                condition_note = "Test this salvaged part with a multimeter before use."
            else:
                factor = 1.0
                condition_note = ""
            if factor <= 0:
                exclusion = {"component_id": cid, "qty": quantity, "reason": condition_note or "User-marked defective component.", "disposal_note": DISPOSAL_NOTE if cid == "battery" else None}
                record["excluded_defective"].append(exclusion)
                expansion.not_reusable.append(exclusion)
                continue
            notes = [condition_note] if condition_note else []
            expansion.lots.append(UnitLot(f"device-{idx}-{entry_no}-{cid}", cid, f"component:{cid}", quantity, factor,
                                          SUITABILITY_FACTOR.get(salvage.get("reuse_suitability", "medium"), 0.85),
                                          salvage.get("reuse_suitability", "medium"), True,
                                          salvage.get("extraction_difficulty"), salvage.get("availability"),
                                          f"device:{device['device_id']}", tuple(notes),
                                          float(salvage["mass_g"]) if salvage.get("mass_g") is not None else None))
            record["units"].append({"component_id": cid, "qty": quantity, "estimated": True, "condition_factor": factor,
                                    "extraction_difficulty": salvage.get("extraction_difficulty"), "availability": salvage.get("availability"),
                                    "reuse_suitability": salvage.get("reuse_suitability"), "from_source": f"device:{device['device_id']}", "notes": notes})
        expansion.resolved_inventory.append(record)
    return expansion
