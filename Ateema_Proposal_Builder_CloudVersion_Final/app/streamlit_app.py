from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Dict, List
import streamlit as st

from ateema.io_loader import load_products
from ateema.summit_rules import apply_summit_rules
from ateema.catalog import partition_by_category
from ateema.upgrader import run_fill_to_cap
from ateema.formatting import format_product_block
from ateema.pricing import apply_discounts, get_effective_unit_price, first_known_price

BASE_DIR = Path(__file__).resolve()
project_root = BASE_DIR.parent.parent

# --- Ateema Logo Building ---
logo_wide_path = project_root / "Logo" / "logo_wide.jpg"     

st.set_page_config(
    page_title="Ateema – Proposal Builder", 
    page_icon="🧭", 
    layout="wide"
)
st.markdown("""
    <style>
        .block-container {
            padding-top: 1rem;
            padding-bottom: 0rem;
        }
    </style>
""", unsafe_allow_html=True)

if logo_wide_path.exists():
    # [1, 3, 1]  -> Logo will occupy 60% of the first row
    # [1, 2, 1]  -> Logo will occupy 50%...
    left_co, cent_co, last_co = st.columns([1, 2, 1])

    with cent_co:
        st.image(str(logo_wide_path), use_container_width=True)
else:
    st.title("Ateema – Proposal Builder")

# ---------- Page ----------
st.set_page_config(page_title="Ateema – Proposal Builder", page_icon="🧭", layout="wide")
st.title("Ateema – Proposal Builder")

# ---------- Sidebar (old layout restored) ----------

DEFAULT_PRODUCTS = str(project_root / "Data" / "PriceStrategy")

#with st.sidebar:
    #st.header("Settings")
    #products_path = st.text_input("Products folder", value=DEFAULT_PRODUCTS)
    #input_mode = st.radio("Input mode", ["Load JSON", "Survey-style"], horizontal=False)
    # parity settings (not used by deterministic allocator)
    #ollama_model = st.text_input("Ollama model", value="gemma3:4b")
    #temperature = st.slider("Temperature", 0.0, 1.0, 0.10, 0.05)
    #soft_cap_pct = st.slider("Soft cap (+%)", 0, 50, 10, 1)

products_path = DEFAULT_PRODUCTS
input_mode = "Survey-style"
ollama_model = "gemma3:4b"
temperature = 0.10
soft_cap_pct = 10

# ---------- Awards config (Gabby) ----------

AWARDS_PATH = project_root / "Data" / "PriceStrategy" / "Summit Awards.json"
try:
    with AWARDS_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # 如果是我们刚才包过壳的形式：{ ..., "awards": [ ... ] }
    if isinstance(data, dict) and "awards" in data:
        AWARDS_CONFIG = data["awards"]
    # 如果还是原来的 list（以防以后同学忘了加壳）
    elif isinstance(data, list):
        AWARDS_CONFIG = data
    else:
        AWARDS_CONFIG = []
        st.warning("Summit awards config has unexpected format.")

    # 所有 general_category，用于 Business Type 下拉框
    GENERAL_AWARD_CATEGORIES = sorted(
        {a.get("general_category") for a in AWARDS_CONFIG if a.get("general_category")}
    )

    # Business type / general_category → Award 名
    BUSINESS_TYPE_TO_AWARD: dict[str, str] = {}

    # Award 名 → 价格 / 描述
    AWARD_NAME_TO_PRICE: dict[str, float] = {}
    AWARD_NAME_TO_DESCRIPTION: dict[str, str] = {}

    for a in AWARDS_CONFIG:
        name = a.get("name")
        gen_cat = a.get("general_category")
        price = a.get("price")
        desc = a.get("description")
        eligible_types = a.get("eligible_business_types") or []

        if gen_cat and name and gen_cat not in BUSINESS_TYPE_TO_AWARD:
            BUSINESS_TYPE_TO_AWARD[gen_cat] = name

        for bt in eligible_types:
            if bt and name and bt not in BUSINESS_TYPE_TO_AWARD:
                BUSINESS_TYPE_TO_AWARD[bt] = name

        if name is not None and price is not None:
            AWARD_NAME_TO_PRICE[name] = float(price)
        if name is not None and desc:
            AWARD_NAME_TO_DESCRIPTION[name] = desc

except Exception as e:
    AWARDS_CONFIG = []
    GENERAL_AWARD_CATEGORIES = []
    BUSINESS_TYPE_TO_AWARD = {}
    AWARD_NAME_TO_PRICE = {}
    AWARD_NAME_TO_DESCRIPTION = {}
    st.warning(f"Summit awards config unavailable: {e}")
# ---------- Price override (session state) ---------- Yuchen 11.30
if "proposal_state" not in st.session_state:
    st.session_state["proposal_state"] = None  # store last generated selections

