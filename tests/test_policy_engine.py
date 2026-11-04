from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models import IntentItem, Outcome, Policy, PurchaseIntent
from app.policy_engine import default_policy, evaluate

NO_SPEND = {"day": 0, "week": 0, "month": 0}


def intent(*pairs, agent="test"):
    return PurchaseIntent(items=[IntentItem(product_id=p, quantity=q) for p, q in pairs], agent=agent)


def rules(decision):
    return {h.rule for h in decision.hits}


# --- pricing & resolution -------------------------------------------------

def test_prices_come_from_catalog_not_intent(catalog, now):
    d = evaluate(intent(("usb-c-cable", 2), ("a5-notebook", 1)), Policy(), catalog, NO_SPEND, now)
    assert d.outcome == Outcome.APPROVE
    assert d.total_cents == 999 * 2 + 450
    assert [l.product_id for l in d.lines] == ["usb-c-cable", "a5-notebook"]


def test_duplicate_lines_are_merged(catalog, now):
    d = evaluate(intent(("gel-pens", 1), ("gel-pens", 2)), Policy(), catalog, NO_SPEND, now)
    assert len(d.lines) == 1 and d.lines[0].quantity == 3
    assert d.total_cents == 350 * 3


def test_unknown_product_blocks(catalog, now):
    d = evaluate(intent(("free-iphone", 1)), Policy(), catalog, NO_SPEND, now)
    assert d.outcome == Outcome.BLOCK
    assert "known_products" in rules(d)


def test_empty_intent_blocks(catalog, now):
    d = evaluate(PurchaseIntent(items=[]), Policy(), catalog, NO_SPEND, now)
    assert d.outcome == Outcome.BLOCK and "non_empty" in rules(d)


def test_quantity_must_be_positive():
    with pytest.raises(ValidationError):
        IntentItem(product_id="usb-c-cable", quantity=0)


# --- individual rules -----------------------------------------------------

def test_per_item_max(catalog, now):
    p = Policy(per_item_max_cents=5000)
    d = evaluate(intent(("anc-headphones", 1)), p, catalog, NO_SPEND, now)
    assert d.outcome == Outcome.BLOCK and "per_item_max" in rules(d)
    ok = evaluate(intent(("usb-c-cable", 1)), p, catalog, NO_SPEND, now)
    assert ok.outcome == Outcome.APPROVE


def test_per_item_max_uses_unit_price_not_line_total(catalog, now):
    # 5 x $9.99 = $49.95 total, but each item is under $10
    d = evaluate(intent(("usb-c-cable", 5)), Policy(per_item_max_cents=1000), catalog, NO_SPEND, now)
    assert d.outcome == Outcome.APPROVE


def test_per_order_max(catalog, now):
    d = evaluate(intent(("mech-keyboard", 1), ("wireless-mouse", 1)),
                 Policy(per_order_max_cents=7000), catalog, NO_SPEND, now)
    assert d.outcome == Outcome.BLOCK and "per_order_max" in rules(d)


def test_order_exactly_at_limit_is_allowed(catalog, now):
    d = evaluate(intent(("usb-c-cable", 1)), Policy(per_order_max_cents=999), catalog, NO_SPEND, now)
    assert d.outcome == Outcome.APPROVE


def test_blocked_category_is_case_insensitive(catalog, now):
    d = evaluate(intent(("controller", 1)), Policy(blocked_categories=["Gaming"]), catalog, NO_SPEND, now)
    assert d.outcome == Outcome.BLOCK and "categories" in rules(d)


def test_allowed_categories_whitelist(catalog, now):
    p = Policy(allowed_categories=["stationery", "electronics"])
    assert evaluate(intent(("gel-pens", 1)), p, catalog, NO_SPEND, now).outcome == Outcome.APPROVE
    d = evaluate(intent(("coffee-beans", 1)), p, catalog, NO_SPEND, now)
    assert d.outcome == Outcome.BLOCK and "categories" in rules(d)


def test_merchant_block_and_whitelist(catalog, now):
    d = evaluate(intent(("esp32-board", 1)), Policy(blocked_merchants=["circuithub"]), catalog, NO_SPEND, now)
    assert d.outcome == Outcome.BLOCK and "merchants" in rules(d)
    d2 = evaluate(intent(("esp32-board", 1)), Policy(allowed_merchants=["VoltMart"]), catalog, NO_SPEND, now)
    assert d2.outcome == Outcome.BLOCK


