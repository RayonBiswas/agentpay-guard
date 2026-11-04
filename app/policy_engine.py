"""Deterministic policy engine: the ONLY component that decides about money.

No LLM output reaches this function except as a PurchaseIntent (product IDs +
quantities). Prices always come from the catalogue.
"""
from __future__ import annotations

from datetime import datetime

from .catalog import Catalog
from .models import Decision, Outcome, Policy, PricedLine, PurchaseIntent, RuleHit
from .periods import in_quiet_hours


def usd(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def evaluate(
    intent: PurchaseIntent,
    policy: Policy,
    catalog: Catalog,
    spent: dict[str, int],
    now: datetime,
) -> Decision:
    """Return APPROVE / HOLD / BLOCK plus every rule that fired.

    spent: committed spend in the current window, e.g. {"day": 0, "week": 1200, "month": 5400}.
    Any BLOCK hit -> BLOCK. Otherwise any HOLD hit -> HOLD. Otherwise APPROVE.
    """
    hits: list[RuleHit] = []
    checked: list[str] = []

    def block(rule: str, detail: str) -> None:
        hits.append(RuleHit(rule=rule, effect=Outcome.BLOCK, detail=detail))

    def hold(rule: str, detail: str) -> None:
        hits.append(RuleHit(rule=rule, effect=Outcome.HOLD, detail=detail))

    # --- 1. Resolve items against the catalogue (prices from catalogue only) ---
    checked.append("known_products")
    lines: list[PricedLine] = []
    merged: dict[str, int] = {}
    for it in intent.items:
        merged[it.product_id] = merged.get(it.product_id, 0) + it.quantity
    for pid, qty in merged.items():
        p = catalog.get(pid)
        if p is None:
            block("known_products", f"Unknown product '{pid}' (not in catalogue)")
            continue
        lines.append(PricedLine(
            product_id=p.id, name=p.name, category=p.category, merchant=p.merchant,
            unit_cents=p.price_cents, quantity=qty, line_cents=p.price_cents * qty,
        ))
    total = sum(l.line_cents for l in lines)

    checked.append("non_empty")
    if not intent.items:
        block("non_empty", "Purchase intent has no items")

    # --- 2. Per-line rules ---
    checked.append("categories")
    checked.append("merchants")
    for l in lines:
        cat, mer = l.category.lower(), l.merchant.lower()
        if cat in policy.blocked_categories:
            block("categories", f"{l.name}: category '{l.category}' is blocked")
        elif policy.allowed_categories is not None and cat not in policy.allowed_categories:
            block("categories", f"{l.name}: category '{l.category}' is not in your allowed list")
        if mer in policy.blocked_merchants:
            block("merchants", f"{l.name}: merchant '{l.merchant}' is blocked")
        elif policy.allowed_merchants is not None and mer not in policy.allowed_merchants:
            block("merchants", f"{l.name}: merchant '{l.merchant}' is not in your allowed list")

    if policy.per_item_max_cents is not None:
        checked.append("per_item_max")
        for l in lines:
            if l.unit_cents > policy.per_item_max_cents:
                block("per_item_max",
                      f"{l.name} costs {usd(l.unit_cents)}, above your per-item max of {usd(policy.per_item_max_cents)}")

    if policy.max_quantity_per_item is not None:
        checked.append("max_quantity")
        for l in lines:
            if l.quantity > policy.max_quantity_per_item:
                block("max_quantity",
                      f"{l.name}: quantity {l.quantity} exceeds your max of {policy.max_quantity_per_item}")

    # --- 3. Order-level rules ---
    if policy.per_order_max_cents is not None:
        checked.append("per_order_max")
        if total > policy.per_order_max_cents:
            block("per_order_max",
                  f"Order total {usd(total)} is above your per-order max of {usd(policy.per_order_max_cents)}")

    for b in policy.budgets:
        rule = f"budget_{b.period}"
        checked.append(rule)
        used = max(0, int(spent.get(b.period, 0)))
        if used + total > b.limit_cents:
            left = max(0, b.limit_cents - used)
            block(rule,
                  f"{b.period.capitalize()} budget {usd(b.limit_cents)}: {usd(used)} already spent, "
                  f"{usd(left)} left, this order needs {usd(total)}")

    if policy.quiet_hours is not None:
        checked.append("quiet_hours")
        q = policy.quiet_hours
        if in_quiet_hours(now, policy.timezone, q.start, q.end):
            block("quiet_hours", f"No purchases between {q.start} and {q.end} ({policy.timezone})")

    # --- 4. Human-in-the-loop threshold ---
    if policy.ask_above_cents is not None:
        checked.append("ask_above")
        if total > policy.ask_above_cents:
            hold("ask_above",
                 f"Order total {usd(total)} is above your ask-me threshold of {usd(policy.ask_above_cents)}")

    if any(h.effect == Outcome.BLOCK for h in hits):
        outcome = Outcome.BLOCK
    elif any(h.effect == Outcome.HOLD for h in hits):
        outcome = Outcome.HOLD
    else:
        outcome = Outcome.APPROVE

    return Decision(outcome=outcome, total_cents=total, lines=lines, hits=hits, checked_rules=checked)


def default_policy(tz: str = "Asia/Kolkata") -> Policy:
    """Demo policy used until the user compiles their own rules."""
    return Policy(
        per_item_max_cents=5000,
        per_order_max_cents=10000,
        budgets=[{"period": "week", "limit_cents": 15000}],
        blocked_categories=["gaming"],
        ask_above_cents=2000,
        max_quantity_per_item=5,
        timezone=tz,
    )
