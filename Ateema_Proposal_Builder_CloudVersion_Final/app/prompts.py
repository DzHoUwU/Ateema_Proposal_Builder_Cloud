from langchain_core.prompts import PromptTemplate

PROPOSAL_PROMPT = PromptTemplate.from_template("""
You are an AI media strategist. Construct a concise, data-driven proposal using ONLY the provided products
and their structured fields. Output STRICT JSON matching the schema below.

=== Client Profile ===
{profile}

Focus: emphasize the client's stated **Focus** and **Market Target** when recommending products.

=== Budget ===
Total: ${budget} (USD)
Target split — Tourist: {alloc_impact}%   |   Industry: {alloc_awareness}%
Soft cap: ${budget_cap}  (≤ {soft_cap_pct}% above budget)

=== Reference Clients ===
{similar_clients}

=== Allowed Products (exact names only) ===
{allowed_products}

=== Product Data (verbatim) ===
{product_data}

=== Product Descriptions (verbatim) ===
{product_descriptions}

=== Sales Strategies (verbatim) ===
{sales_strategies}

=== Option Notes (verbatim) ===
{option_notes}

=== Discount Policies (verbatim) ===
{discount_policies}

OUTPUT STRICT JSON:
{{
  "client_name": "<from profile or given>",
  "budget": {budget},
  "currency": "USD",
  "selections": [
    {{
      "product_name": "<exact product name from Allowed Products>",
      "chosen_option": "<exact option/item name>",
      "chosen_price_window": "<label if applicable>",
      "unit_price": <number>,
      "qty": <integer>,
      "line_total": <number>,
      "reasoning": "2–3 sentences. Must cite details from: (a) option notes if present, (b) product description, (c) sales strategy. Tie back to Focus/Market Target.",
      "discount_policy_note": "<one-sentence summary of the relevant discount rules for this line item>"
    }}
  ],
  "subtotal": <number>,
  "notes": "<≤3 sentences summarizing split adherence and any trade-offs.>"
}}

CONSTRAINTS:
- Use only factual text from provided fields; no fabrication.
- Spend close to total budget; prefer higher tiers when feasible without exceeding soft cap.
- Respect tourist/industry pools and never exceed budget_cap.
- One option per product; qty=1 unless text explicitly allows multiples.
""")