"""
SpecToCart Web App - Step 1 before the demo video.
Updated 2026-10-28 to Patch 8 cart rules (see /analyze and /cart):
  - win_reason and evaluated/rejection lists are shown per item,
    so a judge can see why a product won and why others lost.
  - Over-budget carts no longer return a bare 400. They use the
    pipeline's apply_budget_fallback(): Optional items are dropped
    in policy order (ascending relevance, descending line total)
    and the drops are shown on the cart page. Only if even
    Essential-only is over budget does /analyze stop.
  - A NoValidOfferError from Channel3 sourcing no longer becomes
    a Flask 500. /analyze shows a readable page with the item,
    the search query, and every evaluated candidate with its
    rejection reason, so the failure is auditable.
  - The old "weak match" warning (relevance_score == 1) is removed.
    Under Patch 8 a score-1 product is rejected outright for
    multi-token items, so a winning score of 1 should not happen
    except for a single-token item, and the win_reason + rejection
    list are the honest signal, not a score-1 flag.
  - Project math, step plan, and critic pass are CLI depth
    features (core_pipeline.py --critic --length/--width). They are
    NOT yet in this web app; adding them here is a later step,
    not part of this cart-loop update.


Order of work, agreed 2026-10-23, Gate first:
  Gate (before any Vault code): keys confirmed + .env gitignored,
      26/26 tests, Channel3 raw schema verified, demo regenerated
      from a clean Patch 8 CLI run, flat layout, repo committed.
  Step 1 (this file): PayPal JavaScript SDK button + orders dashboard
                      + webhook listener. Video comes LAST, after all
                      of this is working, so the video shows the final
                      product, not an old terminal flow.
  Step 2 (next): PayPal Vault - save a payment method for the next
                 project, and pay with a saved token.
  Step 3 (next): Webhooks on a public URL - PayPal posts
                 PAYMENT.CAPTURE.COMPLETED / REFUNDED to /webhook,
                 the dashboard updates from that event, not just from
                 the button click.
  Step 4: Judge README.
  Step 5: Under-3-minute demo video.

What this file does, in plain terms:
  - /            : Upload a photo (or use the sample countertop),
                   set a budget, run Gemini Vision -> BOM -> Channel3.
  - /cart        : Show the BOM + sourced cart with relevance scores
                   and buy_url links, and a real PayPal button.
                   The button does NOT create the order in the
                   browser. It asks YOUR server (/api/create-order)
                   to create it, so the amount is computed server-side
                   and your PayPal Secret never leaves the server.
                   On approval, the button asks your server
                   (/api/capture-order) to capture it.
  - /orders      : Dashboard of every order this app created:
                   order id, total, status, capture id, PayPal fee,
                   merchant net, and a refund button.
  - /webhook     : PayPal posts events here. The app logs the event,
                   tries to verify the signature with PayPal
                   (POST /v1/notifications/verify-webhook-signature)
                   if PAYPAL_WEBHOOK_ID is set in .env, and updates
                   the dashboard order status when it sees
                   PAYPAL.CAPTURE.COMPLETED or REFUNDED.
                   PayPal can only reach this if the app has a public
                   HTTPS URL (Render, or a tunnel like ngrok for a
                   local test). On localhost alone, PayPal cannot
                   deliver webhooks - that is expected, not a bug.

Security notes - read these:
  - Only PAYPAL_CLIENT_ID goes to the browser, in the JS SDK script
    tag. That id is meant to be public. PAYPAL_CLIENT_SECRET stays
    on the server, in .env, and is never sent to the browser.
  - Amounts are never trusted from the browser. /api/create-order
    uses the server-side cart saved by /analyze, not a total sent
    by JavaScript.
  - This is a sandbox demo. orders.json / webhook_events.json are
    simple local files, not a production database. Do not put real
    secrets in them.

Run it (on Ubuntu, in ~/spectocart, with (venv) active):
    pip install flask
    python3 spectocart_web.py
    # then open http://127.0.0.1:5000 in a browser
    # .env must already contain GEMINI_API_KEY, CHANNEL3_API_KEY,
    # PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, and optionally
    # PAYPAL_MERCHANT_ID and PAYPAL_WEBHOOK_ID.
    # spectocart_web.py must sit next to core_pipeline.py, because
    # it imports the Gemini / Channel3 / PayPal functions from there.
"""

import json
import os
from datetime import datetime, timezone

from flask import Flask, jsonify, request, redirect, url_for

# Reuse the proven pipeline - do not copy its logic here.
from core_pipeline import (
    FALLBACK_POLICY,
    NoValidOfferError,
    apply_budget_fallback,
    capture_paypal_order,
    create_paypal_sandbox_order,
    create_sample_test_image,
    extract_bom_from_image,
    get_paypal_access_token,
    get_paypal_order_status,
    refund_paypal_capture,
    require_env,
    search_channel3_product,
)
import requests

