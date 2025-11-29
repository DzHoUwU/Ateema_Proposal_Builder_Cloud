from __future__ import annotations
from typing import Dict
from .models import ProductRecord, Selection
from .pricing import effective_line_price

def format_product_block(catalog: Dict[str, ProductRecord], meta: Dict[str, dict]) -> str:
    lines = []
    for name, rec in catalog.items():
        cat = meta.get(name, {}).get("category") or rec.category or "—"
        lines.append(f"* {name}  [category: {cat}]")
        for opt in rec.price_options:
            opt_name = opt.get("name", name)
            if isinstance(opt.get("price_usd"), dict):
                price_map = opt["price_usd"]
            elif isinstance(opt.get("price_usd_by_plan"), dict):
                price_map = opt["price_usd_by_plan"]
            elif isinstance(opt.get("price_usd"), (int, float)):
                price_map = {"base": opt["price_usd"]}
            elif isinstance(opt.get("pricing"), dict):
                price_map = opt["pricing"]
            else:
                price_map = {}
            if price_map:
                tiers = ", ".join(f"{k}:{v}" for k, v in price_map.items())
                lines.append(f"    - {opt_name}: {tiers}")
    return "\n".join(lines)

def print_selection(label: str, sel: Selection, catalog: Dict[str, ProductRecord]):
    print(f"\n[{label}] Subtotal: ${sel.subtotal:,.0f}")
    for prod, (opt_name, tier, base_price) in sel.picks.items():
        tier_txt = f" · {tier}" if tier != "base" else ""
        rec = catalog.get(prod)
        line_total = effective_line_price(rec, tier, base_price) if rec else base_price
        print(f" - {prod}: {opt_name}{tier_txt} → ${line_total:,.0f}")
