# SpecToCart — Vision-First Project Procurement & PayPal Spend Control

**Snap a project photo. Source verified retail SKUs. Enforce hard budget guardrails and settle in one click with PayPal.**

SpecToCart transforms any single DIY or jobsite renovation photo—such as resurfacing a worn kitchen countertop or framing a gallery wall—into a priced, policy-verified Bill of Materials (BOM). Instead of forcing buyers to hunt across dozens of retail tabs or risking AI product hallucinations, SpecToCart pairs **Gemini Vision** with the **Channel3 Product Graph** to score live retail inventory, enforces deterministic **Spend Policy Gates**, and settles the entire project manifest in a single **PayPal Sandbox** or **PayPal Vault v3** checkout.

---

## Why SpecToCart Wins on Trust & Spend Governance

1. **Zero-Hallucination Channel3 Retrieval (`select_best_offer`):**
   - Evaluates live retail inventory using stemmed token overlap, documented retail synonyms (`_TOKEN_SYNONYMS`), and strict head-noun verification.
   - Every candidate SKU is logged in an immutable audit trail with explicit win reasons (`highest_relevance`, `relevance_tie_lowest_price`, `only_valid_offer`) or rejection codes (`head_noun_missing`, `insufficient_relevance`, `price_over_cap`, `score_zero`, `currency_mismatch`, `price_unreadable`). If no candidate passes, `NoValidOfferError` halts execution before PayPal is ever touched.
2. **Deterministic Spend Policy Gate & Auto-Trim Fallback (`apply_budget_fallback`):**
   - Enforces user budget caps at both the BOM generation stage and post-sourcing.
   - If live retail pricing pushes a cart over the authorized cap, the policy engine automatically drops `Optional` items in deterministic order (`ascending relevance, descending line total`) while protecting `Essential` items.
   - Server-side `/api/create-order` enforces `HTTP 409 POLICY_BREACH` if a cart exceeds its cap, ensuring the guardrail holds even if the browser UI is bypassed.
3. **Full-Lifecycle PayPal Architecture (Orders v2, Vault v3, & Verified Webhooks):**
   - **Cryptographic Order Binding:** Binds the source photo's SHA-256 prefix directly into the PayPal purchase unit `custom_id` and generates a collision-safe `invoice_id` (`SPECTOCART-<hash>-<8 hex>`) alongside `PayPal-Request-Id` idempotency headers.
   - **PayPal Vault v3 Returning Buyer Flow:** Converts `/v3/vault/setup-tokens` into persistent `/v3/vault/payment-tokens` stored server-side, enabling 1-click buyer-not-present settlement on subsequent projects using `stored_credential { MERCHANT, UNSCHEDULED, SUBSEQUENT }`.
   - **Cryptographically Verified Webhooks (`/webhook`):** Verifies incoming `PAYMENT.CAPTURE.COMPLETED` and `PAYMENT.CAPTURE.REFUNDED` events via `POST /v1/notifications/verify-webhook-signature` (`verification_status == "SUCCESS"`), deduplicates by `event.id`, and resolves refund objects back to their parent order via capture ID link matching.
4. **Deterministic Project Math & Second-Pass AI Critic:**
   - Computes exact material roll quantities from surface dimensions (`ceil(area_sqft × 1.10 waste / coverage_per_roll)`) and room paint gallon derivations (`ceil((perimeter × height - openings) × coats / 350)`) in Python.
   - Includes a second-pass Gemini semantic critic (`--critic` / `--strict-critic`) and automated step-by-step installation planner.

---

## Verified Live Proof (PayPal Sandbox & Channel3 Product Graph)

All transactions below were executed live against `api-m.sandbox.paypal.com` and `api.trychannel3.com/v1/search` and are preserved in committed JSON audit receipts:

| Workflow / Verification Milestone | Total (USD) | PayPal Order / Capture / Proof ID | Fee / Merchant Net |
| :--- | :--- | :--- | :--- |
| **Countertop Resurfacing (Patch 8 v6 + Project Math + Critic)** | `$49.79` | Order `1GU80970KT059953X` • Capture `4ET24157KE976933Y` | Fee `$2.23` • Net `$47.56` |
| **Spend Policy Auto-Trim (`$48.00` Cap Enforcement)** | `$49.79 → $45.54` | Receipt `demo/demo_fallback_receipt.json` (Dropped Optional Degreaser `$4.25`) | Saved `$4.25` under cap |
| **Web App PayPal JS SDK Server-Side Checkout** | `$54.16` | Capture `8YN70180712304914` | Fee `$2.38` • Net `$51.78` |
| **PayPal Vault v3 Subsequent Charge (Buyer-Not-Present)** | `$35.61` | Order `9DA556555C6203348` • Capture `9U6516399J173801E` | Fee `$1.73` • Net `$33.88` |
| **Webhook Signature Verification & Refund Resolution** | `REFUNDED` | Order `03L03592KH070850F` • Capture `2C255679WD8268150` • Refund `95100155TW447110E` | Verified `SUCCESS` |
| **Multi-Domain Generalization: Gallery Wall (`bare_wall.jpg`)** | `$41.47` | Receipt `demo/second_domain_receipt.json` (`$120.00` Budget Cap) | 4 Verified SKUs |

