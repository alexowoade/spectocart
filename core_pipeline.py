"""
SpecToCart - Project-specific procurement agent, PATCH 8.

New in this patch (2026-10-27):
  PATCH 8 (a): Relevance scoring now requires BOTH
      (i) a minimum number of matched words scaled to item length,
      (ii) at least one head-noun match (last 1-2 meaningful words),
      (iii) explicit rejection reasons: score_zero,
            insufficient_relevance, head_noun_missing, price_unreadable,
            currency_mismatch, price_over_cap.
      This fixes the two demo failures: "Silicone for Body (HU-100)"
      for "Clear Kitchen & Bath Silicone Sealant", and a marble wall
      sticker for "Waterproof Marble Vinyl Wrap". Both were winning
      on a single category word before.
  PATCH 8 (b): select_best_offer() raises NoValidOfferError, a
      LookupError subclass that carries .evaluated and .query, so
      "no valid offer" and "inspect rejections" are not mutually
      exclusive. Callers that just want LookupError get it.
  PATCH 8 (c): Project math. Roll quantity is DERIVED from
      user-supplied dimensions (--length / --width inches), the
      per-roll coverage, and a 10% waste allowance, in Python.
      The derivation string is recorded in the receipt, e.g.
      "ceil(16.67 * 1.10 / 15.00) = 2". If dimensions are not
      supplied, we do NOT invent an area - we fall back to the
      BOM quantity and record assumptions=["dimensions_missing"].
  PATCH 8 (d): Step plan. A second Gemini call generates ordered
      steps naming which BOM item is used in each, with time
      estimates. Falls back to a deterministic template if Gemini
      fails. Recorded in the receipt as "steps".
  PATCH 8 (e): Critic pass (--critic). A third Gemini call per
      sourced item asks "is this plausibly the right category?".
      Verdicts are recorded in the receipt. --strict-critic drops
      Optional items that fail and aborts on Essential failures.
      Without --strict-critic, verdicts are logged but not acted on,
      so a demo cannot be killed by a low-temperature LLM.
  PATCH 8 (f): Receipt hardening. last_receipt.json is only
      overwritten by a receipt of the same demo_mode, so a demo
      run cannot overwrite a live receipt or vice versa. Raw
      Channel3 responses are hashed if >50KB so receipts stay
      readable. DEBUG_CHANNEL3=1 prints the raw first-product JSON
      so the unverified schema can be eyeballed.
  PATCH 8 (g): Key handling. _require_all_keys() is called at the
      top of every mode, so a missing key fails before any API
      call. PAYPAL_RETURN_URL / PAYPAL_CANCEL_URL are honoured.
  PATCH 8 (h) - FIXED 2026-10-27 after review: _stem no longer
      strips "es" generically (tubes -> tube, not tub; cakes ->
      cake, not cak); string "nan"/"inf" prices are rejected;
      project math is now recorded in the receipt even when
      dimensions are missing (assumptions=["dimensions_missing"]),
      not only when dimensions are supplied.
  PATCH 8 (i) - FIXED 2026-10-27 after the first live Patch 8 run:
      _relevance_score now uses the documented _TOKEN_SYNONYMS
      above. The live run rejected every wrap candidate because
      the BOM said "contact paper" and retail titles say "shelf
      liner" / "laminate" / "film" - including the $16.78 Con-Tact
      product that won under Patch 7, which failed only on
      head_noun_missing. With synonyms, that product scores 5
      (marble, vinyl, roll, plus contact and paper via liner)
      and wins again, while the marble wall sticker still fails
      (sticker is not a synonym for wrap).
  PATCH 8 (j) - FIXED 2026-10-27 after the second live Patch 8
      run: _required_matches is now flat (1 for a single-token
      item, 2 otherwise). The scaled rule required 3 matches
      for "Glossy Marble Peel and Stick Countertop Vinyl" and
      rejected the correct Con-Tact product, which shares only
      marble + vinyl. Two core matches plus a head noun is the
      right bar; scaling added false rejections without adding
      protection, because score-1 products are already rejected
      and 2-match wall stickers are already rejected by head.
  PATCH 8 (k) - FIXED 2026-10-27 after the third live Patch 8
      run crashed in the critic pass: CriticVerdict is a
      Pydantic BaseModel, not a dataclass, so dataclasses.asdict()
      raised TypeError on the first verdict. The receipt now
      uses v.model_dump(). The other asdict() calls are correct:
      CandidateEvaluation and ProjectMath are dataclasses.
  PATCH 8 (l) - ADDED 2026-10-28 at the user's request, after
      Gemini returned 503 UNAVAILABLE for the BOM call and for
      a critic call on successive runs: _generate_with_retry()
      retries ONLY transient Gemini errors (503 / UNAVAILABLE /
      ServerError) up to 3 attempts, waiting 30s then 60s
      between attempts, for the BOM, step-plan, and critic
      calls. Non-transient errors (400, 401, bad schema, bad
      key) are raised immediately, not retried. After all
      retries fail, the original behaviour returns: the BOM
      call still raises, the step plan still falls back to
      its template, and the critic still logs "skipped" and
      keeps the item. A retry does not fake a verdict or a
      BOM - it only gives a temporary Google spike time to
      clear.
  PATCH 8 (m) - ADDED 2026-10-28, corrected from a user-pasted
      fallback proposal: _generate_with_fallback_and_retry()
      tries models in-family, in order, ONLY on transient
      errors. The chain is [GEMINI_MODEL or gemini-3.8-flash,
      then the other of gemini-3.8-flash / gemini-3.6-flash].
      Corrections to the proposal, with reasons:
      - gemini-3.5-flash is NOT in the chain. Google deprecated
        it on 2026-10-08 and now routes it automatically to
        gemini-3.6-flash, so adding it would just call 3.6
        twice under a misleading name, and share 3.6's quota.
      - The proposal caught ALL exceptions and tried the next
        model. This version only falls back on transient
        errors (503 / 429 overload, via _is_transient_gemini_error
        plus 429). A 400, 401, or bad schema raises immediately
        and is not masked by a fallback.
      - The proposal's "3.6 at 4/4, 1.5s median, 3.8 failing
        3/4" round-robin data is NOT verified from our runs.
        Our runs show 3.8 succeeding at 08:24 and 09:03 on
        2026-10-28, so 3.8 stays primary by default. Do NOT
        set GEMINI_MODEL=gemini-3.6-flash as primary until
        you have tested 3.6 yourself on Ubuntu with one
        live call (vision + structured output). The fallback
        already gives you 3.6 if 3.8 spikes, without betting
        the whole demo on unverified data.
      - A fallback success is printed in the terminal
        ("fell back to gemini-3.6-flash"), so the model that
        actually answered is visible in the run log, not
        hidden behind a silent routing swap.

Still unverified (unchanged): Channel3 endpoint/payload shape.
Run once with DEBUG_CHANNEL3=1 and eyeball the raw JSON before
trusting buy_url.
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Literal, TypedDict

import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

load_dotenv()


# =====================================================================
# ENV
# =====================================================================
def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing {name} in .env - stopping before any API call.")
    return value


def _require_all_keys(names: list[str]) -> None:
    missing = [n for n in names if not os.getenv(n)]
    if missing:
        raise SystemExit(f"Missing in .env: {', '.join(missing)} - stopping before any API call.")


def get_gemini_client():
    return genai.Client(api_key=require_env("GEMINI_API_KEY"))


# =====================================================================
# GEMINI RETRY (PATCH 8l)
# =====================================================================
def _is_transient_gemini_error(exc: Exception) -> bool:
    """True only for a temporary Google overload, not a real error.

    Real google.genai ServerError carries code/status_code 503.
    The string check is a fallback for wrapped errors (tenacity
    re-raises, SDK wrappers), so a wrapped 503 is still retried.
    400/401/404, bad keys, and bad schemas are NOT transient.
    """
    for attr in ("code", "status_code", "status"):
        value = getattr(exc, attr, None)
        if value in (503, "503", "UNAVAILABLE"):
            return True
    text = str(exc)
    return "503" in text or "UNAVAILABLE" in text or "high demand" in text


def _generate_with_retry(call, label: str, attempts: int = 3, delays: tuple = (30, 60)):
    """Call a Gemini generate_content callable, retrying only 503s.

    `call` is a zero-argument callable returning the response.
    On a transient error, prints the wait and sleeps, then tries
    again. On the last attempt, or on a non-transient error, the
    original exception is raised unchanged to the caller.
    Kept for tests / backward compatibility; the three live
    call sites now use _generate_with_fallback_and_retry below.
    """
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as exc:
            if not _is_transient_gemini_error(exc) or attempt == attempts:
                raise
            wait = delays[attempt - 1] if attempt - 1 < len(delays) else delays[-1]
            print(f"  Gemini {label}: transient overload ({exc}), retrying in {wait}s (attempt {attempt + 1}/{attempts})...")
            time.sleep(wait)


def _gemini_model_chain() -> list[str]:
    """Ordered in-family fallback chain, primary first.

    Primary is GEMINI_MODEL from .env, default gemini-3.8-flash.
    The fallback is the other current Flash model, gemini-3.6-flash.
    gemini-3.5-flash is deliberately excluded: it is deprecated
    and routes automatically to gemini-3.6-flash, so it would
    duplicate the fallback and share its quota pool.
    """
    primary = (os.getenv("GEMINI_MODEL") or "gemini-3.8-flash").strip()
    candidates = ["gemini-3.8-flash", "gemini-3.6-flash"]
    return [primary] + [m for m in candidates if m != primary]


def _is_transient_for_fallback(exc: Exception) -> bool:
    """Transient for fallback: 503 overload, or 429 overload.

    429 is included here (unlike the v5 retry, which was 503-only)
    because a per-model daily quota is a per-model limit: falling
    back to a different model gives a different per-model quota
    pool. A 429 that is a spend cap / billing error is still not
    distinguished here by code alone, so if both models return
    429, the chain still raises after the attempts, it does not
    loop forever.
    """
    if _is_transient_gemini_error(exc):
        return True
    for attr in ("code", "status_code", "status"):
        if getattr(exc, attr, None) in (429, "429", "RESOURCE_EXHAUSTED"):
            return True
    return "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)


def _generate_with_fallback_and_retry(make_call, label: str, attempts: int = 3, delays: tuple = (30, 60)):
    """Try each model in the chain, once, on transient errors only.

    `make_call` is a callable taking a model name and returning
    the response. On a transient error on the primary, the next
    model is tried immediately (no 30s wait — that is the point
    of a fallback: it is faster than retrying the same overloaded
    model). Only after the whole chain fails on transient errors
    does it wait 30s, then 60s, and retry the whole chain.
    Non-transient errors raise immediately, with no fallback.
    """
    chain = _gemini_model_chain()
    last_exc = None
    for attempt in range(1, attempts + 1):
        for model in chain:
            try:
                response = make_call(model)
                if model != chain[0]:
                    print(f"  Gemini {label}: fell back to {model} (primary {chain[0]} failed).")
                return response
            except Exception as exc:
                if not _is_transient_for_fallback(exc):
                    raise
                print(f"  Gemini {label}: {model} transient ({exc.__class__.__name__}); trying next model.")
                last_exc = exc
                continue
        if attempt < attempts:
            wait = delays[attempt - 1] if attempt - 1 < len(delays) else delays[-1]
            print(f"  Gemini {label}: all models transient, retrying chain in {wait}s (attempt {attempt + 1}/{attempts})...")
            time.sleep(wait)
    raise last_exc


# =====================================================================
# SCHEMAS
# =====================================================================
class BOMItem(BaseModel):
    item_name: str = Field(description="Name of the required tool, material, or equipment")
    search_query: str = Field(description="Specific retail search query for Channel3 (e.g., 'marble contact paper roll waterproof')")
    quantity: int = Field(description="Recommended quantity needed for the project", ge=1)
    max_unit_price: float = Field(description="Target maximum unit price in USD to stay within budget")
    priority: Literal["Essential", "Optional"] = Field(description="Must be either 'Essential' or 'Optional'")
    ai_reasoning: str = Field(description="Brief explanation of why this item is needed and how the unit budget was allocated")


class ProjectBOM(BaseModel):
    project_title: str = Field(description="Short title of the detected project")
    estimated_difficulty: Literal["Easy", "Medium", "Hard"] = Field(description="Easy, Medium, or Hard")
    items: list[BOMItem] = Field(min_length=3, max_length=5)


class StepItem(BaseModel):
    n: int = Field(ge=1, description="Step number, 1-based, in order")
    action: str = Field(description="Short imperative description of the step")
    item: str = Field(description="BOM item name used in this step, or 'none' if the step uses no purchased item")
    est_min: int = Field(ge=0, description="Estimated minutes for this step")


class StepPlan(BaseModel):
    steps: list[StepItem] = Field(min_length=2, max_length=8)


class CriticVerdict(BaseModel):
    plausible: bool = Field(description="True only if the sourced product is clearly the right category and use for the BOM item")
    reason: str = Field(description="One short sentence explaining the verdict")


# =====================================================================
# STAGE 1: GEMINI VISION -> BOM
# =====================================================================
def extract_bom_from_image(image_path: str, total_budget: float, user_goal: str) -> ProjectBOM:
    if not math.isfinite(total_budget) or total_budget <= 0:
        raise ValueError(f"total_budget must be a finite positive number (got {total_budget!r}).")
    print(f"\n[1/3] Analyzing image '{image_path}' with Gemini Vision (Target Budget: ${total_budget:.2f})...")
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image not found: {image_path}")

    client = get_gemini_client()
    prompt = f"""
    You are an autonomous procurement and DIY/studio engineering agent.
    Analyze this image along with the user's project goal: "{user_goal}".
    Break the project down into a practical Bill of Materials (BOM) of 3 to 5 purchasable items.
    Ensure the sum of (quantity * max_unit_price) across all items stays strictly under ${total_budget}.
    """
    with Image.open(image_path) as img:
        img.load()
        response = _generate_with_fallback_and_retry(
            lambda model: client.models.generate_content(
                model=model,
                contents=[img, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ProjectBOM,
                    temperature=0.2,
                ),
            ),
            label="Vision (BOM)",
        )

    if not response.text:
        raise ValueError("Gemini returned no text (the request may have been blocked). No BOM was created.")

    bom = ProjectBOM.model_validate_json(response.text)

    bad_prices = [
        item.item_name for item in bom.items
        if not math.isfinite(item.max_unit_price) or item.max_unit_price <= 0
    ]
    if bad_prices:
        raise ValueError(f"Gemini returned a non-finite or non-positive price for: {', '.join(bad_prices)}. Stopping here.")

    bom_total = sum(item.quantity * item.max_unit_price for item in bom.items)
    print(f"  BOM target total: ${bom_total:.2f} (budget: ${total_budget:.2f})")
    if bom_total >= total_budget:
        raise ValueError(
            f"BOM target total ${bom_total:.2f} is NOT under the ${total_budget:.2f} budget. "
            "Stopping here - fix the BOM instead of creating a PayPal order over budget."
        )
    return bom


# =====================================================================
# PRICE PARSING
# =====================================================================
def _price_from_value(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return None
        return float(value)
    if isinstance(value, str):
        try:
            parsed = float(value.replace("$", "").replace(",", "").strip())
        except ValueError:
            return None
        if not math.isfinite(parsed):
            return None
        return parsed
    if isinstance(value, dict):
        for key in ("price", "amount", "value"):
            if key in value:
                return _price_from_value(value[key])
    return None


# =====================================================================
# TOKENIZATION + RELEVANCE (PATCH 8a)
# =====================================================================
_STOPWORDS = {
    "a", "an", "the", "and", "or", "for", "with", "of", "to", "in",
    "on", "by", "at", "from", "per", "x", "self", "size",
}

# Container / unit words that never carry the meaning of an item,
# so they must never act as the "head noun" of a query.
_HEAD_NOUN_EXCEPTIONS = {
    "pack", "set", "kit", "roll", "tube", "piece", "pieces", "unit", "units",
    "box", "bag", "type", "kind", "style", "color", "colour",
    "ounce", "oz", "pound", "lb", "lbs", "inch", "inches", "ft",
    "foot", "feet", "gallon", "gal", "quart", "qt", "liter", "litre",
    "count", "ct", "ml", "kg", "cm", "mm", "pack", "pk",
}

# Words we deliberately do not strip the trailing "s" from.
_NO_STEM = {
    "glass", "class", "boss", "dress", "press", "cross", "grass", "moss",
    "loss", "plus", "bonus", "focus", "campus", "gas", "bus", "series",
    "species", "news", "chassis", "canvas", "atlas", "axis", "analysis",
    "basis", "crisis", "thesis", "diagnosis", "address", "access",
    "process", "success", "princess", "waitress", "actress", "guess",
    "mess", "less", "yes", "this", "his", "hers", "ours", "yours",
}


def _stem(word: str) -> str:
    if word in _NO_STEM or len(word) <= 3:
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"           # batteries -> battery
    if word.endswith("ches") or word.endswith("shes") or word.endswith("xes"):
        return word[:-2]                 # inches -> inch, boxes -> box
    # FIXED 2026-10-27: only strip "es" for s/x/z/ch/sh endings
    # (classes -> class, buses -> bus). For e-ending words, strip
    # only the final "s" below (cakes -> cake, tubes -> tube,
    # cables -> cable). The old generic "es" strip gave tub/cabl/cak.
    if word.endswith("es") and len(word) > 4:
        if word.endswith("sses"):
            return word[:-2]             # classes -> class
        # Otherwise fall through to the single-"s" strip below.
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]                 # liners -> liner
    return word


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {_stem(w) for w in words if w not in _STOPWORDS}


def _meaningful_tokens(text: str) -> list[str]:
    """Ordered tokens excluding stopwords and container/unit words."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    result = []
    for word in words:
        if word in _STOPWORDS:
            continue
        stemmed = _stem(word)
        if stemmed in _HEAD_NOUN_EXCEPTIONS:
            continue
        result.append(stemmed)
    return result