app = Flask(__name__)

ORDERS_FILE = "orders.json"
WEBHOOK_FILE = "webhook_events.json"
CART_FILE = "last_cart.json"
VAULT_FILE = "vault_tokens.json"
VAULT_PENDING_FILE = "vault_pending.json"
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ---------------------------------------------------------------------
# Small JSON-file helpers (demo storage, not production)
# ---------------------------------------------------------------------
def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _save_order(order_record):
    orders = _load_json(ORDERS_FILE, [])
    orders = [o for o in orders if o.get("order_id") != order_record["order_id"]]
    orders.insert(0, order_record)
    _save_json(ORDERS_FILE, orders)


def _update_order(order_id, **fields):
    orders = _load_json(ORDERS_FILE, [])
    for order in orders:
        if order.get("order_id") == order_id:
            order.update(fields)
            break
    else:
        orders.insert(0, {"order_id": order_id, **fields})
    _save_json(ORDERS_FILE, orders)


# ---------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------
PAGE_CSS = """
<style>
body { font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }
h1 { font-size: 1.6rem; } table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; vertical-align: top; }
a { word-break: break-all; } .warn { color: #8a5a00; } .ok { color: #0a6b2d; }
nav a { margin-right: 1rem; }
</style>
"""


@app.route("/")
def home():
    return f"""<!doctype html><html><head><title>SpecToCart</title>{PAGE_CSS}</head><body>
<nav><a href="/">New project</a> <a href="/orders">Orders dashboard</a> <a href="/vault">Vault (returning DIYer)</a> <a href="/webhook-events">Webhook events</a></nav>
<h1>SpecToCart - vision-first, project-specific procurement</h1>
<p>Photograph a project, AI builds a priced shopping list for <b>that one job</b>,
you check out once with PayPal. This is not recurring household restocking.</p>
<form action="/analyze" method="post" enctype="multipart/form-data">
  <p><label>Photo (leave empty to use the sample countertop): <input type="file" name="photo" accept="image/*"></label></p>
  <p><label>Budget (USD): <input type="number" name="budget" value="150" step="0.01" min="1"></label></p>
  <p><label>Goal: <input type="text" name="goal" size="80" value="Wrap this worn kitchen countertop with waterproof marble contact paper and protective edge sealant."></label></p>
  <p><button type="submit">Analyze photo -&gt; BOM -&gt; sourced cart</button></p>
</form>
<p>This step calls Gemini Vision and Channel3 live, so it can take a few seconds.</p>
</body></html>"""


@app.route("/analyze", methods=["POST"])
def analyze():
    budget = float(request.form.get("budget") or 150)
    goal = request.form.get("goal") or ""
    photo = request.files.get("photo")
    if photo and photo.filename:
        image_path = os.path.join(UPLOAD_DIR, photo.filename)
        photo.save(image_path)
    else:
        image_path = create_sample_test_image()

    bom = extract_bom_from_image(image_path, total_budget=budget, user_goal=goal)

    sourced_cart = []
    for item in bom.items:
        try:
            match = search_channel3_product(item.search_query, item.max_unit_price, relevance_text=item.item_name)
        except NoValidOfferError as exc:
            # Readable failure, not a 500. Show every candidate and
            # why it was rejected, so the user can see it was a
            # naming/relevance failure, not a fake product or a crash.
            rows = "".join(
                f"<tr><td>{e.get('title', '')} [{e.get('merchant', '')}]</td>"
                f"<td>${e.get('unit_price') if e.get('unit_price') is not None else ''}</td>"
                f"<td>{e.get('relevance_score', '')}: {', '.join(e.get('matched_words') or [])}</td>"
                f"<td>{e.get('rejected_because') or 'valid, but lost'}</td></tr>"
                for e in exc.evaluated
            )
            return (f"<!doctype html><html><head><title>SpecToCart - no valid offer</title>{PAGE_CSS}</head><body>"
                    f"<h1>No valid offer for: {item.item_name}</h1>"
                    f"<p>Search query: {exc.query} &nbsp; Max unit price: ${item.max_unit_price:.2f}</p>"
                    f"<p>{exc}</p>"
                    f"<table><tr><th>Candidate</th><th>Price</th><th>Relevance</th><th>Rejection reason</th></tr>{rows}</table>"
                    f"<p>No PayPal order was created. <a href='/'>Try again with a different photo, goal, or budget</a></p>"
                    f"</body></html>", 400)
        except LookupError as exc:
            return (f"Channel3 returned no products for '{item.item_name}': {exc}. "
                    f"No PayPal order was created. <a href='/'>Try again</a>", 400)
        line_total = round(match["unit_price"] * item.quantity, 2)
        sourced_cart.append({
            "bom_item": item.item_name,
            "priority": item.priority,
            "quantity": item.quantity,
            "target_unit_budget": item.max_unit_price,
            "sourced_title": match["title"],
            "unit_price": match["unit_price"],
            "line_total": line_total,
            "merchant": match["merchant"],
            "buy_url": match["buy_url"],
            "ai_reasoning": item.ai_reasoning,
            "relevance_score": match["relevance_score"],
            "matched_words": match["matched_words"],
            "win_reason": match.get("win_reason", ""),
            # evaluated candidates for this item (rejections only
            # are shown on /cart). raw_products are deliberately
            # NOT saved here - they would bloat last_cart.json.
            "evaluated": match.get("evaluated", []),
        })

    # Patch 8 budget fallback, instead of a bare 400 when over budget.
    # Optional items are dropped in policy order; Essential items
    # are never dropped. Only Essential-only over budget stops here.
    sourced_cart, fallback_drops, grand_total, ok = apply_budget_fallback(sourced_cart, budget)
    if not ok:
        return (f"STOPPING: even after dropping all Optional items, the Essential-only total "
                f"${grand_total:.2f} is not under the ${budget:.2f} budget. "
                f"No PayPal order was created. <a href='/'>Try again</a>", 400)

    cart = {
        "project_title": bom.project_title,
        "budget": budget,
        "grand_total": grand_total,
        "items": sourced_cart,
        "fallback_applied": bool(fallback_drops),
        "fallback_policy": FALLBACK_POLICY,
        "fallback_drops": fallback_drops,
    }
    _save_json(CART_FILE, cart)
    return redirect(url_for("cart"))


