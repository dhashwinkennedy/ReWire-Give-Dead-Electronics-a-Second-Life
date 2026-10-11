from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


def normalize_alias(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _alias_map(components: list[dict[str, Any]], devices: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    found: dict[str, list[dict[str, str]]] = defaultdict(list)
    for kind, rows, id_field, name_field in (
        ("component", components, "id", "display_name"),
        ("device", devices, "device_id", "device_name"),
    ):
        for row in rows:
            for alias in [row.get(name_field, ""), *row.get("aliases", [])]:
                normalized = normalize_alias(str(alias))
                if normalized:
                    candidate = {"id": row[id_field], "kind": kind, "display_name": row.get(name_field, row[id_field])}
                    if candidate not in found[normalized]:
                        found[normalized].append(candidate)
    return found


def validate_catalog_payload(components: list[dict[str, Any]], devices: list[dict[str, Any]], projects: list[dict[str, Any]], substitutions: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    cids = [c.get("id") for c in components]
    dids = [d.get("device_id") for d in devices]
    pids = [p.get("project_id") for p in projects]
    component_ids = set(cids)
    device_ids = set(dids)
    project_ids = set(pids)
    if len(cids) != len(component_ids):
        errors.append("Duplicate component ids detected (namespace component:<id>).")
    if len(dids) != len(device_ids):
        errors.append("Duplicate device ids detected (namespace device:<id>).")
    if len(pids) != len(project_ids):
        errors.append("Duplicate project ids detected.")

    slot_ids: set[str] = set()
    capability_vocabulary = {cap for c in components for cap in c.get("capabilities", [])}
    coverage: dict[str, bool] = {}
    salvage_ids = {entry.get("component_id") for device in devices for entry in device.get("salvageable_components", [])}
    for project in projects:
        direct_coverage = False
        local_slot_ids: set[str] = set()
        for requirement in project.get("requirements", []) + project.get("optional_requirements", []):
            rid = requirement.get("requirement_id")
            if rid in local_slot_ids:
                errors.append(f"Project {project.get('project_id')} repeats requirement id {rid!r}.")
            if rid:
                local_slot_ids.add(rid)
            candidate_ids = set(requirement.get("candidate_component_ids", []))
            for cid in candidate_ids:
                if cid not in component_ids:
                    errors.append(f"Project {project.get('project_id')} requirement {rid} references missing component {cid!r}.")
                if cid in salvage_ids:
                    direct_coverage = True
            for row in substitutions:
                scope = row.get("scope") or {}
                if row.get("needed_id") in candidate_ids and row.get("substitute_id") in salvage_ids \
                        and (not scope.get("project_ids") or project.get("project_id") in scope["project_ids"]) \
                        and (not scope.get("requirement_ids") or rid in scope["requirement_ids"]):
                    direct_coverage = True
            unknown_caps = set(requirement.get("required_capabilities", [])) - capability_vocabulary
            if unknown_caps:
                warnings.append(f"Project {project.get('project_id')} requirement {rid} uses unknown capabilities: {sorted(unknown_caps)}.")
        slot_ids.update(local_slot_ids)
        coverage[project.get("project_id", "")] = direct_coverage
        constraints_by_req: dict[str, list[dict[str, Any]]] = defaultdict(list)
        reqset = {r.get("requirement_id") for r in project.get("requirements", []) + project.get("optional_requirements", [])}
        for constraint in project.get("spec_constraints", []):
            if constraint.get("requirement_id") not in reqset:
                errors.append(f"Project {project.get('project_id')} spec constraint references missing requirement {constraint.get('requirement_id')!r}.")
            if constraint.get("operator") not in {"eq", "gte", "lte", "between"}:
                errors.append(f"Project {project.get('project_id')} has unsupported spec operator {constraint.get('operator')!r}.")
            if constraint.get("spec") not in {k for component in components for k in component.get("specs", {})}:
                errors.append(f"Project {project.get('project_id')} constraint uses unknown spec {constraint.get('spec')!r}.")
            constraints_by_req[constraint.get("requirement_id", "")].append(constraint)
    for pid, covered in coverage.items():
        if not covered:
            warnings.append(f"Project {pid} has zero direct candidate coverage from salvageable device components.")

    for device in devices:
        local: set[str] = set()
        for failed_id in device.get("common_failure_components", []):
            if failed_id not in component_ids:
                errors.append(f"Device {device.get('device_id')} common_failure_components references missing component {failed_id!r}.")
        for entry in device.get("salvageable_components", []):
            cid = entry.get("component_id")
            if cid not in component_ids:
                errors.append(f"Device {device.get('device_id')} salvage row references missing component {cid!r}.")
            if cid in local:
                errors.append(f"Device {device.get('device_id')} repeats salvage component {cid!r}.")
            local.add(cid)

    for row in substitutions:
        if row.get("needed_id") not in component_ids:
            errors.append(f"Substitution row has missing needed_id {row.get('needed_id')!r}.")
        if row.get("substitute_id") not in component_ids:
            errors.append(f"Substitution row has missing substitute_id {row.get('substitute_id')!r}.")
        if not isinstance(row.get("score"), (int, float)) or not 0 <= row.get("score", -1) <= 1:
            errors.append(f"Substitution row {row.get('needed_id')}→{row.get('substitute_id')} has score outside 0..1.")
        if not row.get("note"):
            errors.append(f"Substitution row {row.get('needed_id')}→{row.get('substitute_id')} is missing a human-readable note.")
        scope = row.get("scope") or {}
        unknown_projects = set(scope.get("project_ids", [])) - project_ids
        unknown_reqs = set(scope.get("requirement_ids", [])) - slot_ids
        if unknown_projects or unknown_reqs:
            errors.append(f"Substitution row {row.get('needed_id')}→{row.get('substitute_id')} has an unknown scope: projects={sorted(unknown_projects)}, requirements={sorted(unknown_reqs)}.")

    alias_map = _alias_map(components, devices)
    collisions = [
        {"alias": alias, "normalized_alias": alias, "candidates": sorted(rows, key=lambda row: (row["kind"], row["id"]))}
        for alias, rows in sorted(alias_map.items()) if len({(row["kind"], row["id"]) for row in rows}) > 1
    ]
    capability_gaps: list[dict[str, Any]] = []
    component_by_id = {c["id"]: c for c in components}
    for project in projects:
        for req in project.get("requirements", []) + project.get("optional_requirements", []):
            required = set(req.get("required_capabilities", []))
            candidates = [component_by_id[cid] for cid in req.get("candidate_component_ids", []) if cid in component_by_id]
            if required and candidates and not any(required <= set(c.get("capabilities", [])) for c in candidates):
                capability_gaps.append({"project_id": project["project_id"], "requirement_id": req["requirement_id"], "required_capabilities": sorted(required)})
    if capability_gaps:
        warnings.append(f"{len(capability_gaps)} requirement slots have candidate lists whose capability tags do not fully cover the slot; exact-id matching remains available.")

    # A salvaged part is usable when it is a direct candidate, an approved
    # substitution, or can retrieve a compatible requirement through the same
    # capability/category discovery rule used by ``matcher.retrieve_candidates``.
    # The previous check considered direct IDs only, which incorrectly flagged
    # usable parts such as RFID readers as catalog orphans.
    used_by_projects = {cid for project in projects for req in project.get("requirements", []) + project.get("optional_requirements", [])
                        for cid in req.get("candidate_component_ids", [])}
    used_by_projects.update(row.get("substitute_id") for row in substitutions
                            if any(row.get("needed_id") in req.get("candidate_component_ids", [])
                                   and (not (row.get("scope") or {}).get("project_ids") or project["project_id"] in (row.get("scope") or {}).get("project_ids"))
                                   and (not (row.get("scope") or {}).get("requirement_ids") or req["requirement_id"] in (row.get("scope") or {}).get("requirement_ids"))
                                   for project in projects for req in project.get("requirements", []) + project.get("optional_requirements", [])))
    discovery_covered: set[str] = set()
    for cid in salvage_ids:
        component = component_by_id.get(cid)
        if not component:
            continue
        category = component.get("category")
        capabilities = set(component.get("capabilities", []))
        if any(
            requirement.get("category") == category
            and set(requirement.get("required_capabilities", [])) <= capabilities
            for project in projects
            for requirement in project.get("requirements", []) + project.get("optional_requirements", [])
        ):
            discovery_covered.add(cid)
    orphaned = sorted(salvage_ids - used_by_projects - discovery_covered)
    if orphaned:
        warnings.append(
            f"{len(orphaned)} salvaged components cannot retrieve any current project through direct, substitution, or capability matching: "
            + ", ".join(orphaned) + "."
        )
    return {
        "catalog_version": "2026.10.05", "ok": not errors, "error_count": len(errors), "warning_count": len(warnings),
        "errors": errors, "warnings": warnings, "alias_collision_count": len(collisions), "alias_collisions": collisions,
        "orphaned_salvage_components": orphaned,
        "capability_covered_salvage_components": sorted(discovery_covered - used_by_projects),
        "projects_with_zero_salvage_coverage": sorted(pid for pid, covered in coverage.items() if not covered),
        "capability_gaps": capability_gaps,
        "namespace_note": "Runtime entity keys are namespaced as component:<id> or device:<id>.",
    }