if "price_overrides" not in st.session_state:
    # key -> new_total_price
    st.session_state["price_overrides"] = {}

if "override_open" not in st.session_state:
    st.session_state["override_open"] = False


def _row_key(pool_label: str, row: dict) -> str:
    # pool_label: "tourist" / "industry"
    return f"{pool_label}|{row.get('product', '')}|{row.get('option', '')}"


def _apply_price_overrides(pool_label: str, rows: list[dict]) -> list[dict]:
    """Mutate a copy of rows by applying user overrides to total_price (and unit_price)."""
    overrides: dict = st.session_state.get("price_overrides", {}) or {}
    out = []
    for r in rows:
        rr = dict(r)
        key = _row_key(pool_label, rr)
        if key in overrides:
            new_total = float(overrides[key])
            qty = float(rr.get("qty") or 1) or 1.0
            rr["total_price"] = new_total
            rr["unit_price"] = new_total / qty
            # mark in notes
            note = rr.get("discount") or ""
            tag = "Price override"
            rr["discount"] = (note + (" | " if note else "") + tag)
        out.append(rr)
    return out


# ---------- Utils ----------
def list_jsons(folder: Path) -> List[str]:
    try:
        return sorted([str(p) for p in folder.glob("*.json")])
    except Exception:
        return []


def qty_from_tier(tier: str) -> int:
    if not tier:
        return 1
    m = re.fullmatch(r"(\d+)\s*[xX]", tier.strip())
    return int(m.group(1)) if m else 1


def make_reasoning(
        product: str,
        option: str,
        tier: str,
        label: str,
        focus: str,
        market_target: str,
) -> str:
    """
    Produce 2–4 short bullet-point style phrases.
    Clean, scannable, cue-card style.
    """

    info = meta.get(product, {})
    desc = info.get("product_description", "")
    strategy = info.get("sales_strategy", "")
    notes = info.get("option_notes", {}).get(option, "")

    bullets = []

    # 1) Focus + Target — compressed to short phrase
    bullets.append(
        f"Supports {focus.lower()}"
    )
    bullets.append(
        f"Reaches {market_target.lower()}"
    )

    # 2) Option-level advantage — shorten aggressively
    if notes:
        short_notes = notes.strip().split(".")[0]
        bullets.append(short_notes)

    # 3) Product description — only most essential clause
    if desc:
        first_clause = desc.strip().split(".")[0]
        bullets.append(first_clause)

    # 4) Strategy — also shortened
    if strategy:
        short_strategy = strategy.strip().split(".")[0]
        bullets.append(short_strategy)

    # Keep only first 3–4 bullets
    bullets = bullets[:4]

    # Format as bullet-point phrases separated by semicolons
    reasoning = "; ".join(bullets)

    return reasoning


def rows_from_selection(
        label: str,
        sel,
        focus: str,
        market_target: str,
        all_products: set[str],
        prepay_full_year: bool,
        is_advertiser: bool,  # Dazhou 11/17 Advertiser
        proposal_date=None,  # Yuchen 11/19 Early Bird
        grand_total: float | None = None,  # Yuchen 11/20 Networking Event
) -> List[Dict]:
    """
    Build table rows for a pool, applying product-specific discounts.

    - unit_price_original: pre-discount unit price from allocator
    - unit_price: discounted unit price (may be same as original)
    """
    out: List[Dict] = []

    for prod, (opt_name, tier, unit_price_original) in sel.picks.items():

        # Base qty from tier
        qty = qty_from_tier(tier)

        # Override ONLY for Interactive Map
        if prod == "Chicago Does Interactive Map":
            qty = st.session_state.get("interactive_map_quarters", qty)

        has_other_products = len(all_products - {prod}) > 0

        # Dazhou 11/24 grand total
        # Force is_advertiser=False, then try to find the original price from META
        product_info = meta.get(prod, {})
        raw_base = unit_price_original  # 默认回退值
        # Try to find original product price from META
        if "price_options" in product_info:
            for opt in product_info["price_options"]:
                if opt.get("name", prod) == opt_name:
                    found_base = first_known_price(opt)
                    if found_base is not None:
                        raw_base = found_base
                    break

        # Calculate real original price based on the seasonal price, forcing is_advertiser = false
        real_list_price = get_effective_unit_price(
            product_name=prod,
            option_name=opt_name,
            base_price=raw_base,
            meta=product_info,
            chosen_date=proposal_date,
            is_advertiser=False  # forcing is_advertiser = false
        )

        # Phase 1 discount engine
        # Caluate the final price with all the discount
        unit_price_discounted, discount_label = apply_discounts(
            product_name=prod,
            option_name=opt_name,
            base_price=real_list_price,  # Dazhou 11/24 grand total
            tier=tier,
            has_other_products=has_other_products,
            prepay_full_year=prepay_full_year,
            is_advertiser=is_advertiser,  # Dazhou 11/17 Advertiser
        )

        line_total_original = unit_price_original * qty
        line_total = unit_price_discounted * qty

        reasoning = make_reasoning(prod, opt_name, tier, label, focus, market_target)

        out.append(
            {
                "product": prod,
                "option": opt_name,
                "qty": qty,
                "unit_price_original": real_list_price,  # Dazhou 11/24 grand total
                "unit_price": unit_price_discounted,  # Dazhou 11/24 grand total
                "discount": discount_label or "",
                "total_price_original": line_total_original,
                "total_price": line_total,
                "reasoning": reasoning,
            }
        )

    # --- Networking Event host/attend (Yuchen 11/20) ---

    if label != "industry":
        return out

    THRESHOLD = 12500.0

    # grand_total 没传，或者没到阈值：不做额外处理
    if grand_total is None:
        return out

    # 只找 Network + HOST（ATTEND 不算）
    host_indices: list[int] = []
    host_total = 0.0

    for idx, row in enumerate(out):
        pname = (row.get("product") or "").lower()
        opt = (row.get("option") or "").lower()

        is_network = ("network" in pname and "event" in pname)

        # HOST 的判定：优先看 option/name 里包含 host；兜底用 3500 原价识别（兼容旧输出）
        unit_orig = float(row.get("unit_price_original") or 0.0)
        is_host = ("host" in opt) or abs(unit_orig - 3500.0) < 1e-6

        if is_network and is_host:
            host_indices.append(idx)
            host_total += float(row.get("total_price") or 0.0)

    # 没选 HOST：不送、不改价（同时也不会自动 append）
    if not host_indices:
        return out

    # 防止“靠 HOST 自己凑到 12,500 又变免费导致阈值不成立”的循环：
    # 用“除去 HOST 的其他消费”判断阈值
    if (grand_total - host_total) < THRESHOLD:
        return out

    benefit_label = "Included at no cost with $12,500+ investment"

    for idx in host_indices:
        row = out[idx]
        row["unit_price"] = 0.0
        row["total_price"] = 0.0
        row["discount"] = benefit_label

    return out