def _head_nouns(wanted: str) -> set[str]:
    tokens = _meaningful_tokens(wanted)
    return set(tokens[-2:]) if tokens else set()


def _required_matches(wanted_tokens: set[str]) -> int:
    # FIXED 2026-10-27 (Patch 8 v3): the old scaling rule
    # max(2, (n+1)//2) required 3 matches for a 6-token BOM name.
    # That rejected the correct $16.78 Con-Tact product for
    # "Glossy Marble Peel and Stick Countertop Vinyl", which
    # shares only the 2 core nouns marble + vinyl - the other
    # words (glossy, peel, stick, countertop) are marketing
    # adjectives retail titles do not repeat in that combination.
    # A flat minimum of 2 is enough: it still rejects a
    # single-category match (Silicone for Body, score 1), and
    # the head-noun rule still rejects a 2-match wall sticker
    # (waterproof + marble, no vinyl/countertop). Scaling was
    # doing the head rule's job badly, and killing good products.
    n = len(wanted_tokens)
    if n <= 1:
        return 1
    return 2


# Documented retail synonyms. Retail titles rarely say "contact
# paper" - they say "shelf liner", "adhesive laminate", or "film".
# A sealant is often sold as "caulk", and a degreaser is often
# written "degreasing" in a BOM and "degreaser" on a product.
# These are the ONLY synonym groups allowed, and they are listed
# here so a judge can audit them. "sticker" is deliberately NOT
# a synonym for "wrap" - that is what keeps the marble wall
# sticker rejected in test_head_noun_required.
_TOKEN_SYNONYMS = {
    "contact": {"liner", "laminate", "film", "covering"},
    "paper": {"liner", "laminate", "film", "covering"},
    "liner": {"contact", "paper", "laminate", "film"},
    "laminate": {"contact", "paper", "liner", "film"},
    "film": {"contact", "paper", "liner", "laminate"},
    "covering": {"contact", "paper", "liner"},
    "wrap": {"liner", "laminate", "film"},
    "sealant": {"caulk"},
    "caulk": {"sealant"},
    "sealing": {"sealant", "sealer"},
    "sealer": {"sealant", "sealing"},
    "degreasing": {"degreaser", "degrease"},
    "degreaser": {"degreasing", "degrease"},
    "degrease": {"degreasing", "degreaser"},
    "cleaning": {"cleaner", "clean"},
    "cleaner": {"cleaning", "clean"},
    "wrapping": {"wrap"},
}


