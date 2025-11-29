
from __future__ import annotations
from copy import deepcopy
from typing import Dict
from .models import ProductRecord

def _contains_all(text: str, *keywords: str) -> bool:
    t = (text or "").lower()
    return all((k or "").lower() in t for k in keywords if k)

def _contains_any(text: str, *keywords: str) -> bool:
    t = (text or "").lower()
    return any((k or "").lower() in t for k in keywords if k)

def filter_summit_booth(rec: ProductRecord, profile_text: str, is_advertiser: bool) -> ProductRecord:
    out = deepcopy(rec)
    name_l = (rec.name or '').lower()
    if "summit" not in name_l or "booth" not in name_l:
        return out

    txt = profile_text or ""
    opts = rec.price_options or []

    def is_label(label: str, key: str) -> bool:
        return (key or "").lower() in (label or "").lower()

    if _contains_all(txt, "cvb", "illinois"):
        out.price_options = [o for o in opts if is_label(o.get("name",""), "Illinois CVB")]
        return out

    if _contains_any(txt, "dmo", "out-of-state", "out of state", "outside illinois"):
        out.price_options = [o for o in opts if is_label(o.get("name",""), "DMO (out of Illinois)")]
        return out

    # small business default
    if is_advertiser is True:
        key = "Basic Booth — advertiser rate"
    elif is_advertiser is False:
        key = "Basic Booth — non-advertiser rate"
    else:
        key = "Basic Booth — advertiser rate"
    out.price_options = [o for o in opts if o.get("name") == key]
    if not out.price_options:
        out.price_options = [o for o in opts if "basic booth" in (o.get("name","").lower())]
    return out

def apply_summit_rules(catalog: Dict[str, ProductRecord], profile_text: str, is_advertiser: bool) -> Dict[str, ProductRecord]:
    new = {}
    for name, rec in catalog.items():
        if "summit" in name.lower() and "booth" in name.lower():
            new[name] = filter_summit_booth(rec, profile_text, is_advertiser)
        else:
            new[name] = rec
    return new