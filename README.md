# SpecToCart — vision-first, project-specific procurement

**Photograph a project, AI builds a priced shopping list for that one job, you check out once with PayPal. This is not recurring household restocking.**

SpecToCart is built for a single DIY job — for example, wrapping a worn kitchen countertop with waterproof marble contact paper and sealant — not for replenishing the same household items every week. You upload a photo, Gemini Vision builds a structured Bill of Materials (BOM) for that project, Channel3 finds live products for each BOM item, and PayPal Sandbox checks out the whole cart in one order.

This honesty matters, and it is stated up front: SpecToCart aggregates sourced items into **one PayPal checkout**. It does **not** place separate orders with Lowe's, Walmart, or any other retailer. The Channel3 `buy_url` links on the cart page are the per-retailer links for each item, for the user to check.

## AI tools used (explicit, for the judges)

- **Gemini 3.6 Flash (primary, proven live 2026-10-28) — BOM, step plan, critic.** Gemini Vision reads the uploaded project photo and returns a structured Bill of Materials (3-5 items, quantity, max unit price, priority, search query, reason). A second Gemini call per item (critic) checks category/use plausibility. A third call generates the ordered step plan.
- **Gemini 3.8 Flash (fallback).** Patch 8 v6 falls back in-family on transient errors only, then retries the chain. No other model is used.
- **Channel3 Product Graph** — live product search and relevance scoring (not a generic web search).
- **PayPal Sandbox** — order, approval, status, capture, refund, Vault (live-tested second charge, web app), webhooks (pending public URL). No real money, sandbox test accounts only.
- **Frameworks:** Python, Pydantic (structured Gemini output), Flask (web app), PayPal JavaScript SDK (web checkout button, server-side create/capture).

## How to score this in 3 minutes (judging criteria)

- **Problem:** A DIYer photographs a worn project and needs a priced, project-specific shopping list, not a generic search. See the first paragraph above.
- **Technical implementation:** Photo -> Gemini BOM -> Channel3 relevance scoring with rejection reasons -> project math in Python -> critic -> PayPal order -> approve -> capture -> audit receipt. Sample receipt: `demo/demo_receipt.json` ($49.79 run, capture `4ET24157KE976933Y`, fee $2.23, net $47.56).
- **Design / UI:** Web app `/cart` shows win reasons and rejection lists per item, not just a price; `/orders` shows fee, net, and refund. CLI prints the same audit trail.
- **Potential impact:** One pipeline, two demo domains: countertop ($49.79, capture `4ET24157KE976933Y`) and gallery wall ($41.47, `--no-paypal` dry run, receipt `demo/second_domain_receipt.json`). The gallery-wall photo is a real Pexels bare-wall photo (1200x675, white wall above a sofa), BOM $94.00, sourced $41.47 under a $120 budget: Belle Maison 7pc frame set $17.99, Room Essentials set of 6 abstract prints $10.00, Command hanging strips $3.49, bubble level vials $9.99. The level item is a partial match (vials only, no tape measure in the title) — recorded honestly here, not hidden — and the run had no critic pass, so no critic verdict is claimed for it. The receipt, not a new feature per domain, is what generalises.
- **Innovation:** Vision-first, project-specific procurement with project math, budget fallback policy, and critic verdicts recorded in the receipt — not recurring replenishment, and not a generic cart.
- **Domain coverage (honest, 2026-10-29):** Wrap is live — `demo/receipt_wrap.json` is a fresh fixed-scorer run (2026-10-29, `--critic --no-paypal`, $34.03: Adhesive Shelf Liner - Marble $7.99x2, Art3d Smoothing Tool Kit $7.82, Clear Silicone Kleen Seam $5.98 with critic REJECT kept non-strict, Spray Nine $4.25, project math ceil(16.67x1.10/15.00)=2 rolls, steps 5 / 120 min). Painting is code-complete but live-blocked: `compute_paint_math()` is unit-tested (48ft x 9ft - 40sqft openings, 2 coats, 350sqft/gallon = 3 gallons, `unit_kind="gallon"`), but Channel3 does not currently return painter's tape — across 7 queries and limit 25, no product titled painter's tape appeared, and the fixed scorer correctly rejects paint/cleaner products as `head_noun_missing` — so no live paint cart exists yet, and none is claimed here. The paint math is unit-tested and the live cart is blocked on inventory.
- **Presentation:** Demo video under 3 minutes (last, after Vault/webhooks, so it shows the final product): photo -> BOM -> project math -> receipt with buy links and critic verdicts -> PayPal approve -> capture COMPLETED -> demo-fallback $49.79 -> $45.54 at budget $48.