def _relevance_score(title: str, wanted: str) -> tuple[int, list[str]]:
    wanted_tokens = _tokenize(wanted)
    title_tokens = _tokenize(title)
    matched = []
    for token in sorted(wanted_tokens):
        if token in title_tokens or (_TOKEN_SYNONYMS.get(token, set()) & title_tokens):
            matched.append(token)
    return len(matched), matched


# =====================================================================
# SELECTION (PATCH 8b)
# =====================================================================
class NoValidOfferError(LookupError):
    def __init__(self, message: str, evaluated: list, query: str):
        super().__init__(message)
        self.evaluated = evaluated
        self.query = query


@dataclass
class CandidateEvaluation:
    title: str
    unit_price: float | None
    merchant: str
    relevance_score: int
    matched_words: list[str]
    rejected_because: str | None = None
    win_reason: str | None = None


@dataclass
class SelectionResult:
    winner: dict | None
    evaluated: list[CandidateEvaluation] = field(default_factory=list)
    win_reason: str | None = None


def _currency_from(offer: dict, top: dict, price_value) -> str | None:
    for source in (offer, top):
        if isinstance(source, dict):
            for key in ("currency", "currency_code"):
                if source.get(key):
                    return str(source[key]).upper()
    if isinstance(price_value, dict):
        for key in ("currency", "currency_code"):
            if price_value.get(key):
                return str(price_value[key]).upper()
    return None