@app.route("/cart")
def cart():
    cart = _load_json(CART_FILE, None)
    if not cart:
        return redirect(url_for("home"))
    client_id = require_env("PAYPAL_CLIENT_ID")
    rows = ""
    for row in cart["items"]:
        buy = f"<br><a href='{row['buy_url']}'>Buy link</a>" if row["buy_url"] else "<br>(no buy link returned)"
        rejected = "".join(
            f"<br><small>Rejected: {e.get('title', '')} [{e.get('merchant', '')}] "
            f"${e.get('unit_price') if e.get('unit_price') is not None else ''} - "
            f"{e.get('rejected_because')}</small>"
            for e in (row.get("evaluated") or [])
            if e.get("rejected_because")
        )
        rows += (
            f"<tr><td>{row['bom_item']} (x{row['quantity']}, {row['priority']})</td>"
            f"<td>{row['sourced_title']} [{row['merchant']}] {buy}{rejected}</td>"
            f"<td>${row['unit_price']:.2f}</td><td>${row['line_total']:.2f}</td>"
            f"<td>{row['relevance_score']}: {', '.join(row['matched_words'])}"
            f"<br><small>won by {row.get('win_reason', '')}</small></td></tr>"
        )
    fallback_html = ""
    if cart.get("fallback_applied"):
        drops = "".join(
            f"<li>{d.get('bom_item')} - ${d.get('line_total'):.2f} - {d.get('reason', '')}</li>"
            for d in cart.get("fallback_drops") or []
        )
        fallback_html = (f"<p class='warn'><b>Budget fallback applied</b> ({cart.get('fallback_policy', '')}): "
                         f"the following Optional items were dropped to get under budget:</p><ul>{drops}</ul>")
    vault = _load_json(VAULT_FILE, None)
    if vault and vault.get("payment_token_id"):
        vault_html = """<h2>Pay with vaulted PayPal (returning DIYer, no re-approval)</h2>
<p>This uses the payment method you saved on <a href='/vault'>/vault</a>, under the future-payment consent you approved there. No browser approval is needed for this second project.</p>
<form action="/api/vault/charge" method="post"><button type="submit">Charge vaulted PayPal for this cart</button></form>
<p><small>Vaulted method: Yes (token id is stored server-side in vault_tokens.json only, never shown here and never committed).</small></p>"""
    else:
        vault_html = """<h2>Returning DIYer?</h2>
<p>No vaulted payment method saved yet. <a href='/vault'>Save your PayPal on /vault</a> first, then come back here for a second project charged without re-approval.</p>"""
    return f"""<!doctype html><html><head><title>SpecToCart cart</title>{PAGE_CSS}</head><body>
<nav><a href="/">New project</a> <a href="/orders">Orders dashboard</a> <a href="/vault">Vault</a></nav>
<h1>{cart['project_title']}</h1>
<p>Budget: ${cart['budget']:.2f} &nbsp; Sourced total: <b>${cart['grand_total']:.2f}</b></p>
{fallback_html}
<table><tr><th>BOM item</th><th>Sourced product</th><th>Unit</th><th>Line total</th><th>Relevance</th></tr>{rows}</table>
<h2>Pay with PayPal (Sandbox)</h2>
<p>Click the PayPal button. Your server creates the order, PayPal approves it, your server captures it.</p>
{vault_html}
<div id="paypal-button-container"></div>
<p id="result"></p>
<script src="https://www.paypal.com/sdk/js?client-id={client_id}&currency=USD"></script>
<script>
paypal.Buttons({{
  createOrder: function() {{
    return fetch('/api/create-order', {{method: 'POST'}})
      .then(r => r.json()).then(d => {{
        if (d.error) throw new Error(d.error);
        return d.order_id;
      }});
  }},
  onApprove: function(data) {{
    return fetch('/api/capture-order', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{order_id: data.orderID}})
    }}).then(r => r.json()).then(d => {{
      document.getElementById('result').innerHTML =
        '<span class="ok"><b>Capture: ' + d.status + '</b></span> Capture id: ' + (d.capture_id || '')
        + ' Amount: $' + (d.amount || '') + ' Fee: $' + (d.fee || '') + ' Net: $' + (d.net || '');
    }});
  }},
  onError: function(err) {{
    document.getElementById('result').textContent = 'PayPal error: ' + err;
  }}
}}).render('#paypal-button-container');
</script>
</body></html>"""


