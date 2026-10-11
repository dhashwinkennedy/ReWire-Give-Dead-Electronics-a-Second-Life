from app.catalog import load_catalogs
from app.expander import expand_inventory
from app.matcher import score_project
from app.resolver import Resolver
from app.schemas import RepurposeItem


def test_one_physical_led_cannot_fill_three_led_slot_quantity():
    catalogs = load_catalogs()
    expansion = expand_inventory([RepurposeItem(name="led", kind="component", quantity=1)], catalogs, Resolver(catalogs))
    project = next(row for row in catalogs.projects if row["project_id"] == "proj_parking_assist_sensor")
    scored = score_project(project, expansion.lots, {expansion.lots[0].lot_id: 1}, expansion, catalogs)
    led_slot = next(row for row in scored.recommendation["requirements"] if row["requirement_id"] == "req_leds")
    assert abs(led_slot["fill"] - 1 / 3) < 0.001
    assert led_slot["satisfied_by"][0]["qty"] == 1
