from app.catalog import load_catalogs
from app.expander import expand_inventory
from app.resolver import Resolver
from app.schemas import RepurposeItem
from app.waste import waste_reduction


def test_waste_is_mass_weighted_and_labeled_as_estimate():
    catalogs = load_catalogs()
    expansion = expand_inventory([RepurposeItem(name="led", kind="component", quantity=2)], catalogs, Resolver(catalogs))
    result = waste_reduction({expansion.lots[0].lot_id: 1}, expansion.lots, catalogs, expansion.total_input_mass_g)
    assert result["grams"] == 1
    assert result["pct_of_input"] == 50
    assert result["label"] == "estimate"
