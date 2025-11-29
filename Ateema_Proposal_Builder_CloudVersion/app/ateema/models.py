
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class PriceOption:
    name: str
    raw: Dict[str, Any]

@dataclass
class ProductRecord:
    name: str
    price_options: List[Dict[str, Any]]  # keep dicts for schema flexibility
    category: Optional[str] = None
    duration_quarter_map: Optional[Dict[str, Any]] = field(default_factory=dict)

@dataclass
class PoolInfo:
    label: str
    budget: float
    subtotal: float = 0.0
    items: List[str] = field(default_factory=list)

@dataclass
class Selection:
    picks: Dict[str, tuple[str, str, float]]  # product -> (option_name, tier_label, price)
    subtotal: float

@dataclass
class ProductRecord:
    name: str
    price_options: List[Dict[str, Any]]
    category: Optional[str] = None
    duration_quarter_map: Optional[Dict[str, Any]] = field(default_factory=dict)
    # NEW:
    product_description: Optional[str] = None
    sales_strategy: Optional[Any] = None         # str or dict
    discount_policy: Optional[Any] = None        # str or dict