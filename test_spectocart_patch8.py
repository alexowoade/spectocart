"""
Tests for SpecToCart PATCH 8 - pure functions only.

Run in ~/spectocart with (venv) active:
    python3 -m pytest test_spectocart_patch8.py -v
    # or, without pytest:
    python3 test_spectocart_patch8.py

No Gemini, Channel3, or PayPal calls. All tests hit pure functions.
"""

import sys
sys.path.insert(0, ".")

from core_pipeline import (
    FALLBACK_POLICY,
    NoValidOfferError,
    _generate_with_fallback_and_retry,
    _generate_with_retry,
    _gemini_model_chain,
    _is_transient_gemini_error,
    _meaningful_tokens,
    _price_from_value,
    _relevance_score,
    _required_matches,
    _tokenize,
    apply_budget_fallback,
    compute_project_math,
    select_best_offer,
)


# ---- price parsing ----
def test_price_from_value():
    assert _price_from_value(12.99) == 12.99
    assert _price_from_value("$12.99") == 12.99
    assert _price_from_value({"price": 12.99}) == 12.99
    assert _price_from_value("not a price") is None
    assert _price_from_value(True) is None          # bool guard
    assert _price_from_value(float("nan")) is None  # NaN guard
    assert _price_from_value(float("inf")) is None  # inf guard
    assert _price_from_value("nan") is None         # string NaN guard
    assert _price_from_value("inf") is None         # string inf guard


# ---- relevance rules ----
def test_tokenize_stemming():
    assert "inch" in _tokenize("inches")   # ches -> ch
    assert "box" in _tokenize("boxes")     # xes -> x
    assert "battery" in _tokenize("batteries")
    assert "glass" in _tokenize("glass")   # exception preserved
    assert "liner" in _tokenize("liners")
    # FIXED 2026-10-27: the old generic "es" strip broke these:
    # tubes -> tub, cables -> cabl, cakes -> cak. Now only the
    # final "s" is stripped for e-ending words.
    assert "tube" in _tokenize("tubes")
    assert "cable" in _tokenize("cables")
    assert "cake" in _tokenize("cakes")
    assert "class" in _tokenize("classes")
    # "tubes" must not leak a bogus "tub" token into head nouns.
    assert _meaningful_tokens("Silicone Sealant Tubes") == ["silicone", "sealant"]


def test_single_category_word_rejected():
    # "Silicone for Body" only matches "silicone" out of 5 wanted words.
    # min_needed = 2 (flat, Patch 8 v3), score = 1 -> insufficient_relevance. This is the
    # exact demo failure from the pre-PATCH-8 receipt.
    offers = [
        {"title": "Silicone for Body (HU-100)", "price": 2.50, "merchant": "hurom.com"},
        {"title": "Clear Kitchen Bath Silicone Sealant 10oz", "price": 8.00, "merchant": "lowes.com"},
    ]
    result = select_best_offer(
        offers, "clear kitchen bath silicone sealant", 10.00,
        relevance_text="Clear Kitchen & Bath Silicone Sealant",
    )
    assert result.winner["title"].startswith("Clear Kitchen Bath")
    rejections = {e.title: e.rejected_because for e in result.evaluated}
    assert rejections["Silicone for Body (HU-100)"] == "insufficient_relevance"


def test_head_noun_required():
    # "Marble Wall Sticker" shares {marble, waterproof}? No - it shares
    # {marble} only against "Waterproof Marble Vinyl Wrap", but let's
    # give it "waterproof" too and still require the head noun "wrap".
    offers = [
        {"title": "Waterproof Marble Wall Sticker Peel Stick", "price": 9.00, "merchant": "a.com"},
        {"title": "Waterproof Marble Vinyl Wrap Roll 15sqft", "price": 14.99, "merchant": "lowes.com"},
    ]
    result = select_best_offer(
        offers, "waterproof marble vinyl wrap", 20.00,
        relevance_text="Waterproof Marble Vinyl Wrap",
    )
    assert "Vinyl Wrap" in result.winner["title"]
    rejections = {e.title: e.rejected_because for e in result.evaluated}
    assert rejections["Waterproof Marble Wall Sticker Peel Stick"] == "head_noun_missing"


