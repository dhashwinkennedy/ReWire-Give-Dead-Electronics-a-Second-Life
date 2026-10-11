#!/usr/bin/env python3
"""Validate migrated catalogs and write machine-readable reports."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.validation import validate_catalog_payload  # noqa: E402


def main() -> int:
    data = ROOT / "data"
    components = json.loads((data / "component_catalog.json").read_text(encoding="utf-8"))
    devices = json.loads((data / "device_salvage_catalog.json").read_text(encoding="utf-8"))
    projects = json.loads((data / "project_catalog.json").read_text(encoding="utf-8"))
    substitutions = json.loads((data / "substitution_catalog.json").read_text(encoding="utf-8"))["rows"]
    report = validate_catalog_payload(components, devices, projects, substitutions)
    (data / "validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (data / "alias_collisions.json").write_text(json.dumps({"collisions": report["alias_collisions"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for error in report["errors"]:
        print(f"ERROR: {error}")
    for warning in report["warnings"]:
        print(f"WARNING: {warning}")
    print(f"Validation: {report['error_count']} error(s), {report['warning_count']} warning(s), "
          f"{report['alias_collision_count']} alias collision(s), {len(report['orphaned_salvage_components'])} orphaned salvage component(s).")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
