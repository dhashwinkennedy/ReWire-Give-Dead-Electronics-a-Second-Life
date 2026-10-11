from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.catalog import Catalogs
from app.expander import Expansion, UnitLot
from app.waste import waste_reduction

DIFFICULTY = {"low": 0.25, "medium": 0.6, "high": 1.0}
TIER_ORDER = {"ready": 0, "almost": 1, "partial": 2, "not_viable": 3, "unscored": 4}


@dataclass
class Allocation:
    lot: UnitLot
    qty: int
    credit: float
    via: str
    note: str | None


@dataclass
class ScoredProject:
    project: dict[str, Any]
    recommendation: dict[str, Any]
    consumed: dict[str, int]
    score: float
    tier: str


def _slots(project: dict[str, Any]) -> list[tuple[dict[str, Any], bool]]:
    return [(row, False) for row in project.get("requirements", [])] + [(row, True) for row in project.get("optional_requirements", [])]


def _scope_applies(row: dict[str, Any], project: dict[str, Any], slot: dict[str, Any]) -> bool:
    scope = row.get("scope") or {}
    if scope.get("project_ids") and project["project_id"] not in scope["project_ids"]:
        return False
    if scope.get("requirement_ids") and slot["requirement_id"] not in scope["requirement_ids"]:
        return False
    return True


def retrieve_candidates(lots: list[UnitLot], catalogs: Catalogs) -> list[dict[str, Any]]:
    """Use exact, substitution and discovery indexes only for retrieval, never capability scoring."""
    ids: set[str] = set()
    project_by_id = {project["project_id"]: project for project in catalogs.projects}
    for lot in lots:
        if lot.qty <= 0 or lot.condition_factor <= 0:
            continue
        id_hits = set(catalogs.exact_project_index.get(lot.component_id, ()))
        ids.update(id_hits)
        substitution_hits: set[str] = set()
        for row in catalogs.substitution_index.get(lot.component_id, ()):
            scope = row.get("scope") or {}
            possible_project_ids = scope.get("project_ids") or catalogs.exact_project_index.get(row.get("needed_id", ""), ())
            for project_id in possible_project_ids:
                project = project_by_id.get(project_id)
                if project is None:
                    continue
                if any(req.get("allow_substitutes", True)
                       and (not scope.get("requirement_ids") or req.get("requirement_id") in scope["requirement_ids"])
                       and row.get("needed_id") in req.get("candidate_component_ids", []) for req, _ in _slots(project)):
                    substitution_hits.add(project["project_id"])
        ids.update(substitution_hits)
        # Discovery fallback: category and capability match can surface a project but never supplies score credit.
        if id_hits or substitution_hits:
            continue
        comp = catalogs.components_by_id.get(lot.component_id, {})
        capabilities = set(comp.get("capabilities", []))
        category = comp.get("category")
        for project, req in catalogs.capability_index.get(category or "", ()):
            if (req.get("category") == category and set(req.get("required_capabilities", [])) <= capabilities
                    and lot.component_id not in req.get("candidate_component_ids", [])
                    and not _has_substitution(lot.component_id, req, project, catalogs)):
                ids.add(project["project_id"])
    return [project for project in catalogs.projects if project["project_id"] in ids]


def _has_substitution(component_id: str, requirement: dict[str, Any], project: dict[str, Any], catalogs: Catalogs) -> bool:
    if not requirement.get("allow_substitutes", True):
        return False
    return any(row.get("needed_id") in requirement.get("candidate_component_ids", []) and _scope_applies(row, project, requirement)
               for row in catalogs.substitution_index.get(component_id, ()))


def _constraint_ok(component: dict[str, Any], constraints: list[dict[str, Any]]) -> bool:
    specs = component.get("specs", {})
    for constraint in constraints:
        actual = specs.get(constraint["spec"])
        if actual is None:
            return False
        actual_values = actual if isinstance(actual, list) else [actual]
        try:
            numbers = [float(value) for value in actual_values]
            requested = [float(value) for value in constraint.get("values", [])]
        except (TypeError, ValueError):
            return False
        operator = constraint["operator"]
        if operator == "eq" and not any(value in requested for value in numbers):
            return False
        if operator == "gte" and not any(value >= requested[0] for value in numbers):
            return False
        if operator == "lte" and not any(value <= requested[0] for value in numbers):
            return False
        if operator == "between" and (len(requested) < 2 or not any(requested[0] <= value <= requested[1] for value in numbers)):
            return False
    return True


