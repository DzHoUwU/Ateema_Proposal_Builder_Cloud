
# -*- coding: utf-8 -*-
# Note: requires faiss (CPU or GPU), sentence-transformers, pandas, numpy, pyarrow
import json, re
from pathlib import Path
import numpy as np
import pandas as pd
import faiss
from sentence_transformers import SentenceTransformer
from typing import Any, Dict, List, Tuple

# ==== paths (edit for your machine) ====
BASE_DIR =  Path(__file__).resolve()
project_root = BASE_DIR.parent.parent

DATA_DIR = BASE_DIR / "Data" / "ClientToProductData"
INDEX_PATH  = DATA_DIR / "customers_faiss.index"
MAP_PARQUET = DATA_DIR / "customers_mapping_deduped.parquet"
META_JSON   = DATA_DIR / "index_meta.json"

if not INDEX_PATH.exists():
    print(f"Warning: Index file not found at {INDEX_PATH}, trying alternative...")
# -------- Load model & data --------
meta = json.loads(META_JSON.read_text(encoding="utf-8"))
model_name = meta.get("model_name", "BAAI/bge-small-en-v1.5")
dim = int(meta.get("dim", 384))

model = SentenceTransformer(model_name)
assert model.get_sentence_embedding_dimension() == dim, \
    f"Dim mismatch: index expects {dim}, model gives {model.get_sentence_embedding_dimension()}"

index = faiss.read_index(str(INDEX_PATH))
df_map = pd.read_parquet(MAP_PARQUET)

# ========== Helpers ==========
def l2_normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=1, keepdims=True) + 1e-12
    return v / n

def to_dict(x: Any) -> Dict[str, Any]:
    if isinstance(x, dict):
        return x
    if isinstance(x, str) and x.strip():
        try:
            return json.loads(x)
        except Exception:
            return {}
    return {}

def normalize_products(val: Any) -> List[str]:
    if val is None:
        return []
    if isinstance(val, (list, tuple, set)):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, dict):
        return [str(k).strip() for k in val.keys() if str(k).strip()]
    if isinstance(val, str) and val.strip():
        return [p.strip() for p in re.split(r"[,\|;/]+", val) if p.strip()]
    return [str(val).strip()]

ID_CANDS   = ["id","customer_id","client_id","ID","__row_id__"]
TEXT_CANDS = ["text","profile_text","doc_text","content","desc","description","__auto_text__"]
id_col     = next(c for c in ID_CANDS if c in df_map.columns)
text_col   = next((c for c in TEXT_CANDS if c in df_map.columns), None)

def _normalize_name(name: str) -> str:
    if not name:
        return ""
    s = re.sub(r"[^\w]+", " ", str(name))
    s = re.sub(r"\s+", " ", s)
    return s.strip().casefold()

_SUMMIT_ALLOWED = {"Booth Non Advertiser", "Booth Advertiser", "Sponsorship"}

def build_purchased_tokens(meta: Dict[str, Any]) -> List[str]:
    root   = (meta.get("product_root") or "").strip()
    detail = meta.get("product_detail")
    lvl2   = meta.get("product_level_detail2")

    if root.lower() == "the summit":
        details = [d for d in normalize_products(detail) if d in _SUMMIT_ALLOWED]
        if details:
            level2_list = normalize_products(lvl2)
            tokens: List[str] = []
            if level2_list:
                for d in details:
                    for l2 in level2_list:
                        tokens.append(f"The Summit - {d} - {l2}")
            else:
                for d in details:
                    tokens.append(f"The Summit - {d}")
            if tokens:
                return tokens

    if root:
        return normalize_products(root)
    return normalize_products(detail)

def profile_to_query(p: Dict[str, Any]) -> str:
    def _fmt(label: str, val: Any) -> str:
        if val is None or (isinstance(val, str) and not val.strip()):
            return ""
        if isinstance(val, (list, tuple, set)):
            val = ", ".join(map(str, val))
        return f"{label}: {val}"
    parts = [
        _fmt("Business Name", p.get("Business Name")),
        _fmt("Type", p.get("Type")),
        _fmt("Focus", p.get("Focus")),
        _fmt("Market Target", p.get("Market Target")),
        _fmt("Business Description", p.get("Business Description")),
    ]
    parts = [x for x in parts if x]
    return "query: " + " | ".join(parts) if parts else "query: "

