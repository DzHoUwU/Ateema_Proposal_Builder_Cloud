
from __future__ import annotations
from .models import PoolInfo, ProductRecord
from typing import Dict

def compute_pools(total_budget: float, tourist_pct: float, industry_pct: float) -> tuple[PoolInfo, PoolInfo]:
    t_budget = round(total_budget * (tourist_pct / 100.0), 2)
    i_budget = round(total_budget * (industry_pct / 100.0), 2)
    return PoolInfo("tourist", t_budget), PoolInfo("industry", i_budget)

def print_pool_audit(total_budget: float, tourist_pct: float, industry_pct: float,
                     t_set: Dict[str, ProductRecord], i_set: Dict[str, ProductRecord]) -> None:
    t_pool, i_pool = compute_pools(total_budget, tourist_pct, industry_pct)
    print("\n=== Focus Split & Pools ===")
    print(f"Total budget: ${total_budget:,.0f} | split: {tourist_pct:.1f}% tourist / {industry_pct:.1f}% industry")
    print(f"Tourist pool:  ${t_pool.budget:,.0f}  | {len(t_set)} products: {', '.join(t_set.keys()) or '—'}")
    print(f"Industry pool: ${i_pool.budget:,.0f}  | {len(i_set)} products: {', '.join(i_set.keys()) or '—'}")
    if total_budget == 45000 and abs(tourist_pct - 60) < 1e-6:
        print("Acceptance guidance: spend ≥ $27k tourist and ≥ $18k industry unless pool is capped by available SKUs.")