# ---------- Load products ----------
folder = Path(products_path)
if not folder.exists():
    st.error(f"Folder not found: {products_path}")
    st.stop()

found = list_jsons(folder)
if not found:
    st.warning("No *.json files found in the selected folder.")
else:
    with st.expander("Found product files (debug)"):
        for f in found:
            st.write(f)

catalog = {}
meta = {}
try:
    catalog, meta = load_products(folder)
except Exception as e:
    bad = None
    for p in folder.glob("*.json"):
        try:
            _ = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception as sub_e:
            bad = (str(p), str(sub_e))
            break
    if bad:
        st.error(f"Failed to load. Problem file: {bad[0]} — {bad[1]}")
    else:
        st.error(f"Failed to load products: {e}")
    st.stop()

# Gabri Award
for internal_name in ["Summit Awards Config", "Summit Awards"]:
    catalog.pop(internal_name, None)
    meta.pop(internal_name, None)

raw_names = sorted(catalog.keys())

# Gabri Award
all_names = raw_names

# ---------- Input areas ----------
profile_text = ""
focus_text = ""
market_text = ""
audience_type_text = ""
is_advertiser = True
total_budget = 45000.0
tourist_pct = 60
industry_pct = 40
prepay_full_year = False  # ← ADD THIS
proposal_date = None
chosen: List[str] = []

if input_mode == "Load JSON":
    st.subheader("Load JSON")
    json_path = st.text_input("Input JSON path", value=str(Path(folder.parent, "Inputs", "input.json")))
    if st.button("Generate Proposal", type="primary", key="gen_from_json"):
        raw = json.loads(Path(json_path).read_text(encoding="utf-8"))
        profile = raw.get("client_profile", "")
        total_budget = float(raw.get("budget", 0))
        similar_clients = raw.get("similar_clients", [])
        chosen = list(raw.get("candidate_products", []))

        # Extract focus/target/audience from profile for reasoning
        focus_match = re.search(r"Focus:\s*(.*)", profile, re.IGNORECASE)
        target_match = re.search(r"Market Target:\s*(.*)", profile, re.IGNORECASE)
        audience_match = re.search(r"Audience Type:\s*(.*)", profile, re.IGNORECASE)

        focus_text = focus_match.group(1).strip() if focus_match else ""
        market_text = target_match.group(1).strip() if target_match else ""
        audience_type_text = audience_match.group(1).strip() if audience_match else ""

        profile_text = profile

        # Yuchen 11.30
        st.session_state["price_overrides"] = {}
        st.session_state["proposal_state"] = None
        st.session_state["override_open"] = False

        st.session_state["_trigger_generate"] = True
        st.session_state["_payload_similar"] = similar_clients