@app.route("/orders")
def orders_page():
    orders = _load_json(ORDERS_FILE, [])
    rows = ""
    for order in orders:
        refund_btn = ""
        if order.get("capture_id"):
            refund_btn = (
                f"<form action='/api/refund' method='post' style='display:inline'>"
                f"<input type='hidden' name='capture_id' value='{order['capture_id']}'>"
                f"<button type='submit'>Refund (sandbox demo)</button></form>"
            )
        rows += (
            f"<tr><td>{order.get('order_id')}</td><td>{order.get('project_title', '')}</td><td>{'vaulted' if order.get('vaulted_method') else 'fresh approval'}</td>"
            f"<td>${order.get('total_usd', '')}</td><td>{order.get('status')}</td>"
            f"<td>{order.get('capture_id', '')}</td><td>${order.get('fee', '')}</td>"
            f"<td>${order.get('net', '')}</td><td>{refund_btn}</td></tr>"
        )
    return f"""<!doctype html><html><head><title>SpecToCart orders</title>{PAGE_CSS}</head><body>
<nav><a href="/">New project</a> <a href="/orders">Orders dashboard</a> <a href="/vault">Vault (returning DIYer)</a> <a href="/webhook-events">Webhook events</a></nav>
<h1>Orders dashboard</h1>
<table><tr><th>Order</th><th>Project</th><th>Method</th><th>Total</th><th>Status</th><th>Capture</th><th>Fee</th><th>Net</th><th>Refund</th></tr>{rows or '<tr><td colspan="8">No orders yet.</td></tr>'}</table>
<p>Refund is a sandbox demo only. Do not refund a capture you want to keep as proof - create a fresh order for a refund demo.</p>
</body></html>"""


@app.route("/webhook-events")
def webhook_events_page():
    events = _load_json(WEBHOOK_FILE, [])
    rows = "".join(
        f"<tr><td>{e.get('received_at')}</td><td>{e.get('event_type')}</td>"
        f"<td>{e.get('event_id')}</td><td>{e.get('verified')}</td></tr>"
        for e in events
    )
    return f"""<!doctype html><html><head><title>Webhook events</title>{PAGE_CSS}</head><body>
<nav><a href="/">New project</a> <a href="/orders">Orders dashboard</a></nav>
<h1>PayPal webhook events received</h1>
<table><tr><th>Received</th><th>Event type</th><th>Event id</th><th>Signature verified</th></tr>{rows or '<tr><td colspan="4">No webhook events yet. PayPal can only deliver these to a public HTTPS URL.</td></tr>'}</table>
</body></html>"""


# ---------------------------------------------------------------------
# API - money decisions happen here, on the server
# ---------------------------------------------------------------------
@app.route("/api/create-order", methods=["POST"])
def api_create_order():
    cart = _load_json(CART_FILE, None)
    if not cart:
        return jsonify({"error": "No cart. Run /analyze first."}), 400
    order = create_paypal_sandbox_order(cart["items"], cart["project_title"])
    _save_order({
        "order_id": order["order_id"],
        "project_title": cart["project_title"],
        "total_usd": order["total_usd"],
        "status": order["status"],
        "capture_id": "",
        "fee": "",
        "net": "",
        "request_id": order.get("request_id", ""),
    })
    return jsonify({"order_id": order["order_id"], "status": order["status"]})


