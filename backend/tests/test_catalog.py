from app.catalog import load_catalogs


def test_catalogs_load_with_namespaced_collision_and_indexes():
    catalogs = load_catalogs()
    assert "water_pump" in catalogs.components_by_id
    assert "water_pump" in catalogs.devices_by_id
    assert catalogs.exact_project_index["led"]
    assert catalogs.substitution_index["cooling_fan"]
    assert catalogs.version == "2026.10.05"
    assert not catalogs.validation_report["errors"]