def test_synonym_contact_paper_is_shelf_liner():
    # The exact live Patch 8 failure 2026-10-27: BOM says "contact
    # paper", retail says "shelf liner". Without synonyms the
    # $16.78 Con-Tact product failed head_noun_missing, even
    # though it matched marble, vinyl and roll. With synonyms
    # contact and paper both match via liner, so it scores 5
    # and wins, and the wall sticker still does not rescue itself.
    offers = [
        {"title": "Con-Tact Brand Self-Adhesive Shelf Liner - Beige Marble Vinyl Roll", "price": 16.78, "merchant": "lowes.com"},
        {"title": "Adhesive Shelf Liner - Marble", "price": 7.99, "merchant": "target.com"},
    ]
    result = select_best_offer(
        offers, "waterproof glossy marble contact paper peel and stick roll 24 inch", 25.00,
        relevance_text="Waterproof Marble Vinyl Contact Paper Roll",
    )
    assert result.winner["unit_price"] == 16.78
    assert result.winner["relevance_score"] == 5


def test_synonym_sealant_is_caulk_and_degreasing_is_degrease():
    offers = [
        {"title": "Clear Silicone Caulk", "price": 8.00},
    ]
    result = select_best_offer(
        offers, "clear silicone caulk", 10.00,
        relevance_text="Clear Waterproof Silicone Sealant",
    )
    assert result.winner["title"] == "Clear Silicone Caulk"
    offers2 = [
        {"title": "Cleaner Degreaser", "price": 5.99},
    ]
    result2 = select_best_offer(
        offers2, "cleaner degreaser", 11.00,
        relevance_text="Surface Prep Degreasing Cleaner",
    )
    assert result2.winner["title"] == "Cleaner Degreaser"


def test_long_bom_name_two_core_matches_pass():
    # The exact second live Patch 8 failure 2026-10-27: Gemini
    # named the item "Glossy Marble Peel and Stick Countertop
    # Vinyl" (6 tokens). The correct Con-Tact product shares
    # only marble + vinyl, so the old scaled minimum of 3
    # rejected it. With a flat minimum of 2, plus head noun
    # vinyl, it passes and wins. A marble-only liner still
    # fails (score 1 < 2).
    offers = [
        {"title": "Con-Tact Brand Self-Adhesive Shelf Liner - Beige Marble Vinyl Roll", "price": 16.78, "merchant": "lowes.com"},
        {"title": "Adhesive Shelf Liner - Marble", "price": 7.99, "merchant": "target.com"},
        {"title": "Duck EasyLiner Adhesive Laminate - Gray Marble", "price": 20.46, "merchant": "dickblick.com"},
    ]
    result = select_best_offer(
        offers, "waterproof marble contact paper self adhesive roll 24 inch", 24.99,
        relevance_text="Glossy Marble Peel and Stick Countertop Vinyl",
    )
    assert result.winner["unit_price"] == 16.78
    assert result.winner["relevance_score"] == 2
    assert _required_matches({"a", "b", "c", "d", "e", "f"}) == 2


def test_critic_verdict_serializes_with_model_dump():
    # Regression for the 2026-10-27 live crash: CriticVerdict
    # is a Pydantic BaseModel, not a dataclass, so the receipt
    # code must use model_dump(), never dataclasses.asdict().
    # This test checks the source directly because the VM test
    # harness stubs pydantic with a bare BaseModel that cannot
    # be instantiated; on Ubuntu, with real pydantic, model_dump()
    # on CriticVerdict returns {"plausible": ..., "reason": ...}.
    import os
    with open(os.path.join(os.path.dirname(__file__), "core_pipeline.py")) as f:
        src = f.read()
    assert "v.model_dump()" in src
    assert "asdict(v)" not in src


def test_gemini_retry_transient_then_success():
    # Patch 8 v5: a 503 on attempt 1, success on attempt 2.
    # delays=(0, 0) keeps the test instant; production uses (30, 60).
    class Fake503(Exception):
        def __init__(self):
            self.code = 503
            super().__init__("503 UNAVAILABLE. high demand")
    calls = {"n": 0}
    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise Fake503()
        return "ok"
    assert _generate_with_retry(flaky, label="test", delays=(0, 0)) == "ok"
    assert calls["n"] == 2


def test_gemini_retry_non_transient_not_retried():
    calls = {"n": 0}
    def bad_key():
        calls["n"] += 1
        raise ValueError("401 UNAUTHENTICATED")
    try:
        _generate_with_retry(bad_key, label="test", delays=(0, 0))
        assert False, "should have raised"
    except ValueError:
        pass
    assert calls["n"] == 1


def test_gemini_retry_gives_up_after_attempts():
    class Fake503(Exception):
        def __init__(self):
            self.code = 503
            super().__init__("503 UNAVAILABLE")
    calls = {"n": 0}
    def always_503():
        calls["n"] += 1
        raise Fake503()
    try:
        _generate_with_retry(always_503, label="test", attempts=3, delays=(0, 0))
        assert False, "should have raised"
    except Fake503:
        pass
    assert calls["n"] == 3
    assert _is_transient_gemini_error(Fake503())
    assert not _is_transient_gemini_error(ValueError("bad schema"))