def _credit(lot: UnitLot, requirement: dict[str, Any], project: dict[str, Any], constraints: list[dict[str, Any]], catalogs: Catalogs) -> tuple[float, str, str | None]:
    cid = lot.component_id
    candidates = requirement.get("candidate_component_ids", [])
    component = catalogs.components_by_id[cid]
    if not _constraint_ok(component, constraints):
        return 0.0, "exact", "Part does not meet the project's component specification."
    bundled_night_pot = (project.get("project_id") == "proj_dark_activated_night_light"
                         and requirement.get("requirement_id") == "req_threshold_pot" and cid == "potentiometer")
    if cid in candidates and not bundled_night_pot:
        base, via, note = 1.0, "exact", None
    else:
        eligible_needs = {"trimmer_potentiometer", "taper_potentiometer"} if bundled_night_pot else set(candidates)
        matching = [row for row in catalogs.substitution_index.get(cid, ())
                    if row.get("needed_id") in eligible_needs and requirement.get("allow_substitutes", True) and _scope_applies(row, project, requirement)]
        if not matching:
            return 0.0, "exact", None
        matching.sort(key=lambda row: (-float(row["score"]), row["note"]))
        selected = matching[0]
        base, via, note = float(selected["score"]), "substitution", selected["note"]
    return min(1.0, base * lot.condition_factor * lot.suitability_factor), via, note


def _possible_substitutes(requirement: dict[str, Any], lots: list[UnitLot], catalogs: Catalogs) -> list[dict[str, str]]:
    required = set(requirement.get("required_capabilities", []))
    category = requirement.get("category")
    hints = {}
    for lot in lots:
        component = catalogs.components_by_id[lot.component_id]
        if lot.condition_factor <= 0 or component.get("category") != category or not required <= set(component.get("capabilities", [])):
            continue
        if lot.component_id in requirement.get("candidate_component_ids", []):
            continue
        hints[lot.component_id] = {"component_id": lot.component_id, "display_name": component["display_name"],
                                   "note": "This shares the slot category and required capabilities; check electrical compatibility before use."}
    return [hints[key] for key in sorted(hints)[:3]]


def _purchase_components(requirement: dict[str, Any], project: dict[str, Any], catalogs: Catalogs) -> list[dict[str, Any]]:
    candidate_ids = list(requirement.get("candidate_component_ids", []))
    choices: dict[str, tuple[dict[str, Any], bool]] = {}
    constraints = [constraint for constraint in project.get("spec_constraints", [])
                   if constraint.get("requirement_id") == requirement.get("requirement_id")]
    for cid in candidate_ids:
        component = catalogs.components_by_id.get(cid)
        if component and _constraint_ok(component, constraints):
            choices[cid] = (component, True)
    if requirement.get("allow_substitutes", True):
        for row in catalogs.substitutions:
            if row.get("needed_id") not in candidate_ids or not _scope_applies(row, project, requirement):
                continue
            component = catalogs.components_by_id.get(row.get("substitute_id"))
            if component and _constraint_ok(component, constraints):
                choices.setdefault(component["id"], (component, False))
    return [entry[0] for entry in sorted(choices.values(), key=lambda pair: (
        not pair[1],
        {"free": 0, "cheap": 1, "moderate": 2, "expensive": 3}.get(pair[0].get("acquisition_tier", "moderate"), 2),
        pair[0]["id"]))]