@app.route("/api/capture-order", methods=["POST"])
def api_capture_order():
    order_id = (request.get_json() or {}).get("order_id")
    if not order_id:
        return jsonify({"error": "order_id required"}), 400
    result = capture_paypal_order(order_id)
    capture = {}
    try:
        capture = result["purchase_units"][0]["payments"]["captures"][0]
        breakdown = capture.get("seller_receivable_breakdown", {})
        amount = capture["amount"]["value"]
        fee = breakdown.get("paypal_fee", {}).get("value", "")
        net = breakdown.get("net_amount", {}).get("value", "")
    except (KeyError, IndexError, TypeError):
        amount = fee = net = ""
    _update_order(order_id, status=result.get("status", "COMPLETED"),
                  capture_id=capture.get("id", ""), fee=fee, net=net)
    return jsonify({"status": result.get("status"), "capture_id": capture.get("id", ""),
                    "amount": amount, "fee": fee, "net": net})


@app.route("/api/refund", methods=["POST"])
def api_refund():
    capture_id = request.form.get("capture_id")
    if not capture_id:
        return jsonify({"error": "capture_id required"}), 400
    result = refund_paypal_capture(capture_id)
    return f"Refund status: {result.get('status')} <a href='/orders'>Back to dashboard</a><pre>{json.dumps(result, indent=2)}</pre>"


@app.route("/api/order-status/<order_id>")
def api_order_status(order_id):
    result = get_paypal_order_status(order_id)
    return jsonify({"order_id": order_id, "status": result.get("status")})



# ---------------------------------------------------------------------
# Vault - returning DIYer, Step 2 after the Gate.
# Flow, proven in the throwaway test 2026-10-28 (setup 201, payment
# token 201, no charge), now integrated here:
#   /vault -> POST /api/vault/setup -> PayPal approve link
#   -> GET /vault/return?token_id=... -> POST /v3/vault/payment-tokens
#   -> vault_tokens.json (server-side only, NEVER committed, NEVER
#      printed in full in a page or in chat)
#   -> second project: POST /api/vault/charge -> order with
#      payment_source.paypal.attributes.vault_id -> capture at once,
#      no browser approval, order record has vaulted_method=True.
# Consent: the /vault page states, before setup, that saving means
# SpecToCart may charge the saved PayPal for a later project without
# re-approval, for that project only. If that consent is not shown
# and approved, do not claim "without re-approval" in README/video.
# If the vaulted charge fails, the normal PayPal button on /cart
# is still there - Vault is an extra path, not a replacement.
# ---------------------------------------------------------------------
import uuid as _vault_uuid

VAULT_MERCHANT_CUSTOMER_ID = "spectocart_demo_user"


def _vault_setup_token():
    token = get_paypal_access_token()
    res = requests.post(
        "https://api-m.sandbox.paypal.com/v3/vault/setup-tokens",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}",
                 "PayPal-Request-Id": str(_vault_uuid.uuid4())},
        json={"customer": {"merchant_customer_id": VAULT_MERCHANT_CUSTOMER_ID},
              "payment_source": {"paypal": {
                  "usage_type": "MERCHANT",
                  "experience_context": {
                      "return_url": "http://127.0.0.1:5000/vault/return",
                      "cancel_url": "http://127.0.0.1:5000/vault/cancel",
                  }}}},
        timeout=15,
    )
    res.raise_for_status()
    return res.json()


def _vault_payment_token(setup_id):
    token = get_paypal_access_token()
    res = requests.post(
        "https://api-m.sandbox.paypal.com/v3/vault/payment-tokens",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}",
                 "PayPal-Request-Id": str(_vault_uuid.uuid4())},
        json={"payment_source": {"token": {"id": setup_id, "type": "SETUP_TOKEN"}}},
        timeout=15,
    )
    res.raise_for_status()
    return res.json()