def test_gemini_fallback_chain_excludes_deprecated_35():
    # Patch 8 v6: gemini-3.5-flash is deprecated and routes to
    # gemini-3.6-flash, so it must NOT be in the chain.
    import os
    old = os.environ.pop("GEMINI_MODEL", None)
    try:
        assert _gemini_model_chain() == ["gemini-3.8-flash", "gemini-3.6-flash"]
        os.environ["GEMINI_MODEL"] = "gemini-3.6-flash"
        assert _gemini_model_chain() == ["gemini-3.6-flash", "gemini-3.8-flash"]
    finally:
        if old is None:
            os.environ.pop("GEMINI_MODEL", None)
        else:
            os.environ["GEMINI_MODEL"] = old


def test_gemini_fallback_second_model_on_transient():
    # TEST FIX 2026-10-28 evening: this test must not depend on the
    # user's .env. With GEMINI_MODEL=gemini-3.6-flash set (primary 3.6,
    # chosen 2026-10-28 15:34), the chain is [3.6, 3.8], not [3.8, 3.6],
    # so the old hard-coded assertion failed even though the code was
    # correct. Pin the primary to 3.8 for this test, then restore it.
    import os
    old = os.environ.get("GEMINI_MODEL")
    os.environ["GEMINI_MODEL"] = "gemini-3.8-flash"
    try:
        class Fake503(Exception):
            def __init__(self):
                self.code = 503
                super().__init__("503 UNAVAILABLE")
        calls = []
        def flaky(model):
            calls.append(model)
            if model == "gemini-3.8-flash":
                raise Fake503()
            return "ok-3.6"
        assert _generate_with_fallback_and_retry(flaky, label="test", delays=(0, 0)) == "ok-3.6"
        assert calls == ["gemini-3.8-flash", "gemini-3.6-flash"]
    finally:
        if old is None:
            os.environ.pop("GEMINI_MODEL", None)
        else:
            os.environ["GEMINI_MODEL"] = old


def test_gemini_fallback_non_transient_not_masked():
    # Same env-pinning fix as above: with GEMINI_MODEL=3.6, the first
    # call is 3.6, not 3.8, so the old assertion was env-dependent.
    import os
    old = os.environ.get("GEMINI_MODEL")
    os.environ["GEMINI_MODEL"] = "gemini-3.8-flash"
    try:
        calls = []
        def bad_request(model):
            calls.append(model)
            raise ValueError("400 INVALID_ARGUMENT")
        try:
            _generate_with_fallback_and_retry(bad_request, label="test", delays=(0, 0))
            assert False, "should have raised"
        except ValueError:
            pass
        assert calls == ["gemini-3.8-flash"]
    finally:
        if old is None:
            os.environ.pop("GEMINI_MODEL", None)
        else:
            os.environ["GEMINI_MODEL"] = old


def test_all_zero_raises_lookup_error():
    offers = [
        {"title": "Banana", "price": 5.00},
        {"title": "Chair", "price": 6.00},
    ]
    try:
        select_best_offer(offers, "Silicone Sealant", 20.00, relevance_text="Silicone Sealant")
    except NoValidOfferError as exc:
        assert isinstance(exc, LookupError)
        assert len(exc.evaluated) == 2
        assert all(e.rejected_because is not None for e in exc.evaluated)
        return
    raise AssertionError("all-zero offers did not raise NoValidOfferError")


def test_tiebreak_cheapest_wins():
    offers = [
        {"title": "Silicone Sealant Tube", "price": 10.00},
        {"title": "Silicone Sealant Pack", "price": 7.00},
    ]
    result = select_best_offer(
        offers, "Silicone Sealant", 20.00, relevance_text="Silicone Sealant",
    )
    # Tie on relevance -> cheapest wins, and win_reason says so.
    assert result.win_reason == "relevance_tie_lowest_price"
    assert result.winner["unit_price"] == 7.00


def test_only_valid_offer():
    offers = [
        {"title": "Silicone Sealant Tube", "price": 9.00},
        {"title": "Banana", "price": 5.00},
    ]
    result = select_best_offer(offers, "Silicone Sealant", 20.00, relevance_text="Silicone Sealant")
    assert result.win_reason == "only_valid_offer"
    assert result.winner["title"] == "Silicone Sealant Tube"