---

## Judge Quickstart — Try It in 2 Minutes (Zero API Keys Required)

You can run the full 35-test verification suite and execute the deterministic Spend Policy Fallback engine offline right out of the box:

```bash
git clone https://github.com/alexowoade/spectocart
cd spectocart
python3 -m venv venv
venv/bin/pip install requests python-dotenv pillow pydantic google-genai flask

# 1. Run the 35 pure-function unit tests (relevance, stemming, synonyms, math, fallback, retry chain)
venv/bin/python test_spectocart_patch8.py

# 2. Run the deterministic Spend Policy Gate demo ($49.79 cart auto-trimmed to $45.54 under a $48 cap)
venv/bin/python core_pipeline.py demo-fallback demo/saved_cart.json 48

# 3. Run the deterministic multi-domain Paint Math engine (48ft x 9ft room, 2 coats -> 3 gallons)
venv/bin/python core_pipeline.py demo-paint-math --perimeter 48 --height 9 --coats 2 --openings 40
```

## Live Web App & CLI Setup (Ubuntu / Python 3.12)

- Copy `.env.example` to `.env` and populate your credentials (`chmod 600 .env`):
  - `GEMINI_API_KEY` (Uses gemini-3.6-flash / gemini-3.8-flash with automatic in-family transient failover)
  - `CHANNEL3_API_KEY` (Live product search against https://api.trychannel3.com/v1/search)
  - `PAYPAL_CLIENT_ID` & `PAYPAL_CLIENT_SECRET` (PayPal Sandbox Business credentials)
  - Optional: `PAYPAL_MERCHANT_ID`, `PAYPAL_WEBHOOK_ID`
- Launch the Web Application (`spectocart_web.py`):

```bash
source venv/bin/activate
python3 spectocart_web.py
# Open http://127.0.0.1:5000
```

  - Visual Spec & Instant Demo Load (`/`): Upload any project photo with surface dimensions, or use the Instant Judge Quick-Load buttons to immediately test the $150 compliant cart ($49.79) and $48 tight-budget policy fallback ($45.54).
  - Spend Gate & AG Grid Cart (`/cart`): Inspect matched Channel3 SKUs, expand the Channel3 Rejection Audit Drawers, toggle between $150 and $48 policy caps live, and settle via PayPal JS SDK or 1-Click PayPal Vault v3.
  - Reconciliation Certificates (`/orders` & `/receipt/<order_id>`): View side-by-side PayPal Settlement Proof (order_id, capture_id, fee, net, custom_id photo hash, invoice_id) and the Zero-Hallucination Audit Trail.
- Run the Full CLI Depth Pipeline (`core_pipeline.py`):

```bash
source venv/bin/activate
python3 core_pipeline.py --critic --length 96 --width 25 --budget 150
python3 core_pipeline.py status <ORDER_ID>
python3 core_pipeline.py capture <ORDER_ID>
```

## Technical Architecture & Security Hygiene

- **Server-Side Secret & Price Integrity:** Only `PAYPAL_CLIENT_ID` is rendered to the browser SDK. `PAYPAL_CLIENT_SECRET` and `vault_tokens.json` remain strictly on the server, and order totals are computed exclusively on the server from validated Channel3 unit prices.
- **Consolidated Single-Order Settlement:** SpecToCart aggregates multi-merchant discovery from the Channel3 Product Graph into a single unified PayPal order while preserving per-item `buy.trychannel3.com` verification links in the cart and audit receipt.
- **Resilient In-Family Model Routing:** `_generate_with_fallback_and_retry()` automatically handles transient 503 UNAVAILABLE and 429 RESOURCE_EXHAUSTED spikes by failing over between gemini-3.6-flash and gemini-3.8-flash before applying exponential backoff.

## License

MIT — see LICENSE.