def _vault_create_and_capture(cart, payment_token_id):
    token = get_paypal_access_token()
    total = f"{float(cart['grand_total']):.2f}"
    purchase_unit = {"description": f"SpecToCart BOM (vaulted): {cart['project_title']}"[:120],
                     "amount": {"currency_code": "USD", "value": total,
                                "breakdown": {"item_total": {"currency_code": "USD", "value": total}}}}
    merchant_id = (os.getenv("PAYPAL_MERCHANT_ID") or "").strip()
    if merchant_id:
        purchase_unit["payee"] = {"merchant_id": merchant_id}
    # Items, like the normal order, so amount = sum(items) is checkable.
    paypal_items = []
    total_cents = 0
    for item in cart["items"]:
        unit_cents = int(round(float(item["unit_price"]) * 100))
        qty = int(item["quantity"])
        total_cents += unit_cents * qty
        paypal_items.append({"name": item["sourced_title"][:120], "quantity": str(qty),
                             "description": f"Vendor: {item['merchant']} | {item['priority']}"[:120],
                             "unit_amount": {"currency_code": "USD", "value": f"{unit_cents / 100:.2f}"}})
    purchase_unit["items"] = paypal_items
    order_payload = {
        "intent": "CAPTURE",
        "purchase_units": [purchase_unit],
        "payment_source": {"paypal": {
            # vault_id MUST be direct under paypal when stored_credential
            # is present. v2 put it under attributes.vault_id and PayPal
            # rejected creation with 422
            # PAYPAL_STORED_CREDENTIAL_MISSING_REQUIRED_PARAMETER,
            # field payment_source/paypal/vault_id, debug f24275192f962.
            "vault_id": payment_token_id,
            "attributes": {
                "customer": {"merchant_customer_id": VAULT_MERCHANT_CUSTOMER_ID}},
            # Merchant-initiated, subsequent, unscheduled (a second
            # project, not a recurring schedule). Without this,
            # PayPal returns PAYER_ACTION_REQUIRED and capture 422s,
            # as seen with order 0EP36392FF822790Y.
            "stored_credential": {
                "payment_initiator": "MERCHANT",
                "payment_type": "UNSCHEDULED",
                "usage": "SUBSEQUENT"}}},
    }
    res = requests.post(
        "https://api-m.sandbox.paypal.com/v2/checkout/orders",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}",
                 "PayPal-Request-Id": str(_vault_uuid.uuid4())},
        json=order_payload, timeout=15,
    )
    if res.status_code >= 400:
        return {"order": {}, "captured": None, "error": f"Vaulted order failed {res.status_code}: {res.text[:1000]}"}
    order = res.json()
    order_id = order.get("id", "")
    order_status = order.get("status", "")
    if order_status == "PAYER_ACTION_REQUIRED":
        # PayPal did NOT treat the vault_id as buyer-not-present
        # approval. Capturing now would 422. Return the order so
        # the page can show this honestly, instead of a 500.
        approve_link = ""
        for link in order.get("links", []):
            if link.get("rel") in ("approve", "payer-action"):
                approve_link = link.get("href")
                break
        return {"order": order, "captured": None,
                "error": f"Vaulted order {order_id} is PAYER_ACTION_REQUIRED, not approved. "
                         f"PayPal did not accept this as a merchant-initiated charge. "
                         f"Approve link (if you want to test the token with approval): {approve_link}"}
    if order_status == "COMPLETED":
        # Vaulted intent=CAPTURE orders can complete on creation.
        # Capturing again gives ORDER_ALREADY_CAPTURED (seen with
        # order 9DAS56555C6203348, debug f771534d1574e). Use the
        # capture already in the order response, do NOT call capture.
        return {"order": order, "captured": order}
    try:
        captured = capture_paypal_order(order_id)
    except Exception as exc:
        # ORDER_ALREADY_CAPTURED here means the same thing: the
        # order completed on creation, so treat the order itself
        # as the captured result instead of failing.
        if "ORDER_ALREADY_CAPTURED" in str(exc):
            return {"order": order, "captured": order}
        return {"order": order, "captured": None, "error": f"Vaulted capture failed: {exc}"}
    return {"order": order, "captured": captured}


@app.route("/vault")
def vault_page():
    vault = _load_json(VAULT_FILE, None)
    saved = "Yes - a payment token is stored server-side (id not shown here)." if vault and vault.get("payment_token_id") else "No - no vaulted method saved yet."
    return f"""<!doctype html><html><head><title>SpecToCart Vault</title>{PAGE_CSS}</head><body>
<nav><a href="/">New project</a> <a href="/orders">Orders dashboard</a></nav>
<h1>Vault - returning DIYer</h1>
<p><b>Story:</b> First project, you approve manually with the PayPal button. Second project, SpecToCart charges the method you saved here, without you re-approving in a browser.</p>
<p><b>Consent, read before you save:</b> By saving your PayPal here and approving on PayPal, you consent to SpecToCart charging that saved PayPal for a later project you start here, without a fresh browser approval for that project. This is a sandbox demo only, and you can delete the saved method on PayPal at any time.</p>
<p>Vaulted method saved: <b>{saved}</b></p>
<form action="/api/vault/setup" method="post"><button type="submit">Save my PayPal for my next project</button></form>
<p><small>Setup token -> payment token, no money charged by saving alone. The token id is stored in vault_tokens.json on the server only, never committed to git, never printed in full here.</small></p>
</body></html>"""


@app.route("/api/vault/setup", methods=["POST"])
def api_vault_setup():
    try:
        data = _vault_setup_token()
    except Exception as exc:
        return f"Vault setup failed: {exc} <a href='/vault'>Back</a>", 400
    setup_id = data.get("id", "")
    approve_link = ""
    for link in data.get("links", []):
        if link.get("rel") in ("approve", "payer-action"):
            approve_link = link.get("href")
            break
    if not setup_id or not approve_link:
        return f"Vault setup returned no approve link: {data} <a href='/vault'>Back</a>", 400
    _save_json(VAULT_PENDING_FILE, {"setup_id": setup_id, "status": data.get("status", "")})
    return redirect(approve_link)


