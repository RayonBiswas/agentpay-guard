from datetime import datetime, timedelta, timezone

import pytest

from app.ledger import InvalidTransition
from app.models import IntentItem, Outcome, Policy, PurchaseIntent
from app.periods import in_quiet_hours, period_start
from app.policy_engine import evaluate

TZ = "Asia/Kolkata"


def _record(ledger, catalog, now, pid, qty=1, policy=None):
    i = PurchaseIntent(items=[IntentItem(product_id=pid, quantity=qty)], agent="t")
    d = evaluate(i, policy or Policy(), catalog, {}, now)
    return ledger.record(i, d, now=now), d


# --- periods --------------------------------------------------------------

def test_period_start_uses_local_timezone():
    # 4 Nov 2026 00:30 IST == 3 Nov 19:00 UTC; local day starts 3 Nov 18:30 UTC
    now = datetime(2026, 11, 3, 19, 0, tzinfo=timezone.utc)
    assert period_start("day", now, TZ) == datetime(2026, 11, 3, 18, 30, tzinfo=timezone.utc)


def test_week_starts_monday_and_month_on_first():
    now = datetime(2026, 11, 4, 6, 30, tzinfo=timezone.utc)  # Wed
    assert period_start("week", now, TZ) == datetime(2026, 11, 1, 18, 30, tzinfo=timezone.utc)  # Mon 2 Nov IST
    assert period_start("month", now, TZ) == datetime(2026, 10, 31, 18, 30, tzinfo=timezone.utc)


def test_period_start_requires_aware_datetime():
    with pytest.raises(ValueError):
        period_start("day", datetime(2026, 11, 4), TZ)


def test_quiet_hours_same_day_window():
    t = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)  # 13:30 IST
    assert in_quiet_hours(t, TZ, "13:00", "14:00")
    assert not in_quiet_hours(t, TZ, "14:00", "15:00")
    assert not in_quiet_hours(t, TZ, "10:00", "10:00")


# --- ledger ---------------------------------------------------------------

def test_record_and_initial_status(ledger, catalog, now):
    eid, d = _record(ledger, catalog, now, "usb-c-cable")
    e = ledger.get(eid)
    assert e["status"] == "approved" and e["outcome"] == "APPROVE"
    assert e["total_cents"] == 999
    assert e["decision"]["lines"][0]["name"].startswith("USB-C")


def test_hold_and_block_initial_status(ledger, catalog, now):
    hid, _ = _record(ledger, catalog, now, "desk-lamp", policy=Policy(ask_above_cents=1000))
    bid, _ = _record(ledger, catalog, now, "controller", policy=Policy(blocked_categories=["gaming"]))
    assert ledger.get(hid)["status"] == "held"
    assert ledger.get(bid)["status"] == "blocked"


def test_only_committed_spend_counts(ledger, catalog, now):
    _record(ledger, catalog, now, "usb-c-cable")                                        # approved 999
    hid, _ = _record(ledger, catalog, now, "desk-lamp", policy=Policy(ask_above_cents=1)) # held
    _record(ledger, catalog, now, "controller", policy=Policy(blocked_categories=["gaming"]))  # blocked
    assert ledger.spent_by_period(TZ, now)["week"] == 999
    ledger.set_status(hid, "approved")
    assert ledger.spent_by_period(TZ, now)["week"] == 999 + 2799


def test_spend_outside_window_ignored(ledger, catalog, now):
    last_month = now - timedelta(days=40)
    _record(ledger, catalog, last_month, "mech-keyboard")
    _record(ledger, catalog, now, "gel-pens")
    s = ledger.spent_by_period(TZ, now)
    assert s == {"day": 350, "week": 350, "month": 350}


def test_payment_lifecycle_and_paypal_ids(ledger, catalog, now):
    eid, _ = _record(ledger, catalog, now, "usb-c-cable")
    ledger.set_status(eid, "paid", paypal_order_id="ORDER123", paypal_capture_id="CAP456")
    e = ledger.get(eid)
    assert (e["status"], e["paypal_order_id"], e["paypal_capture_id"]) == ("paid", "ORDER123", "CAP456")
    ledger.set_status(eid, "cancelled")  # refund
    assert ledger.spent_by_period(TZ, now)["week"] == 0


@pytest.mark.parametrize("start_pid,policy,bad", [
    ("controller", Policy(blocked_categories=["gaming"]), "paid"),   # blocked can never be paid
    ("desk-lamp", Policy(ask_above_cents=1), "paid"),                # held must be approved first
])
def test_invalid_transitions_rejected(ledger, catalog, now, start_pid, policy, bad):
    eid, _ = _record(ledger, catalog, now, start_pid, policy=policy)
    with pytest.raises(InvalidTransition):
        ledger.set_status(eid, bad)


def test_list_filters_and_orders_newest_first(ledger, catalog, now):
    a, _ = _record(ledger, catalog, now, "gel-pens")
    b, _ = _record(ledger, catalog, now, "desk-lamp", policy=Policy(ask_above_cents=1))
    assert [e["id"] for e in ledger.list()] == [b, a]
    assert [e["id"] for e in ledger.list(status="held")] == [b]


def test_unknown_entry_raises(ledger):
    with pytest.raises(KeyError):
        ledger.get(999)