else:
    # Survey-style (vertical order)
    st.subheader("Client Survey")
    proposal_date = st.date_input(
        "Proposal Date",
        help="This determines seasonal booth pricing such as 4/15–6/14 or 6/15–8/31."
    )
    client_name = st.text_input("Business Name", value="River North Seasonal Kitchen")

    # Gabri Award
    business_type = st.selectbox(
        "Business Type (Summit category)",
        GENERAL_AWARD_CATEGORIES,
        index=0,
        help="Choose the Summit category that best matches this client's business."
    )

    matched_award = BUSINESS_TYPE_TO_AWARD.get(business_type)

    if matched_award:
        st.success(f"Matched Summit Award: **{matched_award}**")
    else:
        st.info("No Summit Award automatically matched for this business type.")

    # 支持多选的 Audience Type
    AUDIENCE_OPTIONS = ["Tourist", "Local", "Meeting and Event Planner"]

    audience_types = st.multiselect(
        "Audience Type (you can choose one or more)",
        options=AUDIENCE_OPTIONS,
        default=[AUDIENCE_OPTIONS[0]],
        help="Primary audiences this proposal is meant to reach."
    )

    # 保留一个字符串，兼容后面所有用 audience_type_text 的地方
    if audience_types:
        audience_type_text = ", ".join(audience_types)
    else:
        audience_type_text = ""

    focus_text = st.text_area(
        "Focus (what outcome?)",
        value="Launch seasonal tasting menu; boost lunch & pre-theatre reservations",
        height=80,
    )
    market_text = st.text_area(
        "Market Target (who to reach?)",
        value="Downtown professionals; tourists near River North theatres",
        height=80,
    )
    # === Simple Interactive Map Quarter Selector (always visible) ===
    interactive_map_quarters = st.selectbox(
        "Interactive Map Quarters",
        [1, 2, 3, 4],
        index=0,
        help="How many quarters the Chicago Does Interactive Map should run."
    )
    st.session_state["interactive_map_quarters"] = interactive_map_quarters

    is_advertiser = st.checkbox("Existing Advertiser?", value=True)

    st.subheader("Budget & Split")
    total_budget = st.number_input("Total Budget (USD)", min_value=0.0, value=45000.0, step=500.0)
    tourist_pct = st.slider("Budget Distribution %", min_value=0, max_value=100, value=60, step=1)
    st.markdown(
        f"<div style='margin-top:-8px;margin-bottom:6px;font-size:18px;font-weight:600;'>Tourist Messaging: {tourist_pct}%  •  Industry Relationship: {100 - tourist_pct}%</div>",
        unsafe_allow_html=True
    )
    industry_pct = 100 - tourist_pct

    # Prepay toggle – used for Interactive Map discount
    prepay_full_year = st.checkbox(
        "Prepay eligible annual programs (10% discount on Interactive Map)",
        value=False,
        help="If checked, Interactive Map pricing will reflect a 10% prepay discount where applicable."
    )
    # --- Similar clients (auto-generate) ---
    # Gabri Award
    AWARD_PRODUCT_NAME = "Summit — Awards Sponsorship"
    # All Award Options
    extended_options = all_names + [AWARD_PRODUCT_NAME]

    st.markdown("### Similar Clients")
    k_sim = st.slider("How many similar clients?", 1, 10, 5, 1)

    # Dazhou 12/1 similar clients
    if st.button("Generate similar clients", type="secondary"):
        try:
            with st.spinner("Analyzing similar clients..."):
                from partner.client_to_product_final import similar_clients_json

                new_client_payload = {
                    "Business Name": client_name,
                    "Business Type": business_type,
                    "Focus": focus_text,
                    "Market Target": market_text,
                    "Business Description": "",
                }
                sc = similar_clients_json(new_client_payload, k=k_sim)

                st.session_state["_payload_similar"] = sc["similar_clients"]
                st.rerun()

                # Gabri Award
                # helper:  seed multiselect
                if st.button("Use products from similar clients"):
                    picked = set()
                    for d in sc["similar_clients"]:
                        for p in d.get("purchased", []):
                            # Only real product besides of Awards
                            if p in all_names:
                                picked.add(p)
                    chosen = st.multiselect(
                        "Choose candidate products",
                        options=extended_options,
                        default=sorted(picked),
                    )
        except Exception as e:
            st.error(f"Failed to generate similar clients: {e}")

    if "_payload_similar" in st.session_state:
        similar_data = st.session_state["_payload_similar"]
        st.markdown("---")
        st.markdown("#### Similar Clients Analysis")    
        lines = [
                f"{d['name']} | {', '.join(d.get('purchased', []))} | {d.get('notes', '')}"
                for d in similar_data
            ]
        st.code("\n".join(lines), language="text")
        if st.button("Close Analysis"):
            del st.session_state["_payload_similar"]
            st.rerun()

        st.markdown(
            "<div style='font-size:20px; color:#666; margin-top:15px;'>"
            "Similarity recommendations are powered by AI-based text embeddings and clustering."
            "</div>",
            unsafe_allow_html=True
        )


    # Candidate products – start EMPTY
    chosen = st.multiselect("Choose candidate products", options=extended_options, default=[])

    profile_text = (
        f"Business Name: {client_name}\n"
        f"Business Type: {business_type}\n"
        # Gabri Award
        f"Matched Summit Award: {matched_award if matched_award else 'None'}\n"
        f"Audience Type: {audience_type_text}\n"
        f"Focus: {focus_text}\n"
        f"Market Target: {market_text}"
    )

    if st.button("Generate Proposal", type="primary", key="gen_from_survey"):
        # Yuchen 11.30
        st.session_state["price_overrides"] = {}
        st.session_state["proposal_state"] = None
        st.session_state["override_open"] = False

        st.session_state["_trigger_generate"] = True
        # Gabri Award
        st.session_state["_award_selected"] = (AWARD_PRODUCT_NAME in chosen)