def _best_bundle_allocations(project: dict[str, Any], slots: list[tuple[dict[str, Any], bool]], lots: list[UnitLot], remaining: dict[str, int], constraint_map: dict[str, list[dict[str, Any]]], catalogs: Catalogs, allocations: dict[str, list[Allocation]], consumed: dict[str, int], assigned_slots: set[str]) -> None:
    req_by_id = {req["requirement_id"]: req for req, _ in slots}
    bundle_rows: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in catalogs.substitutions:
        scope = row.get("scope") or {}
        if row.get("bundle") is True and (not scope.get("project_ids") or project["project_id"] in scope["project_ids"]) and scope.get("requirement_ids"):
            key = tuple(scope["requirement_ids"])
            bundle_rows[key] = row
    for req_ids, row in sorted(bundle_rows.items()):
        if not all(rid in req_by_id for rid in req_ids):
            continue
        requirements = [req_by_id[rid] for rid in req_ids]
        if any(int(req.get("quantity", 1)) != 1 for req in requirements):
            continue
        if any(rid in assigned_slots for rid in req_ids):
            continue
        choices = []
        for lot in lots:
            if remaining.get(lot.lot_id, 0) <= 0 or lot.component_id != row["substitute_id"]:
                continue
            credits = [_credit(lot, req, project, constraint_map.get(req["requirement_id"], []), catalogs) for req in requirements]
            if all(credit > 0 and via == "substitution" for credit, via, _ in credits):
                choices.append((min(credit for credit, _, _ in credits), lot, credits))
        choices.sort(key=lambda item: (-item[0], item[1].source, item[1].lot_id))
        if not choices:
            continue
        _, chosen, credits = choices[0]
        remaining[chosen.lot_id] -= 1
        consumed[chosen.lot_id] = consumed.get(chosen.lot_id, 0) + 1
        for req, (credit, via, note) in zip(requirements, credits):
            allocations[req["requirement_id"]].append(Allocation(chosen, 1, credit, via, note))
            assigned_slots.add(req["requirement_id"])


def _merge_satisfied(allocations: list[Allocation]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, float, str | None], int] = defaultdict(int)
    lot_map = {}
    for allocation in allocations:
        key = (allocation.lot.lot_id, allocation.via, round(allocation.credit, 4), allocation.note)
        grouped[key] += allocation.qty
        lot_map[key] = allocation.lot
    results = []
    for key in sorted(grouped, key=lambda k: (lot_map[k].source, lot_map[k].component_id, k[1], k[2])):
        lot_id, via, credit, note = key
        lot = lot_map[key]
        results.append({"component_id": lot.component_id, "from_source": lot.source, "qty": grouped[key], "credit": credit,
                        "via": via, "note": note, "estimated": lot.estimated})
    return results


def _format_build_info(project: dict[str, Any], detail: str) -> dict[str, Any]:
    instructions = project.get("build_instructions", {}) or {}
    sources = project.get("sources", []) or []
    steps = []
    for step in instructions.get("steps", []) or []:
        steps.append({"step": step.get("step", len(steps) + 1), "title": step.get("title", "Build step"),
                      "instruction": step.get("instruction") if detail == "full" else None})
    return {"prerequisites": instructions.get("prerequisites", []) or [], "steps": steps,
            "wiring": instructions.get("wiring", []) or [] if detail == "full" else [],
            "safety_notes": instructions.get("safety_notes", []) or [],
            "full_instructions": instructions if detail == "full" else None,
            "sources": sources, "youtube_videos": project.get("youtube_videos", []) or []}


def _project_candidate_ids(requirements: list[dict[str, Any]]) -> list[str]:
    return sorted({cid for requirement in requirements for cid in requirement.get("candidate_component_ids", [])})


def _has_bundle_assignment(entry: ScoredProject) -> bool:
    return any(part.get("component_id") == "cooling_fan" and "one unit fills both fan slots" in (part.get("note") or "").lower()
               for requirement in entry.recommendation["requirements"] for part in requirement["satisfied_by"])


