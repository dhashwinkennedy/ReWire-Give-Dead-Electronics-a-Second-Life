from app.catalog import load_catalogs
from app.resolver import Resolver


def test_cross_catalog_alias_collision_stays_ambiguous():
    result = Resolver(load_catalogs()).resolve("mobile charger")
    assert result.status == "ambiguous"
    assert {(row["matched_id"], row["kind"]) for row in result.alternatives} == {
        ("phone_charger", "device"), ("usb_power_adapter", "component")
    }


def test_extra_modifier_does_not_auto_accept_generic_component():
    result = Resolver(load_catalogs()).resolve("flux capacitor")
    assert result.status in {"suggested", "unrecognized"}
    assert result.status != "fuzzy"
