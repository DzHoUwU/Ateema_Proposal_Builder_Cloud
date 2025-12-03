from __future__ import annotations
from typing import Dict, Tuple, Optional
from datetime import date

from ateema.pricing import (
    price_points,
    effective_line_price,
    get_effective_unit_price,
)
from ateema.models import Selection, ProductRecord

import re


# ------------------------------------------------------------
# Utility: restricted tiers (unchanged)
# ------------------------------------------------------------
def _is_restricted_tier(label: str, is_advertiser: bool) -> bool:
    if is_advertiser:
        return False

    lbl = label.lower()

    restricted_keywords = [
        "with any campaign",
        "Contract with multiple products",
        "with campaign",
        "existing advertiser",
        "advertiser rate",
        "bundle",
        "add-on",
        "package price",
    ]

    if ("advertiser" in lbl
        and "non-advertiser" not in lbl
        and "non advertiser" not in lbl):
        return True

    for kw in restricted_keywords:
        if kw in lbl:
            return True

    return False


# ------------------------------------------------------------
# Chicago Map — NEW pricing model
# ------------------------------------------------------------
def map_line_price(
    pname: str,
    tier_label: str,
    eff_base: float,
    interactive_map_quarters: int,
) -> float:
    """
    New pricing model for Chicago Does Interactive Map:
      cost = per-quarter price (tier-specific) × number_of_quarters
    """
    if pname != "Chicago Does Interactive Map":
        return None  # signals "not handled here"

    if not interactive_map_quarters:
        interactive_map_quarters = 1

    # eff_base is ALREADY the tier-specific per-quarter price (this is correct)
    return eff_base * interactive_map_quarters