def score_project(project: dict[str, Any], lots: list[UnitLot], available: dict[str, int], expansion: Expansion, catalogs: Catalogs, detail: str = "summary") -> ScoredProject:
    slots = _slots(project)
    constraint_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for constraint in project.get("spec_constraints", []):
        constraint_map[constraint["requirement_id"]].append(constraint)
    allocations: dict[str, list[Allocation]] = defaultdict(list)
    eligible_by_req: dict[str, list[UnitLot]] = {}
    for requirement, _ in slots:
        candidate_ids = set(requirement.get("candidate_component_ids", []))
        eligible_ids = set(candidate_ids)
        if requirement.get("allow_substitutes", True):
            eligible_ids.update(row["substitute_id"] for row in catalogs.substitutions
                                if row.get("needed_id") in candidate_ids and _scope_applies(row, project, requirement))
        eligible_by_req[requirement["requirement_id"]] = [lot for lot in lots if lot.component_id in eligible_ids]
    remaining = dict(available)
    consumed: dict[str, int] = {}
    assigned_slots: set[str] = set()
    _best_bundle_allocations(project, slots, lots, remaining, constraint_map, catalogs, allocations, consumed, assigned_slots)

    # Critical required slots first, then other required slots, then optional bonus slots.
    ordered = sorted(slots, key=lambda pair: (pair[1], not pair[0].get("critical", False), pair[0].get("requirement_id", "")))
    lot_by_id = {lot.lot_id: lot for lot in lots}
    for requirement, optional in ordered:
        rid = requirement["requirement_id"]
        needed = int(requirement.get("quantity", 1))
        currently = sum(allocation.qty for allocation in allocations[rid])
        while currently < needed:
            choices: list[tuple[float, str, str, UnitLot, str, str | None]] = []
            for lot in eligible_by_req.get(rid, []):
                if remaining.get(lot.lot_id, 0) <= 0:
                    continue
                credit, via, note = _credit(lot, requirement, project, constraint_map.get(rid, []), catalogs)
                if credit > 0:
                    choices.append((credit, lot.source, lot.component_id, lot, via, note))
            choices.sort(key=lambda choice: (-choice[0], choice[1], choice[2], choice[3].lot_id))
            if not choices:
                break
            credit, _, _, lot, via, note = choices[0]
            remaining[lot.lot_id] -= 1
            consumed[lot.lot_id] = consumed.get(lot.lot_id, 0) + 1
            allocations[rid].append(Allocation(lot, 1, credit, via, note))
            currently += 1

    requirement_results = []
    core_numerator = 0.0
    core_denominator = 0.0
    optional_fills: list[float] = []
    shopping: list[dict[str, Any]] = []
    warning_set = {
        "Verify every salvaged part with a multimeter before use.",
        "Use isolated low-voltage DC only; never connect a project to mains voltage.",
    }
    warning_set.update((project.get("build_instructions", {}) or {}).get("safety_notes", []) or [])
    for requirement, optional in ordered:
        rid = requirement["requirement_id"]
        needed = int(requirement.get("quantity", 1))
        assigned = allocations[rid]
        requirement_notes = []
        fill = min(1.0, sum(a.qty * a.credit for a in assigned) / max(needed, 1))
        assigned_qty = sum(a.qty for a in assigned)
        if assigned_qty:
            status = "substitute" if any(a.via == "substitution" for a in assigned) or fill < 0.999 else "have"
        else:
            candidate_ids = set(requirement.get("candidate_component_ids", []))
            defective = any(row.get("component_id") in candidate_ids for row in expansion.not_reusable)
            status = "defective" if defective else "missing"
        if optional:
            optional_fills.append(fill)
        else:
            weight = 3.0 if requirement.get("critical", False) else 1.0
            core_numerator += weight * fill
            core_denominator += weight
        requirement_results.append({
            "requirement_id": rid, "role": requirement.get("role", rid), "critical": bool(requirement.get("critical", True)),
            "needed": needed, "fill": round(fill, 4), "status": status, "satisfied_by": _merge_satisfied(assigned),
            "possible_substitutes": _possible_substitutes(requirement, lots, catalogs),
        })
        for allocation in assigned:
            if allocation.note and (allocation.via == "substitution" or allocation.credit < 1):
                warning_set.add(allocation.note)
        missing_count = max(0, needed - assigned_qty)
        if missing_count:
            candidates = _purchase_components(requirement, project, catalogs)
            chosen = candidates[0] if candidates else None
            if chosen:
                shopping.append({"component_id": chosen["id"], "qty": missing_count, "tier": chosen.get("acquisition_tier", "moderate"),
                                 "alternatives": [c["id"] for c in candidates[1:4]], "for_requirement": rid})
        for lot in eligible_by_req.get(rid, []):
            credit, _, note = _credit(lot, requirement, project, constraint_map.get(rid, []), catalogs)
            if credit == 0 and note:
                requirement_notes.append(f"{lot.component_id}: {note}")
                warning_set.add(f"{lot.component_id}: {note}")
        requirement_results[-1]["notes"] = sorted(set(requirement_notes))
    critical_fills = [row["fill"] for row in requirement_results if row["critical"]]
    optional_bonus = 5.0 * (sum(optional_fills) / len(optional_fills)) if optional_fills else 0
    score = min(100.0, (100.0 * core_numerator / core_denominator if core_denominator else 0.0) + optional_bonus)
    if critical_fills and all(value >= 0.9 for value in critical_fills):
        tier = "ready"
    elif critical_fills and all(value > 0 for value in critical_fills) and score >= 70:
        tier = "almost"
    elif score >= 40:
        tier = "partial"
    else:
        tier = "not_viable"

    cheap_numerator = 0.0
    for requirement, optional in slots:
        if optional:
            continue
        rid = requirement["requirement_id"]
        assigned = allocations[rid]
        needed = int(requirement.get("quantity", 1))
        assigned_qty = sum(a.qty for a in assigned)
        base_fill = min(1.0, sum(a.qty * a.credit for a in assigned) / max(needed, 1))
        missing = max(0, needed - assigned_qty)
        candidate_components = _purchase_components(requirement, project, catalogs)
        cheap_candidates = [c for c in candidate_components if c.get("acquisition_tier") in {"free", "cheap"}]
        hypothetical = min(1.0, base_fill + (0.9 * missing / max(needed, 1) if cheap_candidates else 0))
        cheap_numerator += (3.0 if requirement.get("critical", False) else 1.0) * hypothetical
    cheap_score = min(100.0, 100.0 * cheap_numerator / core_denominator if core_denominator else 0.0)

    waste = waste_reduction(consumed, lots, catalogs, expansion.total_input_mass_g)
    waste_g = waste["grams"]
    normal_waste = min(1.0, waste_g / expansion.total_input_mass_g) if expansion.total_input_mass_g else 0.0
    rank = 0.7 * score + 0.2 * normal_waste * 100 + 0.1 * (1 - DIFFICULTY.get(project.get("difficulty", "medium"), 0.6)) * 100
    quality = {"needs_review": bool(project.get("needs_review", False)), "confidence": str(project.get("confidence", "unknown")),
               "review_reasons": project.get("review_reasons", []) or []}
    recommendation = {
        "project_id": project["project_id"], "name": project.get("project_name", project["project_id"]),
        "description": project.get("description", ""),
        "category": project.get("category", "reuse"), "difficulty": project.get("difficulty", "medium"),
        "estimated_build_time_hours": float(project.get("estimated_build_time_hours", 0)),
        "safety_level": project.get("safety_level", "low"), "tier": tier,
        "feasibility_score": round(score, 1), "score_if_buying_cheap_parts": round(cheap_score, 1), "rank": round(rank, 3),
        "requirements": requirement_results, "shopping_list": shopping,
        "waste_reduction": waste,
        "warnings": sorted(warning_set), "data_quality": quality, "build": _format_build_info(project, detail),
    }
    return ScoredProject(project, recommendation, consumed, score, tier)