## What works today (verified live, sandbox only, no real money)

- **CLI pipeline** (`core_pipeline.py`, Patch 8 v6): photo → Gemini BOM → Channel3 sourcing → PayPal order → approve → status → capture, with an audit receipt (`spectocart_receipt_<ORDER_ID>.json`) for every order.
- **Web app** (`spectocart_web.py`, Flask, Patch 8 cart loop): photo upload → `/cart` with relevance scores, win reasons, and rejection lists → PayPal JavaScript SDK button (server-side create/capture, Secret never leaves the server) → `/orders` dashboard with fee, net, and a sandbox refund button, plus a `/webhook` listener.
- **JS SDK live test passed 2026-10-28**: cart $54.16 captured via the button — capture `8YN70180712304914`, fee $2.38, net $51.78.
- **CLI captures completed** (all PayPal Sandbox, test accounts only):
  - $28.04 — capture `5E32968891787943S`, fee $1.47, net $26.57
  - $44.55 — capture `5U826063RY046684S`, fee $2.04, net $42.51
  - $49.87 — capture `0R298208FW259542P`, fee $2.23, net $47.64
  - $51.64 — capture `5X925796KR327684F`, fee $2.29, net $49.35 (Patch 8 v5, with critic verdicts in the receipt)
  - $49.79 — capture `4ET24157KE976933Y`, fee $2.23, net $47.56 (Patch 8 v6, --critic, project math ceil(16.67x1.10/15.00)=2 rolls, critic OK/OK/REJECT/OK, sealant REJECT correct: seam cover vs liquid sealant)
  - **Vault second charge, live-tested 2026-10-28 in the web app: $35.61 — order `9DA556555C6203348`, capture `9U6516399J173801E`, fee $1.73, net $33.88, Method = vaulted in `/orders`, no browser approval for that second project.** Setup-token -> payment-token saved on `/vault` (Personal buyer John Doe, customer id present), then a fresh project charged from the vault with `payment_source.paypal.vault_id` + `stored_credential { MERCHANT, UNSCHEDULED, SUBSEQUENT }`. The order completed on creation; a second capture attempt correctly returned `ORDER_ALREADY_CAPTURED`.
- **Do not re-capture any completed order.** A capture is final; create a fresh order for a new test or a refund demo.

## How it works

1. **Gemini Vision → BOM.** `extract_bom_from_image()` asks Gemini for 3–5 items, each with a quantity, a max unit price, a priority (`Essential` / `Optional`), a retail search query, and a reason. The BOM total must be under the budget, or the run stops before any sourcing.
2. **Channel3 → relevance scoring, not just cheapest.** `select_best_offer()` scores every candidate by token overlap with the BOM item name, with documented retail synonyms (`_TOKEN_SYNONYMS`: contact/paper ↔ liner, laminate, film, covering; sealant ↔ caulk; degreasing ↔ degreaser; cleaning ↔ cleaner; wrapping ↔ wrap). **"Sticker" is deliberately not a synonym for "wrap"**, so marble wall stickers do not win a wrap job. Relevance scoring was fixed 2026-10-29 (34 tests): possessives are stripped before tokenizing (`Painter's` → `painter`, so no spurious `s` match from `Elmer's`), and container/unit words in `_HEAD_NOUN_EXCEPTIONS` (roll, kit, pack, inch, etc.) no longer earn a match point, just as they never counted as a head noun — so a Washi Tape Roll no longer scores 2 against painter's tape.
   - A winner needs at least 2 matched words (1 for a single-token item) **and** a head-noun match (one of the last meaningful words of the BOM name).
   - Win reasons are recorded: `highest_relevance`, `relevance_tie_lowest_price`, `only_valid_offer`.
   - Every rejected candidate is recorded with a reason: `price_unreadable`, `currency_mismatch`, `price_over_cap`, `score_zero`, `insufficient_relevance`, `head_noun_missing`. No fake fallback product is ever invented — if no candidate passes, the run raises `NoValidOfferError` with the full rejection list, and stops before PayPal.