def _topk_buckets_from_query_text(qtext: str, k: int = 5, oversample: int = 20):
    qvec  = model.encode([qtext], convert_to_numpy=True, normalize_embeddings=False).astype("float32")
    qvec  = l2_normalize(qvec)

    total = int(index.ntotal)
    k = max(1, min(int(k), total))
    nprobe = min(max(k * oversample, k), total)
    D, I = index.search(qvec, nprobe)

    buckets: Dict[str, Dict[str, Any]] = {}

    for idx, score in zip(I[0], D[0]):
        if int(idx) < 0:
            continue
        rec  = df_map.iloc[int(idx)]
        meta = to_dict(rec.get("metadata"))

        customer_name = (
            meta.get("customer")
            or meta.get("client")
            or meta.get("name")
            or (rec.get("customer_name") if "customer_name" in df_map.columns else None)
        )
        key = _normalize_name(customer_name) or f"id::{str(rec[id_col])}"

        prods = build_purchased_tokens(meta)

        score_f = float(score)
        if key not in buckets:
            buckets[key] = {
                "name": customer_name or str(rec[id_col]),
                "purchased_set": set(prods),
                "best_score": score_f,
                "text_snippet": (str(rec[text_col])[:200] + "…") if text_col else "",
            }
        else:
            buckets[key]["purchased_set"].update(prods)
            if score_f > buckets[key]["best_score"]:
                buckets[key]["best_score"] = score_f
                buckets[key]["text_snippet"] = (str(rec[text_col])[:200] + "…") if text_col else ""

    return buckets

def similar_clients_json(new_client: Dict[str, Any], k: int = 5, oversample: int = 20) -> Dict[str, Any]:
    qtext = profile_to_query(new_client)
    buckets = _topk_buckets_from_query_text(qtext, k=k, oversample=oversample)
    rows = sorted(buckets.values(), key=lambda x: x["best_score"], reverse=True)[:k]
    return {
        "similar_clients": [
            {
                "name": r["name"],
                "purchased": sorted(r["purchased_set"]) if r["purchased_set"] else [],
                "notes": ""
            }
            for r in rows
        ]
    }

def search_topk_customers_name_unique_with_products(
    new_client: Dict[str, Any],
    k: int = 5,
    oversample: int = 20
) -> Tuple[List[Dict[str, Any]], pd.DataFrame]:
    qtext = profile_to_query(new_client)
    buckets = _topk_buckets_from_query_text(qtext, k=k, oversample=oversample)
    vals = sorted(buckets.values(), key=lambda x: x["best_score"], reverse=True)[:k]

    rows: List[Dict[str, Any]] = []
    for i, b in enumerate(vals, start=1):
        rows.append({
            "rank": i,
            "customer_name": b["name"],
            "relevance": round(b["best_score"], 4),
            "product_detail": sorted(b["purchased_set"]) if b["purchased_set"] else None,
            "text_snippet": b["text_snippet"],
        })

    df_table = pd.DataFrame([{
        "Rank": r["rank"],
        "Customer": r["customer_name"],
        "Relevance": r["relevance"],
        "Product Detail": ", ".join(r["product_detail"]) if r["product_detail"] else "N/A",
        "Snippet": r["text_snippet"]
    } for r in rows])

    return rows, df_table

def format_client_profile(nc: Dict[str, Any]) -> str:
    parts = []
    for k in ["Business Name", "Type", "Focus", "Market Target", "Business Description"]:
        v = nc.get(k, "")
        if v is None:
            v = ""
        parts.append(f"{k}: {v}".strip())
    return "\n".join(parts)

def build_client_summary_json(new_client: Dict[str, Any], k: int = 5, budget: int = 50000) -> Dict[str, Any]:
    sc = similar_clients_json(new_client, k=k)
    all_products: List[str] = []
    for item in sc["similar_clients"]:
        prods = item.get("purchased", []) or []
        all_products.extend([str(p).strip() for p in prods if str(p).strip()])
    candidate_products = sorted(set(all_products))
    output = {
        "client_profile": format_client_profile(new_client),
        "budget": budget,
        "similar_clients": sc["similar_clients"],
        "candidate_products": candidate_products,
    }
    return output

def save_and_download_json(payload: Dict[str, Any], business_name: str = "client", fname_suffix: str = "summary"):
    def slugify(s: str) -> str:
        s = str(s) if s is not None else "client"
        s = re.sub(r"[^\w\-]+", "_", s).strip("_")
        return s or "client"
    fname = f"{slugify(business_name)}_{fname_suffix}.json"
    out_path = f"./{fname}"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"Saved to: {out_path}")

if __name__ == "__main__":
    demo_client = {
        "Business Name": "newclient",
        "Type": "Local Business",
        "Focus": "Immediate Impact",
        "Market Target": "Food",
        "Business Description": "Demo only."
    }
    summary = build_client_summary_json(demo_client, k=5, budget=50000)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