# ---------- Output helpers ----------
# Gabri多选audience recommendation函数
import re  # 顶部如果已经有就不用重复


def make_digital_ads_paragraph(
        audience_type_text: str,
        focus_text: str,
        market_text: str,
        meta: dict,
) -> str:
    """
    Generate the Digital Advertising Recommendation section
    with a sales-friendly tone suitable for Ateema's clients.
    """

    # ---------------------------
    # Load meta data
    # ---------------------------
    digi_meta = meta.get("digital_ads") or {}
    notes_map = digi_meta.get("notes_map") or {}
    product_desc = digi_meta.get("product_description") or ""

    # ---------------------------
    # Normalize audience list
    # ---------------------------
    raw = audience_type_text or ""
    tokens = [t.strip() for t in re.split(r",|/|&|and", raw) if t.strip()]

    audience_types: list[str] = []
    seen = set()
    for t in tokens:
        t_low = t.lower()
        if "meeting" in t_low or "planner" in t_low:
            norm = "Meeting and Event Planner"
        elif "tour" in t_low:
            norm = "Tourist"
        else:
            norm = "Local"

        if norm not in seen:
            seen.add(norm)
            audience_types.append(norm)

    # If nothing selected, skip
    if not audience_types and not (focus_text or market_text):
        return ""

    # ---------------------------
    # SALES-FRIENDLY NOTES (new tone)
    # ---------------------------
    SALES_NOTES = {
        "Tourism": (
            "Tourists rely heavily on digital search, maps, and recommendations "
            "when deciding where to eat, shop, and explore. Digital ads help your "
            "business stay visible during these key decision moments, ensuring "
            "visitors can easily discover you while comparing nearby options."
        ),
        "Local": (
            "Local customers make quick decisions about where to dine, meet friends, "
            "or try something new. Consistent digital visibility keeps your business "
            "top-of-mind, helping you become a go-to choice for people living and "
            "working nearby."
        ),
        "Meeting": (
            "Meeting and Event Planners search for reliable, high-quality partners "
            "when planning group activities or corporate events. Digital ads keep "
            "your business visible early in their planning process, helping you "
            "stand out when they compare venues and build itineraries."
        ),
    }

    # ---------------------------
    # Multi-audience segments (sales tone)
    # ---------------------------
    SEGMENT_TEXT = {
        "tourism": (
            "Visitors rely on search engines, maps, and recommendation platforms "
            "to decide where to go. Keeping your business visible ensures tourists "
            "can easily discover you as they plan their day and compare nearby options."
        ),
        "local": (
            "Locals look for dependable, convenient places to visit regularly. "
            "Digital visibility helps your business stay top-of-mind and become "
            "part of their routine dining, shopping, and entertainment decisions."
        ),
        "meeting": (
            "Planners often make high-value decisions, booking for groups or corporate "
            "activities. Staying visible during their early research phase helps your "
            "business stand out as they evaluate venues and create customized itineraries."
        ),
    }

    # ---------------------------
    # Start composing output
    # ---------------------------
    parts: list[str] = []
    parts.append("**Digital Advertising Recommendation**")

    # ---------------------------
    # SINGLE AUDIENCE → summary + sales notes
    # ---------------------------
    if len(audience_types) == 1:
        at = audience_types[0]
        at_low = at.lower()

        # Sales summary
        parts.append(
            f"Your proposal is focused on reaching **{at}**. "
            "Digital advertising helps your business stay visible at the exact "
            "moments when this audience is searching, comparing choices, and "
            "deciding where to go."
        )

        # Identify note
        if "meeting" in at_low:
            parts.append(SALES_NOTES["Meeting"])
        elif "tour" in at_low:
            parts.append(SALES_NOTES["Tourism"])
        else:
            parts.append(SALES_NOTES["Local"])

    # ---------------------------
    # MULTIPLE AUDIENCES → summary + segments
    # ---------------------------
    else:
        # Make audience string
        if len(audience_types) == 2:
            joined = " and ".join(audience_types)
        else:
            joined = ", ".join(audience_types[:-1]) + f" and {audience_types[-1]}"

        # Sales-style summary
        parts.append(
            f"Your proposal is designed to reach multiple key audience groups — **{joined}**. "
            "Digital advertising ensures your business appears during the exact moments "
            "when each group is searching, comparing options, or making plans."
        )

        # classify helper
        def classify(a: str) -> str:
            a_low = a.lower()
            if "meeting" in a_low or "planner" in a_low:
                return "meeting"
            elif "tour" in a_low:
                return "tourism"
            return "local"

        seen_seg = set()
        for a in audience_types:
            seg = classify(a)
            if seg in seen_seg:
                continue
            seen_seg.add(seg)

            label = (
                "Meeting & Event Planners" if seg == "meeting" else
                "Tourists / Visitors" if seg == "tourism" else
                "Local Customers"
            )

            parts.append(f"**{label}**\n\n{SEGMENT_TEXT[seg]}")

    # ---------------------------
    # Focus + Market (sales tone)
    # ---------------------------
    if focus_text:
        parts.append(
            "This directly supports what you shared about your current priorities: "
            f"**{focus_text.strip()}**."
        )

    if market_text:
        parts.append(
            "It also helps you stay in front of the audiences most relevant to your business: "
            f"**{market_text.strip()}**."
        )

    return "\n\n".join(parts)