@app.route("/vault/return")
def vault_return():
    setup_id = request.args.get("approval_token_id") or request.args.get("token_id") or ""
    if not setup_id:
        pending = _load_json(VAULT_PENDING_FILE, {})
        setup_id = pending.get("setup_id", "")
    if not setup_id:
        return "No setup token found. Start again on <a href='/vault'>/vault</a>.", 400
    try:
        data = _vault_payment_token(setup_id)
    except Exception as exc:
        return f"Payment token conversion failed: {exc} <a href='/vault'>Back</a>", 400
    payment_token_id = data.get("id", "")
    if not payment_token_id:
        return f"Payment token conversion returned no id: {data} <a href='/vault'>Back</a>", 400
    _save_json(VAULT_FILE, {"payment_token_id": payment_token_id,
                            "customer_id_present": bool(data.get("customer")),
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "vaulted_method": True})
    return f"""<!doctype html><html><head><title>Vault saved</title>{PAGE_CSS}</head><body>
<h1>Vaulted method saved</h1>
<p>Payment token returned: <b>Yes</b> (id stored server-side only, not shown here). Customer id present: <b>{'Yes' if data.get('customer') else 'No'}</b></p>
<p>Next: run a second project on <a href='/'>/</a>, then on /cart use "Charge vaulted PayPal for this cart". No browser approval should be needed for that charge.</p>
<p><a href='/orders'>Orders dashboard</a></p>
</body></html>"""


@app.route("/vault/cancel")
def vault_cancel():
    return "Vault setup cancelled. No payment method was saved. <a href='/vault'>Back to /vault</a>"


@app.route("/api/vault/charge", methods=["POST"])
def api_vault_charge():
    cart = _load_json(CART_FILE, None)
    vault = _load_json(VAULT_FILE, None)
    if not cart:
        return "No cart. Run /analyze first.", 400
    if not vault or not vault.get("payment_token_id"):
        return "No vaulted method saved. Save one on <a href='/vault'>/vault</a> first.", 400
    result = _vault_create_and_capture(cart, vault["payment_token_id"])
    if "error" in result and not result.get("captured"):
        # Save the attempt so /orders shows it, then show the error.
        _save_order({"order_id": (result.get("order") or {}).get("id", ""), "project_title": cart["project_title"],
                     "total_usd": cart["grand_total"], "status": (result.get("order") or {}).get("status", ""),
                     "capture_id": "", "fee": "", "net": "", "vaulted_method": True})
        return f"Vaulted charge failed: {result['error']} <br>Try the normal PayPal button on <a href='/cart'>/cart</a> instead.", 400
    captured = result["captured"]
    order_id = result["order"].get("id", "")
    capture = {}
    try:
        capture = captured["purchase_units"][0]["payments"]["captures"][0]
        breakdown = capture.get("seller_receivable_breakdown", {})
        fee = breakdown.get("paypal_fee", {}).get("value", "")
        net = breakdown.get("net_amount", {}).get("value", "")
    except (KeyError, IndexError, TypeError):
        fee = net = ""
    _save_order({"order_id": order_id, "project_title": cart["project_title"],
                 "total_usd": cart["grand_total"], "status": captured.get("status", "COMPLETED"),
                 "capture_id": capture.get("id", ""), "fee": fee, "net": net,
                 "vaulted_method": True,
                 "request_id": result["order"].get("id", "")})
    return f"""<!doctype html><html><head><title>Vaulted charge</title>{PAGE_CSS}</head><body>
<h1>Vaulted charge: {captured.get('status', '')}</h1>
<p>Order: {order_id} &nbsp; Capture: {capture.get('id', '')} &nbsp; Total: ${cart['grand_total']:.2f} &nbsp; Fee: ${fee} &nbsp; Net: ${net}</p>
<p>Method: <b>vaulted, not fresh approval</b> - this order appears in <a href='/orders'>/orders</a> with Method = vaulted.</p>
</body></html>"""