3. **Project math (CLI, `--length` / `--width` in inches).** Roll quantity is derived in Python: `ceil(area_sqft × 1.10 waste / coverage_per_roll)`, and the derivation is recorded in the receipt. If dimensions are not supplied, the BOM quantity is used and the receipt records `assumptions=["dimensions_missing"]`. Dimensions are never invented from a photo.
4. **Budget enforcement + fallback.** The budget is checked after the BOM and again after sourcing. If sourcing is over budget, `apply_budget_fallback()` drops Optional items one at a time, in policy order (ascending relevance, descending line total), until under budget. Essential items are never dropped. Only Essential-only over budget stops the run.
5. **Critic pass (CLI, `--critic`).** A second Gemini call per item asks: is this plausibly the right category and use? Verdicts are recorded in the receipt. Without `--strict-critic`, verdicts are logged but not acted on, so a demo cannot be killed by an LLM verdict. With `--strict-critic`, Optional failures are dropped and Essential failures abort the run. The critic is a semantic safety net for cases token scoring cannot tell apart — for example, a silicone seam cover or silicone craft stamps versus a silicone sealant tube, or a glue gun versus a heat gun. The 2026-10-28 $51.64 run is honest proof of this: its receipt contains 3 critic REJECTs, and it is labelled here as a pipeline test, not a clean demo cart.
6. **Step plan (CLI).** A Gemini call generates an ordered step plan naming which BOM item each step uses, with minute estimates. If Gemini fails, a deterministic template fallback is used.
7. **PayPal Sandbox checkout.** Order creation, approval, status, and capture are separate steps. An order is **not** a payment: you must approve it as a Personal sandbox buyer (not the merchant account — a merchant cannot pay itself), check `status` says `APPROVED`, and only then `capture`. Idempotency uses `PayPal-Request-Id` on order creation and capture. Refunds are built (`refund <CAPTURE_ID>`) but should only be run on a fresh test order, never on a proof capture you want to keep.

## Gemini reliability notes (read this before judging a live run)

- **503 UNAVAILABLE is transient Google overload, not a key or code failure.** Patch 8 v6 retries only transient Gemini errors (503 / UNAVAILABLE / ServerError) and falls back in-family first: primary `GEMINI_MODEL` (now `gemini-3.6-flash`, proven live 2026-10-28) then `gemini-3.8-flash`, immediately on transient, then chain retry waiting 30s then 60s, for the BOM, step-plan, and critic calls. Non-transient errors (400, 401, bad schema) are not retried and are not masked by a fallback. Both models can spike together — the 2026-10-28 evening critic run failed on both models, then succeeded on chain attempt 3 after 30s + 60s waits.
- **429 RESOURCE_EXHAUSTED is a Gemini API rate limit, not a code failure.** One full `--critic` run uses about 6 calls (BOM + 4 critic + step plan). Run without `--critic` for most tests (2 calls per run), save `--critic` for final verification, and wait for the quota window to reset if you hit the limit. Do not hammer retries on a 429 — wait for the `retryDelay` Google returns.
- **Demo mode (offline, no API calls).** `python3 core_pipeline.py demo-fallback demo/saved_cart.json 40` loads a saved cart, applies the budget fallback at the given budget, and writes a demo receipt labelled `demo_mode: true`. This exists for reproducibility, not as a workaround for API limits: Gemini regenerates the BOM on every live run, so a saved cart is the only way to re-record the exact same tight-budget fallback demo twice. Demo data is never presented as live data. The committed `demo/saved_cart.json` was regenerated 2026-10-28 evening from the clean Patch 8 v6 $49.79 run (order `1GU80970KT059953X`, capture `4ET24157KE976933Y`): wrap Con-Tact $16.78x2, tool Smoother & Utility Knife Kit $5.99, sealant Silicone Seam Cover $5.99 (critic REJECT, kept non-strict), degreaser Spray Nine $4.25. The old wall-sticker and "Silicone for Body" matches are gone. Tight-budget fallback demo: `python3 core_pipeline.py demo-fallback demo/saved_cart.json 48` gives $49.79 -> $45.54, Optional degreaser dropped.

## Setup (Ubuntu, Python 3.12)

```bash
cd ~/spectocart
python3 -m venv venv
source venv/bin/activate
pip install requests python-dotenv pillow pydantic google-genai flask
```