def test_highest_relevance_beats_cheaper():
    # FIXED 2026-10-27: the old second offer ("Silicone Sealant Tube",
    # score 2) is now correctly rejected as insufficient_relevance
    # against the 5-word query (needs 3), so it could not be the
    # "cheaper but lower relevance" rival. The new second offer
    # scores 4 (clear, kitchen, silicone, sealant), stays valid,
    # and still loses to the 5-word winner on relevance.
    offers = [
        {"title": "Clear Kitchen Bath Silicone Sealant", "price": 12.00},
        {"title": "Clear Kitchen Silicone Sealant", "price": 3.00},
    ]
    result = select_best_offer(
        offers, "Silicone Sealant", 20.00,
        relevance_text="Clear Kitchen & Bath Silicone Sealant",
    )
    assert result.winner["unit_price"] == 12.00
    assert result.win_reason == "highest_relevance"


def test_rejection_reasons():
    offers = [
        {"title": "Silicone Sealant Tube", "price": 8.00},
        {"title": "Silicone Sealant Tube", "price": "not a price"},
        {"title": "Silicone Sealant Tube", "price": 9.00, "currency": "EUR"},
        {"title": "Silicone Sealant Tube", "price": 99.00},
        {"title": "Banana", "price": 5.00},
    ]
    result = select_best_offer(offers, "Silicone Sealant", 20.00, relevance_text="Silicone Sealant")
    assert result.winner["unit_price"] == 8.00
    reasons = [e.rejected_because for e in result.evaluated]
    assert "price_unreadable" in reasons
    assert "currency_mismatch" in reasons
    assert "price_over_cap" in reasons
    assert "score_zero" in reasons


# ---- project math ----
def test_project_math_rolls():
    pm = compute_project_math(96, 25, 15.0, waste_pct=10.0)
    # area = 96*25/144 = 16.67 sqft; 10% waste -> 18.33; /15 -> 1.22 -> ceil -> 2
    assert pm.rolls_needed == 2
    assert "= 2 roll" in pm.derivation
    assert "dimensions_missing" not in pm.assumptions


def test_project_math_missing_dimensions():
    pm = compute_project_math(None, None, 15.0, fallback_bom_quantity=3)
    assert pm.rolls_needed is None
    assert pm.area_sqft is None
    assert "dimensions_missing" in pm.assumptions
    assert "BOM quantity of 3" in pm.derivation


def test_project_math_coverage_assumption_labeled():
    pm = compute_project_math(96, 25, None)
    assert any("coverage_assumed" in a for a in pm.assumptions)
    assert pm.coverage_sqft_per_roll == 15.0


def test_project_math_rejects_bad_dimensions():
    # FIXED 2026-10-27: the old "import pytest_placeholder" line broke
    # this test with ModuleNotFoundError before it ever tested the
    # dimension check. That import is deleted; no pytest is needed.
    try:
        compute_project_math(0, 25, 15.0)
    except ValueError:
        return
    raise AssertionError("zero dimension did not raise")


# ---- fallback ----
def test_fallback_no_op_under_budget():
    cart = [{"bom_item": "Wrap", "priority": "Essential", "line_total": 20.00, "relevance_score": 2}]
    new_cart, drops, total, ok = apply_budget_fallback(cart, 150.00)
    assert drops == [] and total == 20.00 and ok is True and len(new_cart) == 1


def test_fallback_drops_optional_in_policy_order():
    cart = [
        {"bom_item": "Wrap", "priority": "Essential", "line_total": 29.98, "relevance_score": 2},
        {"bom_item": "Tool Kit", "priority": "Optional", "line_total": 7.82, "relevance_score": 3},
        {"bom_item": "Sealant", "priority": "Essential", "line_total": 2.50, "relevance_score": 1},
        {"bom_item": "Degreaser", "priority": "Optional", "line_total": 4.25, "relevance_score": 4},
    ]
    new_cart, drops, total, ok = apply_budget_fallback(cart, 40.00)
    assert ok is True
    assert total == 36.73
    assert [d["bom_item"] for d in drops] == ["Tool Kit"]
    assert total == round(sum(r["line_total"] for r in new_cart), 2)  # invariant, not magic number


def test_fallback_still_over_budget():
    cart = [
        {"bom_item": "Wrap", "priority": "Essential", "line_total": 100.00, "relevance_score": 2},
        {"bom_item": "Tool Kit", "priority": "Optional", "line_total": 10.00, "relevance_score": 3},
    ]
    new_cart, drops, total, ok = apply_budget_fallback(cart, 50.00)
    assert ok is False
    assert len(new_cart) == 1
    # Invariant: final total equals the sum of the kept line totals.
    assert total == round(sum(r["line_total"] for r in new_cart), 2)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed")