def select_best_offer(products: list, query: str, max_price: float, relevance_text: str = "") -> SelectionResult:
    wanted = relevance_text or query
    wanted_tokens = _tokenize(wanted)
    head_nouns = _head_nouns(wanted)
    min_needed = _required_matches(wanted_tokens)

    evaluated: list[CandidateEvaluation] = []
    valid: list[dict] = []

    for top in products:
        if not isinstance(top, dict):
            continue
        offers = top.get("offers", [])
        offer_list = offers if offers else [{}]
        for offer in offer_list:
            if not isinstance(offer, dict):
                offer = {}
            raw_price = offer.get("price", offer.get("amount"))
            price = _price_from_value(raw_price) if raw_price is not None else None
            if price is None:
                price = _price_from_value(top.get("price"))
                raw_price = top.get("price")
            full_title = top.get("title", query) or query
            merchant = (
                offer.get("domain")
                or top.get("merchant")
                or top.get("brand_name")
                or "Unknown retailer"
            )
            score, matched = _relevance_score(full_title, wanted)
            matched_set = set(matched)

            rejected = None
            if price is None or price <= 0:
                rejected = "price_unreadable"
            else:
                currency = _currency_from(offer, top, raw_price)
                if currency is not None and currency != "USD":
                    rejected = "currency_mismatch"
                elif price > max_price:
                    rejected = "price_over_cap"
                elif score == 0:
                    rejected = "score_zero"
                elif score < min_needed:
                    rejected = "insufficient_relevance"
                elif not (matched_set & head_nouns):
                    rejected = "head_noun_missing"

            evaluation = CandidateEvaluation(
                title=full_title[:80],
                unit_price=round(price, 2) if price is not None else None,
                merchant=merchant,
                relevance_score=score,
                matched_words=matched,
                rejected_because=rejected,
            )
            evaluated.append(evaluation)

            if rejected is not None:
                continue
            valid.append({
                "product_id": top.get("id", ""),
                "title": full_title[:80],
                "unit_price": round(price, 2),
                "merchant": merchant,
                "buy_url": offer.get("url") or top.get("url") or top.get("product_url") or "",
                "image_url": top.get("image_url", ""),
                "relevance_score": score,
                "matched_words": matched,
            })

    if not valid:
        raise NoValidOfferError(
            f"No product for '{query}' had a readable USD price <= ${max_price:.2f} "
            f"AND >= {min_needed} matching word(s) including a head noun of '{wanted}'.",
            evaluated=evaluated,
            query=query,
        )

    valid_sorted = sorted(valid, key=lambda c: (-c["relevance_score"], c["unit_price"]))
    winner = valid_sorted[0]
    if len(valid) == 1:
        win_reason = "only_valid_offer"
    elif valid_sorted[1]["relevance_score"] == winner["relevance_score"]:
        win_reason = "relevance_tie_lowest_price"
    else:
        win_reason = "highest_relevance"
    winner["win_reason"] = win_reason

    for evaluation in evaluated:
        if (
            evaluation.rejected_because is None
            and evaluation.title == winner["title"]
            and evaluation.unit_price == winner["unit_price"]
            and evaluation.merchant == winner["merchant"]
            and evaluation.win_reason is None
        ):
            evaluation.win_reason = win_reason
            break
    return SelectionResult(winner=winner, evaluated=evaluated, win_reason=win_reason)


def search_channel3_product(query: str, max_price: float, relevance_text: str = "") -> dict:
    api_key = require_env("CHANNEL3_API_KEY")
    url = "https://api.trychannel3.com/v1/search"
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "x-user-id": "spectocart_demo_user",
    }
    payload = {
        "query": query,
        "filters": {"price": {"max_price": max_price}, "availability": ["InStock"]},
        "limit": 10,
    }
    res = requests.post(url, headers=headers, json=payload, timeout=12)
    res.raise_for_status()
    data = res.json()

    products = data if isinstance(data, list) else data.get("products", data.get("results", []))
    if not products:
        raise LookupError(f"Channel3 returned no products for '{query}'.")

    if os.getenv("DEBUG_CHANNEL3") == "1":
        print("\n=== RAW Channel3 response (first product) ===")
        print(json.dumps(products[0], indent=2)[:4000])
        print("=== END ===\n")

    result = select_best_offer(products, query, max_price, relevance_text=relevance_text)
    winner = dict(result.winner)
    winner["evaluated"] = [asdict(e) for e in result.evaluated]
    winner["raw_products"] = products
    return winner


# =====================================================================
# PROJECT MATH (PATCH 8c)
# =====================================================================
@dataclass
class ProjectMath:
    length_in: float | None
    width_in: float | None
    area_sqft: float | None
    waste_pct: float
    coverage_sqft_per_roll: float | None
    rolls_needed: int | None
    derivation: str
    assumptions: list[str]


DEFAULT_COVERAGE_SQFT = 15.0


def compute_project_math(
    length_in: float | None,
    width_in: float | None,
    coverage_sqft_per_roll: float | None,
    waste_pct: float = 10.0,
    fallback_bom_quantity: int | None = None,
) -> ProjectMath:
    assumptions: list[str] = []

    if length_in is None or width_in is None:
        return ProjectMath(
            length_in=None, width_in=None, area_sqft=None, waste_pct=waste_pct,
            coverage_sqft_per_roll=coverage_sqft_per_roll, rolls_needed=None,
            derivation="dimensions not supplied; using BOM quantity"
                       + (f" of {fallback_bom_quantity}" if fallback_bom_quantity else ""),
            assumptions=["dimensions_missing"],
        )

    if not math.isfinite(length_in) or not math.isfinite(width_in) or length_in <= 0 or width_in <= 0:
        raise ValueError(f"Dimensions must be finite and positive (got length={length_in!r}, width={width_in!r}).")

    area_sqft = (length_in * width_in) / 144.0
    if coverage_sqft_per_roll is None or coverage_sqft_per_roll <= 0:
        assumptions.append(f"coverage_assumed_{DEFAULT_COVERAGE_SQFT:g}sqft")
        coverage_sqft_per_roll = DEFAULT_COVERAGE_SQFT

    rolls = math.ceil(area_sqft * (1 + waste_pct / 100.0) / coverage_sqft_per_roll)
    return ProjectMath(
        length_in=length_in, width_in=width_in, area_sqft=round(area_sqft, 2),
        waste_pct=waste_pct, coverage_sqft_per_roll=coverage_sqft_per_roll,
        rolls_needed=rolls,
        derivation=(
            f"ceil({area_sqft:.2f} sqft * {1 + waste_pct / 100:.2f} waste / "
            f"{coverage_sqft_per_roll:.2f} sqft per roll) = {rolls} roll(s)"
        ),
        assumptions=assumptions,
    )