# Dazhou 11/17 Advertiser
def _render_table(label: str, sel, all_products: set[str], prepay_full_year: bool, is_advertiser: bool,
                  grand_total: float | None = None,
                  include_award: bool = False):  # Gabri Award# Dazhou 11/17 Advertiser # Yuchen 11/19 Early Bird # Yuchen 11/20 Networking Event
    import pandas as pd

    rows = rows_from_selection(
        label=label,
        sel=sel,
        focus=focus_text,
        market_target=market_text,
        all_products=all_products,
        prepay_full_year=prepay_full_year,
        is_advertiser=is_advertiser,  # Dazhou 11/17 Advertiser
        proposal_date=proposal_date,  # Yuchen 11/19 Early Bird
        grand_total=grand_total,  # Yuchen 11/20 Networking Event
    )
    # Gabri Award
    # ---------- Insert Award begin ----------
    award_extra_total = 0.0
    # Award only in industry pool
    if label == "industry" and matched_award and include_award:
        award_price = AWARD_NAME_TO_PRICE.get(matched_award, 0.0)
        award_desc = AWARD_NAME_TO_DESCRIPTION.get(
            matched_award,
            f"Includes recognition in {matched_award}, aligned with your business type and the Summit Awards program.",
        )

        award_row = {
            "product": "Summit — Awards Sponsorship",
            "option": matched_award,
            "qty": 1,
            "unit_price_original": award_price,
            "unit_price": award_price,
            "discount": "",
            "total_price_original": award_price,
            "total_price": award_price,
            "reasoning": award_desc,
        }
        already = any(
            r.get("product") == award_row["product"]
            and r.get("option") == award_row["option"]
            for r in rows
        )
        if not already:
            rows.append(award_row)
            award_extra_total += award_price

    # ---------- Insert Award End ----------

    # ✅ apply overrides AFTER all auto-insert logic (award / free network, etc.) Yuchen 11.30
    rows = _apply_price_overrides(label, rows)

    # Dazhou 11/26 ambassador bug fix
    current_table_total = 0.0

    # Dazhou 11/26 Subtotal price fix
    if rows:
        # Calculate the subtotal price on here
        current_table_total = sum(r["total_price"] for r in rows)
        st.write(f"**Subtotal:** ${current_table_total:,.2f}")

    if rows:
        df = pd.DataFrame(
            rows,
            columns=[
                "product",
                "option",
                "qty",
                "unit_price_original",
                "unit_price",
                "discount",
                "total_price_original",
                "total_price",
                "reasoning",
            ],
        )

        current_table_total = df["total_price"].sum()

        df.index = df.index + 1
        # Dazohu 11/24 grand total
        df = df.rename(columns={
            "product": "Product",
            "option": "Option",
            "qty": "Qty",
            "unit_price_original": "Unit Original Price",
            "unit_price": "Price",
            "discount": "Discount / Notes",
            "total_price_original": "Total Original",
            "total_price": "Total Price",
            "reasoning": "Reasoning"
        })

        st.markdown(
            """
            <style>
            table { table-layout: fixed; width: 100%; border-collapse: collapse; }
            thead th { font-weight: 700; font-size: 16px !important; text-align: left; padding: 6px; }
            td { white-space: normal !important; word-wrap: break-word !important; font-size: 15px; vertical-align: top; padding: 6px; }

            /* --- 适配 Index 列的宽度分配 (共 10 列) --- */

            /* Col 1: Index (新增 - 极窄，灰色字体) */
            th:nth-child(1), td:nth-child(1) { width: 4%; color: #888; text-align: center; }

            /* Col 2: Product (11%) */
            th:nth-child(2), td:nth-child(2) { width: 11%; }

            /* Col 3: Option (12%) */
            th:nth-child(3), td:nth-child(3) { width: 12%; }

            /* Col 4: Qty (4%) */
            th:nth-child(4), td:nth-child(4) { width: 4%; text-align: center; }

            /* Col 5 & 6: 单价 (7.5% each) */
            th:nth-child(5), td:nth-child(5) { width: 7.5%; }
            th:nth-child(6), td:nth-child(6) { width: 7.5%; }

            /* Col 7: Discount (9%) */
            th:nth-child(7), td:nth-child(7) { width: 9%; }

            /* Col 8 & 9: 总价 (7.5% each) */
            th:nth-child(8), td:nth-child(8) { width: 7.5%; }
            th:nth-child(9), td:nth-child(9) { width: 7.5%; }

            /* Col 10: Reasoning (最后一列 - 剩余约 25%) */
            th:nth-child(10), td:nth-child(10) { width: 30%; }

            </style>
            """,
            unsafe_allow_html=True,
        )
        st.table(df)
    else:
        st.info("No items selected.")
    # Dazhou 11/26 ambassador bug fix
    return current_table_total