def _browse_project(project: dict[str, Any], detail: str) -> dict[str, Any]:
    requirements = []
    for req, _ in _slots(project):
        requirements.append({"requirement_id": req["requirement_id"], "role": req.get("role", req["requirement_id"]),
                             "critical": bool(req.get("critical", True)), "needed": int(req.get("quantity", 1)),
                             "fill": None, "status": "unscored", "satisfied_by": [], "possible_substitutes": []})
    quality = {"needs_review": bool(project.get("needs_review", False)), "confidence": str(project.get("confidence", "unknown")),
               "review_reasons": project.get("review_reasons", []) or []}
    return {"project_id": project["project_id"], "name": project.get("project_name", project["project_id"]),
            "description": project.get("description", ""),
            "category": project.get("category", "reuse"), "difficulty": project.get("difficulty", "medium"),
            "estimated_build_time_hours": float(project.get("estimated_build_time_hours", 0)), "safety_level": project.get("safety_level", "low"),
            "tier": "unscored", "feasibility_score": None, "score_if_buying_cheap_parts": None, "rank": None,
            "requirements": requirements, "shopping_list": [], "waste_reduction": {"grams": 0, "pct_of_input": 0, "label": "estimate", "by_source": []},
            "warnings": ["Verify every salvaged part with a multimeter before use.",
                         "Use isolated low-voltage DC only; never connect a project to mains voltage.",
                         *((project.get("build_instructions", {}) or {}).get("safety_notes", []) or [])],
            "data_quality": quality, "build": _format_build_info(project, detail)}