def test_max_quantity(catalog, now):
    d = evaluate(intent(("oat-milk", 6)), Policy(max_quantity_per_item=5), catalog, NO_SPEND, now)
    assert d.outcome == Outcome.BLOCK and "max_quantity" in rules(d)


def test_budget_counts_previous_spend(catalog, now):
    p = Policy(budgets=[{"period": "week", "limit_cents": 5000}])
    assert evaluate(intent(("power-bank", 1)), p, catalog, {"week": 2000}, now).outcome == Outcome.APPROVE
    d = evaluate(intent(("power-bank", 1)), p, catalog, {"week": 3000}, now)  # 3000+2499 > 5000
    assert d.outcome == Outcome.BLOCK and "budget_week" in rules(d)
    assert "$20.00 left" in d.hits[0].detail


def test_multiple_budgets_each_checked(catalog, now):
    p = Policy(budgets=[{"period": "day", "limit_cents": 1000}, {"period": "month", "limit_cents": 100000}])
    d = evaluate(intent(("wireless-mouse", 1)), p, catalog, NO_SPEND, now)
    assert rules(d) == {"budget_day"}


def test_duplicate_budget_period_rejected():
    with pytest.raises(ValidationError):
        Policy(budgets=[{"period": "week", "limit_cents": 1}, {"period": "week", "limit_cents": 2}])


def test_ask_above_holds(catalog, now):
    d = evaluate(intent(("desk-lamp", 1)), Policy(ask_above_cents=2000), catalog, NO_SPEND, now)
    assert d.outcome == Outcome.HOLD and rules(d) == {"ask_above"}


def test_block_wins_over_hold(catalog, now):
    p = Policy(ask_above_cents=1000, blocked_categories=["gaming"])
    d = evaluate(intent(("controller", 1)), p, catalog, NO_SPEND, now)
    assert d.outcome == Outcome.BLOCK
    assert {"ask_above", "categories"} <= rules(d)


# --- quiet hours ----------------------------------------------------------

def test_quiet_hours_across_midnight(catalog):
    p = Policy(quiet_hours={"start": "23:00", "end": "07:00"}, timezone="Asia/Kolkata")
    late = datetime(2026, 11, 4, 18, 0, tzinfo=timezone.utc)   # 23:30 IST
    early = datetime(2026, 11, 4, 1, 0, tzinfo=timezone.utc)   # 06:30 IST
    day = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)     # 07:30 IST
    i = intent(("gel-pens", 1))
    assert evaluate(i, p, catalog, NO_SPEND, late).outcome == Outcome.BLOCK
    assert evaluate(i, p, catalog, NO_SPEND, early).outcome == Outcome.BLOCK
    assert evaluate(i, p, catalog, NO_SPEND, day).outcome == Outcome.APPROVE


def test_bad_quiet_hours_format_rejected():
    with pytest.raises(ValidationError):
        Policy(quiet_hours={"start": "11pm", "end": "07:00"})


# --- transparency & default policy ---------------------------------------

def test_checked_rules_lists_everything_evaluated(catalog, now):
    d = evaluate(intent(("gel-pens", 1)), default_policy(), catalog, NO_SPEND, now)
    for r in ["known_products", "categories", "merchants", "per_item_max", "per_order_max",
              "budget_week", "ask_above", "max_quantity"]:
        assert r in d.checked_rules


@pytest.mark.parametrize("items,expected", [
    ([("usb-c-cable", 1), ("a5-notebook", 1)], Outcome.APPROVE),   # $14.49
    ([("desk-lamp", 1)], Outcome.HOLD),                              # $27.99 > $20 ask
    ([("anc-headphones", 1)], Outcome.BLOCK),                        # > $50 per item
    ([("game-giftcard", 1)], Outcome.BLOCK),                         # gaming blocked
])
def test_demo_story_with_default_policy(catalog, now, items, expected):
    assert evaluate(intent(*items), default_policy(), catalog, NO_SPEND, now).outcome == expected
