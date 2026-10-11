from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def post(items, **options):
    with TestClient(app) as client:
        return client.post("/api/v1/repurpose", json={"items": items, "options": options})


def component(name, quantity=1, condition="working"):
    return {"name": name, "kind": "component", "quantity": quantity, "condition": condition}


def test_dark_night_light_is_ready_and_pot_is_substitution():
    response = post([
        component("ldr_light_sensor"), component("bjt_transistor"), component("potentiometer"),
        component("led"), component("resistor", 2), component("usb_power_bank"),
    ])
    assert response.status_code == 200
    project = next(row for row in response.json()["recommendations"] if row["project_id"] == "proj_dark_activated_night_light")
    assert project["tier"] == "ready"
    assert project["feasibility_score"] >= 90
    pot = next(row for row in project["requirements"] if row["requirement_id"] == "req_threshold_pot")
    assert pot["satisfied_by"][0]["component_id"] == "potentiometer"
    assert pot["satisfied_by"][0]["via"] == "substitution"
    resistors = next(row for row in project["requirements"] if row["requirement_id"] == "req_resistors")
    assert all(part["component_id"] != "fuse" for part in resistors["satisfied_by"])


def test_soil_alert_is_ready_at_100():
    response = post([component(name) for name in ["arduino_uno", "soil_moisture_sensor", "led", "resistor", "buzzer"]])
    assert response.status_code == 200
    project = next(row for row in response.json()["recommendations"] if row["project_id"] == "proj_soil_moisture_alert")
    assert project["tier"] == "ready"
    assert project["feasibility_score"] == 100


def test_full_detail_returns_original_build_instruction_payload():
    response = post([component("arduino_uno")], min_score=0, max_results=50, detail="full")
    assert response.status_code == 200
    project = next(row for row in response.json()["recommendations"] if row["project_id"] == "proj_soil_moisture_alert")
    assert project["build"]["full_instructions"] is not None
    assert project["build"]["full_instructions"]["steps"]


def test_feature_phone_defective_battery_is_excluded_and_controller_is_shopping_item():
    response = post([{"name": "feature_phone", "kind": "device", "condition": "defective", "defective_components": ["battery"]}])
    assert response.status_code == 200
    inventory = response.json()["resolved_inventory"][0]
    assert any(row["component_id"] == "battery" and row["disposal_note"] for row in inventory["excluded_defective"])
    unit_ids = {row["component_id"] for row in inventory["units"]}
    assert {"keypad", "speaker", "microphone", "vibration_motor"} <= unit_ids
    assert all(project["tier"] != "ready" for project in response.json()["recommendations"])
    assert any("controller" in item["for_requirement"] for project in response.json()["recommendations"] for item in project["shopping_list"])


def test_defective_device_without_parts_prompts_with_catalog_suggestions():
    item = post([{"name": "feature_phone", "kind": "device", "condition": "defective"}], min_score=0).json()["resolved_inventory"][0]
    assert item["prompt_user_to_specify_defects"] is True
    assert item["defect_suggestions"] == ["battery", "lcd_display", "usb_connector"]


def test_desktop_pc_fan_bundle_fills_motor_and_blade_with_one_unit():
    response = post([{"name": "desktop_pc", "kind": "device"}])
    assert response.status_code == 200
    project = next(row for row in response.json()["recommendations"] if row["project_id"] == "proj_temperature_controlled_fan")
    motor = next(row for row in project["requirements"] if row["requirement_id"] == "req_fan_motor")
    blade = next(row for row in project["requirements"] if row["requirement_id"] == "req_fan_blade")
    assert motor["satisfied_by"][0]["component_id"] == "cooling_fan"
    assert blade["satisfied_by"][0]["component_id"] == "cooling_fan"
    assert "bundle" in (motor["satisfied_by"][0]["note"] or "").lower()
    assert any("controller" in item["for_requirement"] for item in project["shopping_list"])
    assert any(item["component_id"] == "mosfet" for item in project["shopping_list"])


def test_empty_inventory_browses_every_project_without_scores():
    response = post([])
    assert response.status_code == 200
    data = response.json()
    assert data["meta"]["mode"] == "browse"
    assert len(data["recommendations"]) == 36
    assert all(project["feasibility_score"] is None and project["tier"] == "unscored" for project in data["recommendations"])


def test_full_browse_validates_every_catalog_build_payload():
    response = post([], detail="full")
    assert response.status_code == 200
    projects = response.json()["recommendations"]
    assert len(projects) == 36
    assert all(project["build"]["full_instructions"] is not None for project in projects)


