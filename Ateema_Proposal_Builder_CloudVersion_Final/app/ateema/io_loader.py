from __future__ import annotations
from pathlib import Path
from typing import Dict, Tuple, Any
import json

from .models import ProductRecord

def _norm_duration_keys(dqm: dict | None) -> dict:
    if not dqm:
        return {}
    out = {}
    for k, v in dqm.items():
        out[(str(k)).upper()] = v
    return out

def load_products(path: Path) -> tuple[dict[str, ProductRecord], dict[str, dict]]:
    """Load product JSON files from a folder.

    Returns (catalog, meta) where:
      - catalog maps product name -> ProductRecord (now includes new fields)
      - meta carries light attributes incl. category and option-level notes_map
    """
    catalog: Dict[str, ProductRecord] = {}
    meta: Dict[str, dict] = {}

    for p in sorted(path.glob("*.json")):
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)

        name = data.get("product_name") or data.get("name") or p.stem
        price_options = data.get("price_options") or data.get("options") or []
        cat = (
            data.get("category")
            or (data.get("categories")[0] if isinstance(data.get("categories"), list) and data.get("categories") else None)
        )
        dqm = _norm_duration_keys(data.get("duration_quarter_map"))

        # NEW fields (product level)
        product_description = data.get("product_description") or data.get("description")
        sales_strategy = data.get("sales_strategy")
        discount_policy = data.get("discount_policy")

        # NEW seasonal pricing loader
        seasonal_price_windows = data.get("seasonal_price_windows", {})

        # Option-level notes map: { option_name -> notes_string }
        notes_map: Dict[str, str] = {}
        for opt in price_options:
            opt_name = opt.get("name") or name
            note = opt.get("notes")
            if note:
                notes_map[str(opt_name)] = str(note)

        catalog[name] = ProductRecord(
            name=name,
            price_options=price_options,
            category=cat,
            duration_quarter_map=dqm,
            product_description=product_description,
            sales_strategy=sales_strategy,
            discount_policy=discount_policy,
        )

        meta[name] = {
            "category": cat,
            "notes_map": notes_map,
            "product_description": product_description,
            "sales_strategy": sales_strategy,
            "discount_policy": discount_policy,
            "seasonal_price_windows": seasonal_price_windows
        }

    return catalog, meta
# Test Yuchen