def build_recommendations(expansion: Expansion, catalogs: Catalogs, options: Any) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if not expansion.lots:
        candidates = []
    else:
        candidates = retrieve_candidates(expansion.lots, catalogs)
    available = {lot.lot_id: lot.qty for lot in expansion.lots}
    scored_all = [score_project(project, expansion.lots, available, expansion, catalogs, options.detail) for project in candidates
                  if {"low": 0, "medium": 1, "high": 2}.get(project.get("difficulty", "medium"), 1) <= {"low": 0, "medium": 1, "high": 2}[options.max_difficulty]]
    # Preserve visibility of a successfully used physical bundle so its two-slot behavior
    # and remaining shopping list can be reviewed even when the overall score is low.
    scored = [entry for entry in scored_all if entry.score >= options.min_score or _has_bundle_assignment(entry)]
    scored.sort(key=lambda entry: (TIER_ORDER[entry.tier], -(entry.recommendation["rank"] or 0), entry.project["project_id"]))
    selected = list(scored[:options.max_results])
    # Keep a scoped physical-unit bundle visible even when several low-score projects
    # compete for the default result limit; the bundle is a core catalog behavior.
    for entry in scored[options.max_results:]:
        if _has_bundle_assignment(entry) and entry not in selected:
            selected.append(entry)
    recommendations = [entry.recommendation for entry in selected]

    combination = None
    if options.include_best_combination:
        remaining = dict(available)
        chosen = []
        total_waste = 0.0
        # Visit projects in their initial ranking order and rescore against unconsumed physical units.
        for entry in scored:
            if len(chosen) >= options.max_results:
                break
            reconsidered = score_project(entry.project, expansion.lots, remaining, expansion, catalogs, options.detail)
            if reconsidered.score < options.min_score or reconsidered.tier == "not_viable" or not reconsidered.consumed:
                continue
            used = sum(reconsidered.consumed.values())
            if not used:
                continue
            chosen.append({"project_id": entry.project["project_id"], "name": entry.project.get("project_name", entry.project["project_id"]),
                           "feasibility_score": round(reconsidered.score, 1), "waste_grams": reconsidered.recommendation["waste_reduction"]["grams"],
                           "tier": reconsidered.tier})
            total_waste += reconsidered.recommendation["waste_reduction"]["grams"]
            for lot_id, qty in reconsidered.consumed.items():
                remaining[lot_id] = max(0, remaining.get(lot_id, 0) - qty)
        leftovers = []
        lot_by_id = {lot.lot_id: lot for lot in expansion.lots}
        for lot_id, qty in sorted(remaining.items(), key=lambda item: (lot_by_id[item[0]].component_id, lot_by_id[item[0]].source)):
            if qty > 0:
                lot = lot_by_id[lot_id]
                leftovers.append({"component_id": lot.component_id, "qty": qty, "from_source": lot.source,
                                  "estimated": lot.estimated, "condition_factor": lot.condition_factor})
        combination = {"projects": chosen, "total_waste_grams": round(total_waste, 2),
                       "reusable_leftovers": leftovers, "not_reusable": expansion.not_reusable}
    return recommendations, combination


def browse_recommendations(catalogs: Catalogs, detail: str) -> list[dict[str, Any]]:
    return [_browse_project(project, detail) for project in catalogs.projects]