# ---------- Generation (compute only) ----------
if st.session_state.get("_trigger_generate"):
    st.session_state["_trigger_generate"] = False
    award_selected = st.session_state.get("_award_selected", False)

    if not chosen:
        st.warning("Select at least one product.")
        st.stop()

    subset = {k: catalog[k] for k in chosen if k in catalog}
    subset = apply_summit_rules(subset, profile_text=profile_text, is_advertiser=is_advertiser)

    t_set, i_set = partition_by_category(subset, {k: meta.get(k, {}) for k in subset.keys()})

    t_sel, i_sel, grand_total, warning_msg = run_fill_to_cap(
        total_budget, tourist_pct, industry_pct, t_set, i_set, meta, proposal_date,
        is_advertiser=is_advertiser,
        interactive_map_quarters=st.session_state.get("interactive_map_quarters", 1),
    )

    all_products = set(t_sel.picks.keys()) | set(i_sel.picks.keys())

    st.session_state["proposal_state"] = {
        "t_sel": t_sel,
        "i_sel": i_sel,
        "grand_total": grand_total,
        "warning_msg": warning_msg,
        "all_products": all_products,
        "subset": subset,
        "award_selected": award_selected,
    }

# ---------- Render (always) ----------
state = st.session_state.get("proposal_state")
if state:
    t_sel = state["t_sel"]
    i_sel = state["i_sel"]
    grand_total = state["grand_total"]
    warning_msg = state["warning_msg"]
    all_products = state["all_products"]
    subset = state["subset"]
    award_selected = state["award_selected"]

    # --- 下面把你原来的渲染整段粘进来 ---
    st.subheader("Tourist Pool")
    t_real_total = _render_table("tourist", t_sel, all_products, prepay_full_year, is_advertiser, grand_total,
                                 award_selected)
    st.markdown("---")
    st.markdown("---")

    st.subheader("Industry Pool")
    i_real_total = _render_table("industry", i_sel, all_products, prepay_full_year, is_advertiser, grand_total,
                                 award_selected)
    st.markdown("---")

    grand_total_with_award = t_real_total + i_real_total

    if warning_msg:
        st.warning(f"{warning_msg}")

    left, right = st.columns([0.78, 0.22])
    with left:
        st.markdown(f"### Grand Total: ${grand_total_with_award:,.2f} (Hard cap = ${total_budget * 1.10:,.0f})")
    with right:
        st.markdown("")
        if st.button("Price Override", use_container_width=True):
            st.session_state["override_open"] = True

    with st.expander("Allocator input preview"):
        st.code(format_product_block(subset, {k: meta.get(k, {}) for k in subset.keys()}))

    try:
        digi_text = make_digital_ads_paragraph(audience_type_text, focus_text, market_text, meta)
        if digi_text:
            st.markdown("---")
            st.markdown(digi_text)
    except Exception as e:
        st.warning(f"Digital advertising note unavailable: {e}")