def test_mobile_charger_is_ambiguous_without_kind_and_resolves_with_kind():
    auto = post([{"name": "mobile charger"}]).json()
    item = auto["resolved_inventory"][0]
    assert item["status"] == "ambiguous"
    assert {(choice["matched_id"], choice["kind"]) for choice in item["alternatives"]} >= {("phone_charger", "device"), ("usb_power_adapter", "component")}
    for kind, expected in [("device", "phone_charger"), ("component", "usb_power_adapter")]:
        resolved = post([{"name": "mobile charger", "kind": kind}]).json()["resolved_inventory"][0]
        assert resolved["matched_id"] == expected
        assert resolved["status"] == "alias"


def test_unrecognized_flux_capacitor_is_http_200():
    response = post([{"name": "flux capacitor"}])
    assert response.status_code == 200
    assert response.json()["unrecognized"][0]["input"] == "flux capacitor"


def test_microwave_shows_safety_banner_and_skips_high_voltage_parts():
    response = post([{"name": "microwave_oven", "kind": "device"}], min_score=0)
    data = response.json()
    assert data["safety_banners"]
    skipped = {row["component_id"] for row in data["resolved_inventory"][0]["skipped_for_safety"]}
    assert "transformer" in skipped
    offered = {row["component_id"] for row in data["resolved_inventory"][0]["units"]}
    assert "micro_switch" in offered and "cooling_fan" in offered
    assert "transformer" not in offered
    assert "capacitors that hold lethal charge" in data["safety_banners"][0]


def test_defective_standalone_component_is_not_reusable():
    data = post([component("led", condition="defective")]).json()
    assert data["resolved_inventory"][0]["excluded_defective"]
    assert data["best_combination"]["not_reusable"]
    assert not data["resolved_inventory"][0]["units"]


def test_score_is_monotonic_when_adding_needed_parts_and_response_is_deterministic():
    one = post([component("arduino_uno")], min_score=0, max_results=50).json()
    two = post([component("arduino_uno"), component("soil_moisture_sensor")], min_score=0, max_results=50).json()
    score_one = next(project["feasibility_score"] for project in one["recommendations"] if project["project_id"] == "proj_soil_moisture_alert")
    score_two = next(project["feasibility_score"] for project in two["recommendations"] if project["project_id"] == "proj_soil_moisture_alert")
    assert score_two >= score_one
    assert post([component("led")], min_score=0).json() == post([component("led")], min_score=0).json()


def test_validation_errors_return_422():
    assert post([{"name": "x", "quantity": 0}]).status_code == 422
    assert post([{"name": "   "}]).status_code == 422
    assert post([{"name": "led", "kind": "component", "defective_components": ["battery"]}]).status_code == 422


def test_frontend_static_assets_and_api_routes_are_served():
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert "What could you build" in client.get("/").text
        vocab = client.get("/static/vocab.json")
        assert vocab.status_code == 200 and len(vocab.json()["items"]) == 242
        schema = client.get("/openapi.json").json()
        assert "/api/v1/repurpose" in schema["paths"]
        assert "/api/v1/ai_search" in schema["paths"]
        assert sum(path.startswith("/api/") for path in schema["paths"]) == 2


def test_two_projects_cannot_consume_one_led_in_best_combination():
    from dataclasses import replace
    from types import MappingProxyType, SimpleNamespace
    from app.catalog import load_catalogs
    from app.expander import expand_inventory
    from app.matcher import build_recommendations
    from app.resolver import Resolver

    base = load_catalogs()
    project_rows = []
    for project_id in ["test_led_project_a", "test_led_project_b"]:
        project_rows.append({"project_id": project_id, "project_name": project_id, "description": "One LED project.", "category": "test",
                             "difficulty": "low", "estimated_build_time_hours": 1, "safety_level": "low",
                             "requirements": [{"requirement_id": "req_led", "role": "indicator", "category": "output_device", "required_capabilities": [],
                                               "quantity": 1, "critical": True, "candidate_component_ids": ["led"], "allow_substitutes": True}],
                             "optional_requirements": [], "spec_constraints": [], "build_instructions": {"steps": []}, "sources": [],
                             "youtube_videos": [], "needs_review": True, "confidence": "low", "review_reasons": []})
    catalogs = replace(base, projects=tuple(project_rows), exact_project_index=MappingProxyType({"led": tuple(p["project_id"] for p in project_rows)}))
    resolver = Resolver(base)
    from app.schemas import RepurposeItem
    expansion = expand_inventory([RepurposeItem(name="led", kind="component")], base, resolver)
    recommendations, combination = build_recommendations(expansion, catalogs, SimpleNamespace(detail="summary", max_difficulty="high", min_score=0, max_results=10, include_best_combination=True))
    assert len(recommendations) == 2
    assert len(combination["projects"]) == 1
    assert combination["total_waste_grams"] == 1
