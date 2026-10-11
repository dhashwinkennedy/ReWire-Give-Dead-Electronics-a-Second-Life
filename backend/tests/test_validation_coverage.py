from __future__ import annotations

from app.catalog import load_catalogs


def test_capability_retrievable_salvage_parts_are_not_reported_as_orphans():
    report = load_catalogs().validation_report

    # RFID readers are not a direct project candidate but are deliberately
    # discoverable for the RFID scanner requirement through capability matching.
    assert "rfid_reader" in report["capability_covered_salvage_components"]
    assert "rfid_reader" not in report["orphaned_salvage_components"]
    assert all("rfid_reader" not in warning for warning in report["warnings"])


def test_unmatched_salvage_is_reported_once_as_actionable_summary():
    report = load_catalogs().validation_report

    assert "camera_module" in report["orphaned_salvage_components"]
    orphan_warnings = [warning for warning in report["warnings"] if "cannot retrieve any current project" in warning]
    assert len(orphan_warnings) == 1
    assert "camera_module" in orphan_warnings[0]