def _pick_math_item(sourced_cart: list[dict]) -> dict | None:
    for row in sourced_cart:
        if row.get("priority") == "Essential" and int(row.get("quantity", 0)) > 1:
            return row
    for row in sourced_cart:
        if row.get("priority") == "Essential":
            return row
    return None


# =====================================================================
# BUDGET FALLBACK
# =====================================================================
FALLBACK_POLICY = "optional items dropped by ascending relevance, descending line total"


def apply_budget_fallback(sourced_cart: list[dict], budget: float) -> tuple[list[dict], list[dict], float, bool]:
    total = round(sum(float(row.get("line_total", 0)) for row in sourced_cart), 2)
    if total < budget:
        return list(sourced_cart), [], total, True

    kept = list(sourced_cart)
    drops: list[dict] = []
    optional_sorted = sorted(
        [row for row in kept if row.get("priority") == "Optional"],
        key=lambda r: (float(r.get("relevance_score", 0)), -float(r.get("line_total", 0))),
    )
    for row in optional_sorted:
        if total < budget:
            break
        kept.remove(row)
        total = round(total - float(row.get("line_total", 0)), 2)
        drops.append({
            "bom_item": row.get("bom_item"),
            "line_total": row.get("line_total"),
            "relevance_score": row.get("relevance_score", 0),
            "reason": f"dropped by budget fallback: Optional item, policy is {FALLBACK_POLICY}",
        })
    return kept, drops, total, total < budget


# =====================================================================
# STEP PLAN (PATCH 8d)
# =====================================================================
def _fallback_step_plan(bom_items: list[dict]) -> list[dict]:
    steps = [{"n": 1, "action": "Prepare the workspace and clean the surface", "item": "none", "est_min": 15}]
    for i, b in enumerate(bom_items, start=2):
        steps.append({
            "n": i, "action": f"Apply / use {b.get('item_name', 'item')}",
            "item": b.get("item_name", "unknown"), "est_min": 15,
        })
    steps.append({"n": len(steps) + 1, "action": "Finish and inspect the result", "item": "none", "est_min": 10})
    return steps


def generate_step_plan(bom_items: list[dict], project_title: str) -> list[dict]:
    item_lines = "\n".join(
        f"  - {b.get('item_name')} (priority: {b.get('priority')})"
        for b in bom_items
    )
    prompt = f"""
    You are a project planner. The project is: "{project_title}".
    The purchased items are:
    {item_lines}

    Produce an ordered step plan (2 to 8 steps) for completing the project.
    Each step must be short, imperative, and name which purchased item (if any) it uses.
    Use the exact item name from the list above, or "none" if the step uses no purchased item.
    Estimate minutes per step.
    """
    try:
        client = get_gemini_client()
        response = _generate_with_fallback_and_retry(
            lambda model: client.models.generate_content(
                model=model,
                contents=[prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=StepPlan,
                    temperature=0.3,
                ),
            ),
            label="step plan",
        )
        if not response.text:
            print("  Step plan: Gemini returned no text; using fallback template.")
            return _fallback_step_plan(bom_items)
        plan = StepPlan.model_validate_json(response.text)
        return [s.model_dump() for s in plan.steps]
    except Exception as exc:
        print(f"  Step plan generation failed ({exc}); using fallback template.")
        return _fallback_step_plan(bom_items)


# =====================================================================
# CRITIC (PATCH 8e)
# =====================================================================
def critic_check(item_name: str, sourced_title: str, merchant: str, unit_price: float) -> CriticVerdict | None:
    prompt = f"""
    You are a skeptical procurement reviewer.
    BOM item: "{item_name}"
    Sourced product title: "{sourced_title}"
    Merchant: {merchant}
    Unit price: ${unit_price:.2f}

    Is this sourced product plausibly the right category and use for the BOM item?
    Example of NOT plausible: "Silicone for Body (HU-100)" for "Clear Kitchen & Bath Silicone Sealant"
    - both say "silicone", but they are different products.
    Example of NOT plausible: "Marble pattern wall sticker sheet" for "Waterproof marble vinyl wrap roll".

    Return JSON per the schema. Be strict: if in doubt, plausible=false with a reason.
    """
    try:
        client = get_gemini_client()
        response = _generate_with_fallback_and_retry(
            lambda model: client.models.generate_content(
                model=model,
                contents=[prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=CriticVerdict,
                    temperature=0.1,
                ),
            ),
            label="critic",
        )
        if not response.text:
            return None
        return CriticVerdict.model_validate_json(response.text)
    except Exception as exc:
        print(f"    Critic check skipped ({exc}).")
        return None


# =====================================================================
# RECEIPT
# =====================================================================
class Receipt(TypedDict, total=False):
    generated_at: str
    demo_mode: bool
    photo_path: str
    photo_sha256: str
    project_title: str
    budget: float
    bom: list
    project_math: dict
    assumptions: list
    fallback_policy: str
    fallback_applied: bool
    fallback_drops: list
    sourced_cart: list
    sourced_total: float
    evaluated_by_item: list
    steps: list
    critic_verdicts: list
    paypal_order_id: str
    paypal_status: str
    paypal_total_usd: str
    paypal_approve_url: str
    paypal_request_id: str
    capture_id: str
    capture_status: str
    capture_fee_usd: str
    capture_net_usd: str
    webhook_event_id: str