Copy `.env.example` to `.env` and fill in your own keys. Never commit `.env`, and never paste a key in chat or in a public repo. `chmod 600 .env`.

Required keys: `GEMINI_API_KEY`, `CHANNEL3_API_KEY`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`.
Optional: `PAYPAL_MERCHANT_ID` (sets the PayPal payee; if unset, the default sandbox payee is used), `PAYPAL_WEBHOOK_ID` (only for webhook signature verification), `PAYPAL_RETURN_URL`, `PAYPAL_CANCEL_URL`.

## Run it

CLI (depth demo, with critic, project math, and step plan):

```bash
source venv/bin/activate
python3 test_spectocart_patch8.py          # expect 26 tests passed (Patch 8 v6)
python3 core_pipeline.py --critic --length 96 --width 25
# then, when an order is CREATED:
python3 core_pipeline.py status <ORDER_ID>   # must say APPROVED first
python3 core_pipeline.py capture <ORDER_ID>
```

Web app (JS SDK button + dashboard):

```bash
source venv/bin/activate
python3 spectocart_web.py
# open http://127.0.0.1:5000
```

The web app must sit next to `core_pipeline.py` and use the same `.env`. Only `PAYPAL_CLIENT_ID` goes to the browser; the Secret stays server-side, and amounts are computed server-side from the saved cart, never trusted from JavaScript.

## Channel3 — what is and is not verified

The endpoint `https://api.trychannel3.com/v1/search` (header `x-api-key`, `x-user-id: spectocart_demo_user`, `filters.price.max_price`, `availability: ["InStock"]`) has been proven working empirically across many live runs. Raw schema verified 2026-10-28 evening with `DEBUG_CHANNEL3=1`: product fields are `id, title, description, brands[], images[], category{slug,title,path[]}, materials[], key_features[], colors[], offers[]{url,domain,price{price,compare_at_price,currency},availability,condition,max_commission_rate,dimensions}, variants, structured_attributes, ratings`. `buy_url` populates from `offers[0].url` (a `buy.trychannel3.com` link) — there is no top-level `buy_url` field. Merchant = `offers[0].domain`. Category IS present. Shipping cost / delivery time is NOT present in the raw JSON, so shipping is not claimed. Cross-merchant grouping and single-store cart remain Future work, not a current claim.

## Future work (honestly not yet live-tested)

- **PayPal Vault (enabled, not yet integrated)**: sandbox Save payment methods is ON for the Spec2cart app, and a throwaway setup-token test passed 2026-10-28 evening (setup token 201, payment token 201, no charge, customer id present). The returning-DIYer story is: first project manual approval, second project from a vaulted method under an explicit Billing Agreement / future-payment consent — not a generic saved card, and not recurring replenishment. The second-charge integration (fresh token -> real order in `/orders`, receipt recording 'vaulted method') is still to be built and live-tested after the Gate, and is not yet in the web app. If that second charge is not live-tested, Vault must not be claimed as working.
- **Webhooks on a public URL**: the `/webhook` listener exists and logs/verify-attempts events, and updates `/orders` from `PAYMENT.CAPTURE.COMPLETED` / `PAYMENT.CAPTURE.REFUNDED`, with signature verification via `/v1/notifications/verify-webhook-signature` and idempotent duplicate handling to be live-tested. PayPal can only deliver these to a public HTTPS URL (Render or a tunnel), which is not yet set up, so webhook delivery itself is not yet live-tested. On localhost alone, no webhook delivery is expected. Kill-switch: if signature verification is not clean by end of day 1 of the webhook block, ship without webhooks.
- **Single-store cart**, **cheaper-Optional substitution** (currently Optional items are dropped, never substituted), **subscriptions, Pay Later, and disputes** — deliberately not built, because they do not differentiate a project-specific procurement tool and would be checklist depth.
- **Web app project math, step plan, and critic** — currently CLI-only.

## Hackathon

PayPal AI Hackathon on Devpost, deadline 2026-11-12 21:00 GMT+1. The only fundamental requirement is meaningful use of both PayPal and AI, which this meets: Gemini Vision + Channel3 product graph + PayPal Sandbox checkout, with an audit receipt a judge can verify. Sponsors include Channel3 (Best Use of Channel3, $1,500).

## License

MIT — see `LICENSE`.