# ---------- Price Override UI ----------
if st.session_state.get("override_open") and state:
    import pandas as pd

    t_sel = state["t_sel"]
    i_sel = state["i_sel"]
    grand_total = state["grand_total"]
    all_products = state["all_products"]
    award_selected = state["award_selected"]

    # 1) rebuild rows (same source of truth as tables)
    t_rows = rows_from_selection(
        label="tourist",
        sel=t_sel,
        focus=focus_text,
        market_target=market_text,
        all_products=all_products,
        prepay_full_year=prepay_full_year,
        is_advertiser=is_advertiser,
        proposal_date=proposal_date,
        grand_total=grand_total,
    )

    i_rows = rows_from_selection(
        label="industry",
        sel=i_sel,
        focus=focus_text,
        market_target=market_text,
        all_products=all_products,
        prepay_full_year=prepay_full_year,
        is_advertiser=is_advertiser,
        proposal_date=proposal_date,
        grand_total=grand_total,
    )

    # 2) keep award row consistent with _render_table
    if award_selected:  # label just to mirror logic
        try:
            if matched_award:
                already = any(
                    r.get("product") == "Summit — Awards Sponsorship"
                    and r.get("option") == matched_award
                    for r in i_rows
                )
                if not already:
                    award_price = AWARD_NAME_TO_PRICE.get(matched_award, 0.0)
                    i_rows.append({
                        "product": "Summit — Awards Sponsorship",
                        "option": matched_award,
                        "qty": 1,
                        "unit_price_original": award_price,
                        "unit_price": award_price,
                        "discount": "",
                        "total_price_original": award_price,
                        "total_price": award_price,
                        "reasoning": AWARD_NAME_TO_DESCRIPTION.get(matched_award, ""),
                    })
        except Exception:
            pass

    # 3) apply existing overrides so "Current Total" matches what's on the page now
    # base (no override)
    t_base = t_rows
    i_base = i_rows

    # current (with override applied)
    t_curr = _apply_price_overrides("tourist", t_base)
    i_curr = _apply_price_overrides("industry", i_base)

    # 4) build editor df
    rows = []
    for pool, base_list, curr_list in [
        ("tourist", t_base, t_curr),
        ("industry", i_base, i_curr),
    ]:
        # 用 key 对齐（防止顺序变）
        base_map = {f"{pool}|{r.get('product', '')}|{r.get('option', '')}": r for r in base_list}
        curr_map = {f"{pool}|{r.get('product', '')}|{r.get('option', '')}": r for r in curr_list}

        for k, b in base_map.items():
            c = curr_map.get(k, b)
            rows.append({
                "Pool": pool,
                "Product": b.get("product", ""),
                "Option": b.get("option", ""),
                "Qty": float(b.get("qty") or 1),
                "Base Total": float(b.get("total_price") or 0.0),
                "Current Total": float(c.get("total_price") or 0.0),
                "New Total": float(c.get("total_price") or 0.0),  # 默认=当前
            })

    df0 = pd.DataFrame(rows)


    def _apply_from_editor(edited_df: pd.DataFrame):
        overrides = st.session_state.get("price_overrides", {}) or {}
        for _, row in edited_df.iterrows():
            k = f"{row['Pool']}|{row['Product']}|{row['Option']}"
            base = float(row["Base Total"])
            new = float(row["New Total"])
            if abs(new - base) > 1e-9:
                overrides[k] = new
            else:
                overrides.pop(k, None)
        st.session_state["price_overrides"] = overrides
        st.session_state["override_open"] = False
        st.rerun()


    # 5) dialog if available, otherwise fallback panel
    if hasattr(st, "dialog"):
        @st.dialog("Price Override")
        def _dlg():
            edited = st.data_editor(
                df0,
                hide_index=True,
                disabled=["Pool", "Product", "Option", "Qty", "Current Total"],
                column_config={
                    "New Total": st.column_config.NumberColumn(min_value=0.0, step=50.0, format="%.2f")
                },
                use_container_width=True,
                key="override_editor",
            )

            c1, c2, c3 = st.columns([0.34, 0.33, 0.33])
            with c1:
                if st.button("Apply", type="primary", use_container_width=True):
                    _apply_from_editor(edited)
            with c2:
                if st.button("Reset", use_container_width=True):
                    st.session_state["price_overrides"] = {}
                    st.session_state["override_open"] = False
                    st.rerun()
            with c3:
                if st.button("Cancel", use_container_width=True):
                    st.session_state["override_open"] = False
                    st.rerun()


        _dlg()
    else:
        st.subheader("Price Override")
        edited = st.data_editor(
            df0,
            hide_index=True,
            disabled=["Pool", "Product", "Option", "Qty", "Base Total", "Current Total"],
            use_container_width=True,
            key="override_editor",
        )
        c1, c2, c3 = st.columns([0.34, 0.33, 0.33])
        with c1:
            if st.button("Apply", type="primary", use_container_width=True):
                _apply_from_editor(edited)
        with c2:
            if st.button("Reset", use_container_width=True):
                st.session_state["price_overrides"] = {}
                st.session_state["override_open"] = False
                st.rerun()
        with c3:
            if st.button("Cancel", use_container_width=True):
                st.session_state["override_open"] = False
                st.rerun()