def _photo_sha256(image_path: str) -> str:
    try:
        digest = hashlib.sha256()
        with open(image_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def build_receipt(
    *,
    photo_path: str,
    project_title: str,
    budget: float,
    bom_items: list[dict],
    sourced_cart: list[dict],
    evaluated_by_item: list[dict],
    fallback_policy: str,
    fallback_applied: bool,
    fallback_drops: list[dict],
    sourced_total: float,
    project_math: dict | None = None,
    steps: list[dict] | None = None,
    critic_verdicts: list[dict] | None = None,
    paypal_order: dict | None = None,
    demo_mode: bool = False,
) -> Receipt:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "demo_mode": demo_mode,
        "photo_path": photo_path,
        "photo_sha256": _photo_sha256(photo_path) if photo_path else "",
        "project_title": project_title,
        "budget": budget,
        "bom": bom_items,
        "project_math": project_math or {},
        "assumptions": (project_math or {}).get("assumptions", []),
        "fallback_policy": fallback_policy,
        "fallback_applied": fallback_applied,
        "fallback_drops": fallback_drops,
        "sourced_cart": [
            {k: v for k, v in row.items() if k not in ("evaluated", "raw_products")}
            for row in sourced_cart
        ],
        "sourced_total": sourced_total,
        "evaluated_by_item": evaluated_by_item,
        "steps": steps or [],
        "critic_verdicts": critic_verdicts or [],
        "paypal_order_id": (paypal_order or {}).get("order_id", ""),
        "paypal_status": (paypal_order or {}).get("status", ""),
        "paypal_total_usd": (paypal_order or {}).get("total_usd", ""),
        "paypal_approve_url": (paypal_order or {}).get("approve_url", ""),
        "paypal_request_id": (paypal_order or {}).get("request_id", ""),
        "capture_id": "",
        "capture_status": "",
        "capture_fee_usd": "",
        "capture_net_usd": "",
        "webhook_event_id": "",
    }


def write_receipt(receipt: Receipt, path: str | None = None, update_last: bool = True) -> str:
    if path is None:
        order_id = receipt.get("paypal_order_id") or "no-order"
        path = f"spectocart_receipt_{order_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=2)
    if update_last:
        if os.path.exists("last_receipt.json"):
            try:
                with open("last_receipt.json", encoding="utf-8") as f:
                    existing = json.load(f)
                if existing.get("demo_mode", False) != receipt.get("demo_mode", False):
                    print(f"  Not overwriting last_receipt.json (demo_mode mismatch). Wrote {path} only.")
                    return path
            except (OSError, json.JSONDecodeError):
                pass
        with open("last_receipt.json", "w", encoding="utf-8") as f:
            json.dump(receipt, f, indent=2)
    return path


# =====================================================================
# PAYPAL
# =====================================================================
def get_paypal_access_token() -> str:
    res = requests.post(
        "https://api-m.sandbox.paypal.com/v1/oauth2/token",
        auth=(require_env("PAYPAL_CLIENT_ID"), require_env("PAYPAL_CLIENT_SECRET")),
        data={"grant_type": "client_credentials"},
        headers={"Accept": "application/json"},
        timeout=10,
    )
    res.raise_for_status()
    return res.json()["access_token"]


def create_paypal_sandbox_order(sourced_cart: list[dict], project_title: str) -> dict:
    if not sourced_cart:
        raise ValueError("Cannot create a PayPal order for an empty cart.")

    print("\n[3/3] Authenticating with PayPal Sandbox & generating itemized Order...")
    token = get_paypal_access_token()

    paypal_items = []
    total_cents = 0
    for item in sourced_cart:
        unit_cents = int(round(float(item["unit_price"]) * 100))
        qty = int(item["quantity"])
        total_cents += unit_cents * qty
        paypal_items.append({
            "name": item["sourced_title"][:120],
            "quantity": str(qty),
            "description": f"Vendor: {item['merchant']} | {item['priority']}"[:120],
            "unit_amount": {"currency_code": "USD", "value": f"{unit_cents / 100:.2f}"},
        })
    total_str = f"{total_cents / 100:.2f}"

    purchase_unit = {
        "description": f"SpecToCart BOM: {project_title}"[:120],
        "amount": {
            "currency_code": "USD",
            "value": total_str,
            "breakdown": {"item_total": {"currency_code": "USD", "value": total_str}},
        },
        "items": paypal_items,
    }
    merchant_id = (os.getenv("PAYPAL_MERCHANT_ID") or "").strip()
    if merchant_id:
        purchase_unit["payee"] = {"merchant_id": merchant_id}
        print(f"  Payee (merchant): {merchant_id}")
    else:
        print("  Payee: default merchant for this Client ID (no PAYPAL_MERCHANT_ID set)")

    order_payload = {
        "intent": "CAPTURE",
        "purchase_units": [purchase_unit],
        "application_context": {
            "brand_name": "SpecToCart AI Procurement",
            "user_action": "PAY_NOW",
            "return_url": os.getenv("PAYPAL_RETURN_URL", "https://example.com/success"),
            "cancel_url": os.getenv("PAYPAL_CANCEL_URL", "https://example.com/cancel"),
        },
    }

    request_id = str(uuid.uuid4())
    print(f"  PayPal-Request-Id (idempotency): {request_id}")
    res = requests.post(
        "https://api-m.sandbox.paypal.com/v2/checkout/orders",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "PayPal-Request-Id": request_id,
        },
        json=order_payload,
        timeout=12,
    )
    res.raise_for_status()
    order_data = res.json()

    approve_link = next((l["href"] for l in order_data.get("links", []) if l["rel"] == "approve"), None)
    if not approve_link:
        raise RuntimeError(f"PayPal created order {order_data.get('id')} but returned no approve link.")

    return {
        "order_id": order_data["id"],
        "status": order_data["status"],
        "total_usd": total_str,
        "approve_url": approve_link,
        "request_id": request_id,
    }


def capture_paypal_order(order_id: str) -> dict:
    token = get_paypal_access_token()
    capture_request_id = str(uuid.uuid4())
    print(f"PayPal-Request-Id (idempotency): {capture_request_id}")
    res = requests.post(
        f"https://api-m.sandbox.paypal.com/v2/checkout/orders/{order_id}/capture",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "PayPal-Request-Id": capture_request_id,
        },
        timeout=12,
    )
    if res.status_code >= 400:
        print(f"PayPal capture failed ({res.status_code}): {res.text[:1000]}")
    res.raise_for_status()
    return res.json()


def refund_paypal_capture(capture_id: str, amount_usd: str | None = None) -> dict:
    token = get_paypal_access_token()
    payload: dict = {}
    if amount_usd is not None:
        payload["amount"] = {"currency_code": "USD", "value": f"{float(amount_usd):.2f}"}
    refund_request_id = str(uuid.uuid4())
    print(f"PayPal-Request-Id (idempotency): {refund_request_id}")
    res = requests.post(
        f"https://api-m.sandbox.paypal.com/v2/payments/captures/{capture_id}/refund",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "PayPal-Request-Id": refund_request_id,
        },
        json=payload,
        timeout=12,
    )
    if res.status_code >= 400:
        print(f"PayPal refund failed ({res.status_code}): {res.text[:1000]}")
    res.raise_for_status()
    return res.json()


def get_paypal_order_status(order_id: str) -> dict:
    token = get_paypal_access_token()
    res = requests.get(
        f"https://api-m.sandbox.paypal.com/v2/checkout/orders/{order_id}",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        timeout=12,
    )
    res.raise_for_status()
    return res.json()


