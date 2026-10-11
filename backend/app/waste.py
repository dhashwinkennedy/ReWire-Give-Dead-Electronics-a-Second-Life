from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.catalog import Catalogs
from app.expander import UnitLot


def waste_reduction(consumed: dict[str, int], lots: list[UnitLot], catalogs: Catalogs, input_mass_g: float) -> dict[str, Any]:
    lot_by_id = {lot.lot_id: lot for lot in lots}
    by_source: dict[tuple[str, str, float], int] = defaultdict(int)
    grams = 0.0
    for lot_id, quantity in consumed.items():
        if quantity <= 0 or lot_id not in lot_by_id:
            continue
        lot = lot_by_id[lot_id]
        mass_per_unit = lot.mass_g_override if lot.mass_g_override is not None else float(catalogs.components_by_id[lot.component_id].get("mass_g", 0))
        by_source[(lot.component_id, lot.source, mass_per_unit)] += quantity
        grams += mass_per_unit * quantity
    source_rows = [
        {"component_id": cid, "qty": quantity,
         "grams": round(mass_per_unit * quantity, 2),
         "from_source": source}
        for (cid, source, mass_per_unit), quantity in sorted(by_source.items())
    ]
    return {"grams": round(grams, 2), "pct_of_input": round(100 * grams / input_mass_g, 2) if input_mass_g else 0.0,
            "label": "estimate", "by_source": source_rows}