# ------------------------------------------------------------
# Core allocator — new Map handling added
# ------------------------------------------------------------
def greedy_fill_to_cap(
    budget: float,
    products: Dict[str, ProductRecord],
    meta: Dict[str, dict],
    chosen_date: Optional[date],
    is_advertiser: bool = False,
    interactive_map_quarters: Optional[int] = None,
) -> Tuple[Selection, bool]:

    picks: Dict[str, Tuple[str, str, float]] = {}
    subtotal = 0.0

    # --------------------------------------------------------
    # Baseline picks (select initial tier for each product)
    # --------------------------------------------------------
    for pname, product in products.items():

        best = None
        best_line = None

        for opt in product.price_options:
            opt_name = opt.get("name", pname)

            # Summit Booth advertiser filter
            if "summit" in pname.lower() and "booth" in pname.lower():
                low = opt_name.lower()
                has_non = ("non-advertiser" in low) or ("non advertiser" in low)
                has_adv = ("advertiser" in low) and (not has_non)

                if is_advertiser and has_non:
                    continue
                if (not is_advertiser) and has_adv:
                    continue

            for lbl, base_price in price_points(opt):
                if _is_restricted_tier(lbl, is_advertiser):
                    continue

                eff_base = get_effective_unit_price(
                    pname,
                    opt_name,
                    base_price,
                    meta.get(pname, {}),
                    chosen_date,
                    is_advertiser=is_advertiser,
                )

                # --------------------------------------------------------
                # NEW — Chicago Map uses custom total price formula
                # --------------------------------------------------------
                map_price = map_line_price(
                    pname, lbl, eff_base, interactive_map_quarters
                )
                if map_price is not None:
                    line = map_price
                else:
                    line = effective_line_price(product, lbl, eff_base)

                # --------------------------------------------------------
                # NEW — Chicago Map wants **highest tier under budget**
                # --------------------------------------------------------
                if pname == "Chicago Does Interactive Map":
                    # pick highest valid (<=budget)
                    if line <= budget and (best is None or line > best_line):
                        best = (opt_name, lbl, eff_base)
                        best_line = line
                else:
                    # normal products: cheapest-first baseline (unchanged)
                    if best is None or line < best_line:
                        best = (opt_name, lbl, eff_base)
                        best_line = line

        if best:
            opt_name, lbl, eff_base = best

            # compute cost again for subtotal
            map_price = map_line_price(
                pname, lbl, eff_base, interactive_map_quarters
            )
            if map_price is not None:
                subtotal += map_price
            else:
                subtotal += effective_line_price(product, lbl, eff_base)

            picks[pname] = (opt_name, lbl, eff_base)


    # --------------------------------------------------------
    # Upgrade loop — now Map upgrades use new pricing model
    # --------------------------------------------------------
    forced_overage = subtotal > budget

    if not forced_overage:
        improved = True

        while improved:
            improved = False

            for pname, (cur_opt, cur_lbl, cur_base) in list(picks.items()):
                product = products[pname]

                # current cost
                map_price = map_line_price(
                    pname, cur_lbl, cur_base, interactive_map_quarters
                )
                if map_price is not None:
                    cur_line = map_price
                else:
                    cur_line = effective_line_price(product, cur_lbl, cur_base)

                upgrades = []

                # explore all other tiers
                for opt in product.price_options:
                    opt_name = opt.get("name", pname)

                    # repeat booth advertiser logic
                    if "summit" in pname.lower() and "booth" in pname.lower():
                        low = opt_name.lower()
                        has_non = ("non-advertiser" in low) or ("non advertiser" in low)
                        has_adv = ("advertiser" in low) and (not has_non)

                        if is_advertiser and has_non:
                            continue
                        if (not is_advertiser) and has_adv:
                            continue

                    for lbl, base_price in price_points(opt):
                        if _is_restricted_tier(lbl, is_advertiser):
                            continue

                        eff_base = get_effective_unit_price(
                            pname,
                            opt_name,
                            base_price,
                            meta.get(pname, {}),
                            chosen_date,
                            is_advertiser=is_advertiser,
                        )

                        map_price = map_line_price(
                            pname, lbl, eff_base, interactive_map_quarters
                        )
                        if map_price is not None:
                            new_line = map_price
                        else:
                            new_line = effective_line_price(product, lbl, eff_base)

                        # upgrades must be more expensive than current tier
                        if new_line > cur_line:
                            upgrades.append((opt_name, lbl, eff_base, new_line))

                upgrades.sort(key=lambda x: x[3])  # ascending by cost

                for opt_name, lbl, eff_base, new_line in upgrades:

                    if subtotal - cur_line + new_line <= budget:
                        subtotal = subtotal - cur_line + new_line
                        picks[pname] = (opt_name, lbl, eff_base)
                        improved = True
                        break

    return Selection(picks=picks, subtotal=round(subtotal, 2)), forced_overage


# ------------------------------------------------------------
# Run for both pools
# ------------------------------------------------------------
def run_fill_to_cap(
    total_budget: float,
    tourist_pct: float,
    industry_pct: float,
    t_set: Dict[str, ProductRecord],
    i_set: Dict[str, ProductRecord],
    meta: Dict[str, dict],
    chosen_date: Optional[date] = None,
    is_advertiser: bool = False,
    interactive_map_quarters: Optional[int] = None,
):

    t_budget = total_budget * (tourist_pct / 100.0)
    i_budget = total_budget * (industry_pct / 100.0)

    t_sel, t_warn = greedy_fill_to_cap(
        t_budget, t_set, meta, chosen_date,
        is_advertiser=is_advertiser,
        interactive_map_quarters=interactive_map_quarters,
    )

    i_sel, i_warn = greedy_fill_to_cap(
        i_budget, i_set, meta, chosen_date,
        is_advertiser=is_advertiser,
        interactive_map_quarters=interactive_map_quarters,
    )

    grand_total = round(t_sel.subtotal + i_sel.subtotal, 2)

    warning_msg = None
    if t_warn or i_warn:
        warning_msg = (
            "Heads up: Even with the most cost-effective options selected, "
            "the current budget distribution cannot support this product mix."
        )

    return t_sel, i_sel, grand_total, warning_msg