# =====================================================================
# SAMPLE IMAGE
# =====================================================================
def create_sample_test_image(filename="sample_countertop.jpg"):
    if not os.path.exists(filename):
        img = Image.new("RGB", (800, 500), color=(235, 230, 220))
        draw = ImageDraw.Draw(img)
        draw.rectangle([80, 200, 720, 420], fill=(160, 140, 120), outline=(90, 75, 60), width=4)
        draw.rectangle([80, 180, 720, 210], fill=(120, 105, 90), outline=(70, 55, 45), width=3)
        draw.text((220, 100), "Worn Kitchen Countertop Surface (Needs Vinyl Wrap)", fill=(40, 40, 40))
        img.save(filename)
    return filename


# =====================================================================
# MAIN
# =====================================================================
def _run_pipeline(args) -> int:
    _require_all_keys(
        ["GEMINI_API_KEY", "CHANNEL3_API_KEY"]
        + ([] if args.no_paypal else ["PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"])
    )

    test_img = args.image if os.path.exists(args.image) else create_sample_test_image()
    goal = args.goal
    budget_limit = args.budget

    bom = extract_bom_from_image(test_img, total_budget=budget_limit, user_goal=goal)
    print(f"\nGemini Extracted Project: {bom.project_title} ({len(bom.items)} line items)")
    print("  Structured BOM from Gemini Vision:")
    for bom_item in bom.items:
        print(f"    - {bom_item.item_name} (x{bom_item.quantity}, max ${bom_item.max_unit_price:.2f}, "
              f"{bom_item.priority}, search: '{bom_item.search_query}')")

    print("\n[2/3] Querying Channel3 Product Graph for live inventory...")
    sourced_cart: list[dict] = []
    evaluated_by_item: list[dict] = []
    for item in bom.items:
        try:
            c3 = search_channel3_product(item.search_query, item.max_unit_price, relevance_text=item.item_name)
        except NoValidOfferError as exc:
            print(f"  ✗ {item.item_name}: no valid offer. Rejections:")
            for e in exc.evaluated:
                print(f"      - '{e.title}' @ ${e.unit_price} [{e.merchant}]: {e.rejected_because}")
            raise
        line_total = round(c3["unit_price"] * item.quantity, 2)
        sourced_cart.append({
            "bom_item": item.item_name,
            "priority": item.priority,
            "quantity": item.quantity,
            "target_unit_budget": item.max_unit_price,
            "sourced_title": c3["title"],
            "unit_price": c3["unit_price"],
            "line_total": line_total,
            "merchant": c3["merchant"],
            "buy_url": c3["buy_url"],
            "ai_reasoning": item.ai_reasoning,
            "relevance_score": c3["relevance_score"],
            "matched_words": c3["matched_words"],
            "win_reason": c3["win_reason"],
        })

        raw = c3.get("raw_products", [])
        raw_serialized = json.dumps(raw, default=str)
        if len(raw_serialized) > 50_000:
            raw_entry = {
                "sha256": hashlib.sha256(raw_serialized.encode()).hexdigest(),
                "size_bytes": len(raw_serialized),
                "note": "raw products truncated to hash; set DEBUG_CHANNEL3=1 to print full response",
            }
        else:
            raw_entry = raw

        evaluated_by_item.append({
            "bom_item": item.item_name,
            "query": item.search_query,
            "evaluated": c3.get("evaluated", []),
            "raw_products": raw_entry,
            "win_reason": c3["win_reason"],
        })
        print(f"  - {item.item_name} (x{item.quantity}) -> '{c3['title']}' @ ${c3['unit_price']} "
              f"[{c3['merchant']}] (relevance {c3['relevance_score']}: "
              f"{', '.join(c3['matched_words'])}, won by {c3['win_reason']})")
        if c3["buy_url"]:
            print(f"      Buy: {c3['buy_url']}")

    # Project math (dimensions from CLI)
    # FIXED 2026-10-27: project_math is now recorded even when
    # dimensions are missing, so the receipt carries
    # assumptions=["dimensions_missing"] instead of project_math={}.
    project_math = None
    math_item = next(
        (r for r in sourced_cart if r["bom_item"] == args.math_item), None
    ) if args.math_item else _pick_math_item(sourced_cart)
    if args.length is not None and args.width is not None:
        if math_item:
            pm = compute_project_math(
                args.length, args.width, args.coverage,
                fallback_bom_quantity=math_item["quantity"],
            )
            project_math = asdict(pm)
            if pm.rolls_needed:
                old_qty = math_item["quantity"]
                math_item["quantity"] = pm.rolls_needed
                math_item["line_total"] = round(math_item["unit_price"] * pm.rolls_needed, 2)
                print(f"\n  Project math: {pm.derivation}")
                print(f"  Quantity for '{math_item['bom_item']}' updated {old_qty} -> {pm.rolls_needed}.")
        else:
            print("\n  Project math skipped: no suitable Essential item found.")
    else:
        pm = compute_project_math(
            None, None, args.coverage,
            fallback_bom_quantity=math_item["quantity"] if math_item else None,
        )
        project_math = asdict(pm)
        print(f"\n  Project math: {pm.derivation} (dimensions not supplied; using BOM quantity)")

    # Critic pass
    critic_verdicts: list[dict] = []
    if args.critic:
        print("\n[2.5/3] Running critic pass (second Gemini model call per item)...")
        surviving = []
        for row in sourced_cart:
            v = critic_check(row["bom_item"], row["sourced_title"], row["merchant"], row["unit_price"])
            critic_verdicts.append({
                "bom_item": row["bom_item"],
                "sourced_title": row["sourced_title"],
                "verdict": v.model_dump() if v is not None else None,
            })
            if v is None:
                surviving.append(row)
                continue
            if v.plausible:
                print(f"  OK     {row['bom_item']}: {v.reason}")
                surviving.append(row)
            else:
                print(f"  REJECT {row['bom_item']}: {v.reason}")
                if args.strict_critic:
                    if row["priority"] == "Essential":
                        print(f"STOPPING: Essential item '{row['bom_item']}' failed the critic and --strict-critic is set.")
                        return 1
                    print(f"  Dropped (critic, Optional): {row['bom_item']}")
                else:
                    print("  (kept; pass --strict-critic to abort/drop)")
                    surviving.append(row)
        sourced_cart = surviving

    # Budget check + fallback
    grand_total = round(sum(row["line_total"] for row in sourced_cart), 2)
    print(f"\nSourced grand total: ${grand_total:.2f} (budget: ${budget_limit:.2f})")
    fallback_drops: list[dict] = []
    fallback_applied = False
    if grand_total >= budget_limit:
        print(f"Over budget - applying fallback, policy: {FALLBACK_POLICY}")
        sourced_cart, fallback_drops, grand_total, ok = apply_budget_fallback(sourced_cart, budget_limit)
        fallback_applied = bool(fallback_drops)
        for drop in fallback_drops:
            print(f"  Dropped (Optional): {drop['bom_item']} (${drop['line_total']})")
        print(f"Fallback total: ${grand_total:.2f} (budget: ${budget_limit:.2f}), ok={ok}")
        if not ok:
            print("STOPPING: even Essential-only total is not under budget. No PayPal order was created.")
            return 1

    # Step plan (final cart)
    print("\n[2.75/3] Generating step plan...")
    steps = generate_step_plan(
        [{"item_name": r["bom_item"], "priority": r["priority"]} for r in sourced_cart],
        bom.project_title,
    )
    for s in steps:
        print(f"  {s['n']}. {s['action']} [item: {s['item']}] ({s['est_min']} min)")

    # No-paypal mode
    if args.no_paypal:
        receipt = build_receipt(
            photo_path=test_img, project_title=bom.project_title, budget=budget_limit,
            bom_items=[b.model_dump() for b in bom.items],
            sourced_cart=sourced_cart, evaluated_by_item=evaluated_by_item,
            fallback_policy=FALLBACK_POLICY, fallback_applied=fallback_applied,
            fallback_drops=fallback_drops, sourced_total=grand_total,
            project_math=project_math, steps=steps, critic_verdicts=critic_verdicts,
        )
        write_receipt(receipt)
        print("\n--no-paypal: stopping before PayPal order creation.")
        return 0

    # PayPal
    paypal_order = create_paypal_sandbox_order(sourced_cart, bom.project_title)
    receipt = build_receipt(
        photo_path=test_img, project_title=bom.project_title, budget=budget_limit,
        bom_items=[b.model_dump() for b in bom.items],
        sourced_cart=sourced_cart, evaluated_by_item=evaluated_by_item,
        fallback_policy=FALLBACK_POLICY, fallback_applied=fallback_applied,
        fallback_drops=fallback_drops, sourced_total=grand_total,
        project_math=project_math, steps=steps, critic_verdicts=critic_verdicts,
        paypal_order=paypal_order,
    )
    receipt_path = write_receipt(receipt)
    print(f"Audit receipt written: {receipt_path}")

    print("\n============================================================")
    print("PAYPAL ORDER CREATED - NOT PAID YET")
    print("============================================================")
    print(f"PayPal Order ID : {paypal_order['order_id']}")
    print(f"Order Status    : {paypal_order['status']}")
    print(f"Grand Total     : ${paypal_order['total_usd']} USD")
    print(f"Approve Link    : {paypal_order['approve_url']}")
    print("Next: open the approve link, approve in the sandbox, then:")
    print(f"  python3 core_pipeline.py capture {paypal_order['order_id']}")
    print("============================================================")
    return 0


