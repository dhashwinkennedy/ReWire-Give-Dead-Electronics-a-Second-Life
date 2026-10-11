from app.catalog import load_catalogs
from app.expander import expand_inventory
from app.resolver import Resolver
from app.schemas import RepurposeItem


def test_unknown_device_parts_get_test_factor():
    catalogs = load_catalogs()
    result = expand_inventory([RepurposeItem(name="feature_phone", kind="device", condition="unknown")], catalogs, Resolver(catalogs))
    assert result.resolved_inventory[0]["units"]
    assert all(row["condition_factor"] == 0.7 for row in result.resolved_inventory[0]["units"])


def test_smoke_detector_surfaces_americium_warning():
    catalogs = load_catalogs()
    result = expand_inventory([RepurposeItem(name="smoke_detector", kind="device")], catalogs, Resolver(catalogs))
    assert any("americium" in banner.lower() and "never open" in banner.lower() for banner in result.safety_banners)