# ---------------------------------------------------------------------
# Webhook listener - Step 1 version: log + verify if possible + update
# dashboard. Full public-URL setup is Step 3, before the video.
# ---------------------------------------------------------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    """Webhook listener v2 (2026-10-30) - verification gate + dedupe.

    Works-live rules, in order:
    1) If PAYPAL_WEBHOOK_ID is set, the event MUST verify via
       POST /v1/notifications/verify-webhook-signature with
       verification_status == "SUCCESS" before ANY order is touched.
       Unverified / failed / error events are logged in
       webhook_events.json with verified=<status> and return 200
       (so PayPal does not retry forever), but do NOT update orders.
       A listener that trusts the payload without verification is
       worse than no listener.
    2) Dedupe by event id: if this event id is already in
       webhook_events.json, log it as "duplicate (ignored)" and
       return 200 without updating the order a second time.
    3) Only then, for PAYMENT.CAPTURE.COMPLETED / REFUNDED, update
       the matching order in orders.json. For REFUNDED, set BOTH
       status and capture_status to REFUNDED, so the dashboard
       and any receipt reader see the same state.
    """
    event = request.get_json(silent=True) or {}
    event_type = event.get("event_type", "unknown")
    event_id = event.get("id", "")

    events = _load_json(WEBHOOK_FILE, [])
    if event_id and any(e.get("event_id") == event_id for e in events):
        events.insert(0, {
            "received_at": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "event_id": event_id,
            "verified": "duplicate (ignored)",
        })
        _save_json(WEBHOOK_FILE, events)
        return "OK", 200

    verified = "not attempted (no PAYPAL_WEBHOOK_ID in .env)"
    webhook_id = os.getenv("PAYPAL_WEBHOOK_ID", "").strip()
    if webhook_id:
        try:
            token = get_paypal_access_token()
            verify_res = requests.post(
                "https://api-m.sandbox.paypal.com/v1/notifications/verify-webhook-signature",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
                json={
                    "auth_algo": request.headers.get("PAYPAL-AUTH-ALGO", ""),
                    "cert_url": request.headers.get("PAYPAL-CERT-URL", ""),
                    "transmission_id": request.headers.get("PAYPAL-TRANSMISSION-ID", ""),
                    "transmission_sig": request.headers.get("PAYPAL-TRANSMISSION-SIG", ""),
                    "transmission_time": request.headers.get("PAYPAL-TRANSMISSION-TIME", ""),
                    "webhook_id": webhook_id,
                    "webhook_event": event,
                },
                timeout=10,
            )
            try:
                verify_body = verify_res.json()
            except Exception:
                verify_body = {}
            verified = str(verify_body.get("verification_status", verify_res.status_code))
            # Diagnostic only (2026-10-30): PayPal returns 400 with an
            # error body, not a verification_status, so the old code
            # hid the real reason behind "400". Keep the body (first
            # 500 chars, no secrets) in the event log and in the
            # Flask log, so the next delivery tells us WHY it failed.
            verify_detail = str(verify_body)[:500] if verified != "SUCCESS" else ""
            print(f"Webhook verify: {event_type} {event_id} -> {verified} detail={verify_detail}")
        except Exception as exc:  # noqa: BLE001 - demo logging only
            verified = f"verification error: {exc}"
        if verified != "SUCCESS":
            events.insert(0, {
                "received_at": datetime.now(timezone.utc).isoformat(),
                "event_type": event_type,
                "event_id": event_id,
                "verified": verified,
                "verify_detail": verify_detail if verified != "SUCCESS" else "",
            })
            _save_json(WEBHOOK_FILE, events)
            return "OK", 200

    resource = event.get("resource", {}) if isinstance(event.get("resource"), dict) else {}
    related = resource.get("supplementary_data", {}).get("related_ids", {})
    if event_type == "PAYMENT.CAPTURE.COMPLETED":
        if related.get("order_id"):
            _update_order(related["order_id"], status="COMPLETED (webhook)",
                          capture_id=resource.get("id", ""))
    elif event_type == "PAYMENT.CAPTURE.REFUNDED":
        # FIXED 2026-10-30: a REFUNDED event's resource is a refund
        # object, not a capture object, so it does NOT carry
        # supplementary_data.related_ids.order_id the way a
        # COMPLETED capture does. That is why the verified REFUNDED
        # event above was logged SUCCESS but updated nothing - the
        # old code silently skipped it when related order_id was
        # missing. Fall back to matching by capture id: first
        # resource.capture_id, then the "up" link to
        # /v2/payments/captures/<CAPTURE_ID>, then find the order
        # in orders.json with that capture_id.
        refund_order_id = related.get("order_id")
        if not refund_order_id:
            cap_id = resource.get("capture_id", "")
            if not cap_id:
                for link in resource.get("links", []) or []:
                    href = link.get("href", "") if isinstance(link, dict) else ""
                    if "/captures/" in href:
                        cap_id = href.rsplit("/captures/", 1)[-1].split("/")[0].split("?")[0]
                        break
            if cap_id:
                for _o in _load_json(ORDERS_FILE, []):
                    if _o.get("capture_id") == cap_id:
                        refund_order_id = _o.get("order_id")
                        break
                print(f"Webhook REFUNDED: matched capture {cap_id} to order {refund_order_id}")
            else:
                print(f"Webhook REFUNDED: no order_id and no capture_id in resource, cannot update order")
        if refund_order_id:
            _update_order(refund_order_id, status="REFUNDED (webhook)",
                          capture_status="REFUNDED")

    events.insert(0, {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "event_id": event_id,
        "verified": verified,
    })
    _save_json(WEBHOOK_FILE, events)
    return "OK", 200


if __name__ == "__main__":
    # Debug is off by default in Flask when run this way in production
    # mode; for a local demo, use_reloader=False to avoid double runs.
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