def main() -> int:
    argv = sys.argv[1:]

    if argv and argv[0] == "capture":
        _require_all_keys(["PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"])
        if len(argv) != 2:
            raise SystemExit("Usage: capture <ORDER_ID>")
        result = capture_paypal_order(argv[1])
        print(f"Capture status: {result.get('status')}")
        print(result)
        try:
            if os.path.exists("last_receipt.json"):
                with open("last_receipt.json", encoding="utf-8") as f:
                    receipt = json.load(f)
                if receipt.get("paypal_order_id") == argv[1]:
                    cap = result.get("purchase_units", [{}])[0].get("payments", {}).get("captures", [{}])[0]
                    breakdown = cap.get("seller_receivable_breakdown", {})
                    receipt["capture_id"] = cap.get("id", "")
                    receipt["capture_status"] = result.get("status", "")
                    receipt["capture_fee_usd"] = breakdown.get("paypal_fee", {}).get("value", "")
                    receipt["capture_net_usd"] = breakdown.get("net_amount", {}).get("value", "")
                    write_receipt(receipt, f"spectocart_receipt_{argv[1]}.json")
                    print(f"Receipt updated: spectocart_receipt_{argv[1]}.json")
        except Exception as exc:
            print(f"Receipt update skipped: {exc}")
        return 0

    if argv and argv[0] == "status":
        _require_all_keys(["PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"])
        if len(argv) != 2:
            raise SystemExit("Usage: status <ORDER_ID>")
        print(f"Order status: {get_paypal_order_status(argv[1]).get('status')}")
        return 0

    if argv and argv[0] == "refund":
        _require_all_keys(["PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"])
        if len(argv) not in (2, 3):
            raise SystemExit("Usage: refund <CAPTURE_ID> [amount]")
        amount = argv[2] if len(argv) == 3 else None
        result = refund_paypal_capture(argv[1], amount)
        print(f"Refund status: {result.get('status')}")
        print(result)
        return 0

    if argv and argv[0] == "demo-fallback":
        if len(argv) != 3:
            raise SystemExit("Usage: demo-fallback <cart.json> <budget>")
        saved_path, demo_budget = argv[1], float(argv[2])
        with open(saved_path, encoding="utf-8") as f:
            saved = json.load(f)
        items = saved.get("items", saved if isinstance(saved, list) else [])
        new_cart, drops, final_total, ok = apply_budget_fallback(items, demo_budget)
        original = round(sum(float(r.get("line_total", 0)) for r in items), 2)
        print(f"Demo fallback: budget ${demo_budget:.2f}, original total ${original:.2f}")
        for d in drops:
            print(f"  Dropped (Optional): {d['bom_item']} (${d['line_total']}) - {d['reason']}")
        print(f"Demo fallback: new total ${final_total:.2f}, ok={ok}")
        receipt = build_receipt(
            photo_path=saved.get("photo_path", ""),
            project_title=saved.get("project_title", "Demo saved cart"),
            budget=demo_budget,
            bom_items=saved.get("bom", []),
            sourced_cart=new_cart,
            evaluated_by_item=saved.get("evaluated_by_item", []),
            fallback_policy=FALLBACK_POLICY,
            fallback_applied=bool(drops),
            fallback_drops=drops,
            sourced_total=final_total,
            demo_mode=True,
        )
        os.makedirs("demo", exist_ok=True)
        write_receipt(receipt, "demo/demo_receipt.json", update_last=False)
        print("Demo receipt written: demo/demo_receipt.json")
        return 0 if ok else 1

    parser = argparse.ArgumentParser(description="SpecToCart pipeline")
    parser.add_argument("--image", default="sample_countertop.jpg")
    parser.add_argument("--goal", default="Wrap this worn kitchen countertop with waterproof marble contact paper and protective edge sealant.")
    parser.add_argument("--budget", type=float, default=150.0)
    parser.add_argument("--length", type=float, default=None, help="Countertop length in inches (enables project math)")
    parser.add_argument("--width", type=float, default=None, help="Countertop width in inches (enables project math)")
    parser.add_argument("--coverage", type=float, default=None, help="Coverage in sq ft per roll (default: 15, labeled assumption)")
    parser.add_argument("--math-item", default=None, help="BOM item to receive the derived quantity (default: first Essential with qty>1)")
    parser.add_argument("--critic", action="store_true", help="Enable the second-pass LLM critic")
    parser.add_argument("--strict-critic", action="store_true", help="Abort/drop on implausible verdicts")
    parser.add_argument("--no-paypal", action="store_true", help="Stop before PayPal order creation (dry run)")
    args = parser.parse_args(argv)
    return _run_pipeline(args)


if __name__ == "__main__":
    raise SystemExit(main())
