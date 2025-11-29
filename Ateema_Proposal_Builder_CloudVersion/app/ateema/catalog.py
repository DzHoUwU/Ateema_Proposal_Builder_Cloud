
from __future__ import annotations
from typing import Dict, Tuple, Any
from .models import ProductRecord

def normalize_category(raw: str | None) -> str | None:
    if not raw:
        return None
    r = raw.strip().lower()
    if "tourist" in r:
        return "tourist"
    if "industry" in r:
        return "industry"
    return r

def partition_by_category(catalog: Dict[str, ProductRecord], meta: Dict[str, dict]) -> tuple[dict[str, ProductRecord], dict[str, ProductRecord]]:
    tourist, industry = {}, {}
    for name, rec in catalog.items():
        cat = normalize_category((meta.get(name) or {}).get("category"))
        if cat == "tourist":
            tourist[name] = rec
        elif cat == "industry":
            industry[name] = rec
    return tourist, industry
