from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.ledger import Ledger
from app.llm import LLMClient
from app.main import create_app
from app.paypal_client import PayPalError, SimPayPal

NOON_IST = datetime(2026, 11, 4, 6, 30, tzinfo=timezone.utc)


def make_client(autopay=False, paypal=None):
    app = create_app(settings=get_settings(), llm=LLMClient("", ""), paypal=paypal or SimPayPal(),
                     ledger=Ledger(":memory:"), now_fn=lambda: NOON_IST, autopay=autopay)
    return TestClient(app)


@pytest.fixture
def client():
    return make_client()


def chat(client, msg):
    r = client.post("/api/chat", json={"message": msg})
    assert r.status_code == 200, r.text
    return r.json()


def pay_via_paypal(client, entry_id):
    o = client.post(f"/api/entries/{entry_id}/paypal/order").json()
    assert o["approve_url"].endswith(o["order_id"])
    client.post(f"/api/sim/paypal/{o['order_id']}/approve")
    return client.post(f"/api/entries/{entry_id}/paypal/capture", json={"order_id": o["order_id"]})


# ------------------------------------------------------------------ meta

def test_health_and_config(client):
    h = client.get("/api/health").json()
    assert h["ok"] and h["paypal_mode"] == "sim" and h["llm"].startswith("heuristic")
    assert client.get("/api/config").json()["paypal_client_id"] == ""  # never leaked in sim


def test_catalog_and_default_policy(client):
    assert len(client.get("/api/catalog").json()) == 30
    p = client.get("/api/policy").json()
    assert p["policy"]["per_item_max_cents"] == 5000 and "Weekly budget" in p["rules_text"]


# ----------------------------------------------------------- rules flow

def test_compile_then_save_policy(client):
    r = client.post("/api/rules/compile", json={"text": "Max $10 per item. Weekly budget $30."}).json()
    assert r["source"] == "heuristic" and r["policy"]["per_item_max_cents"] == 1000
    # compile does NOT save; user must confirm
    assert client.get("/api/policy").json()["policy"]["per_item_max_cents"] == 5000
    saved = client.put("/api/policy", json={"policy": r["policy"], "rules_text": "x"})
    assert saved.status_code == 200
    assert client.get("/api/policy").json()["policy"]["per_item_max_cents"] == 1000


def test_invalid_policy_rejected(client):
    r = client.put("/api/policy", json={"policy": {"per_item_max_cents": -5}})
    assert r.status_code == 422


# ------------------------------------------------------------- demo story

def test_story_approve_pay_hold_block_refund(client):
    # 1) small order -> APPROVE -> pay through PayPal (sim)
    a = chat(client, "Buy me a USB-C cable and a notebook")
    assert a["entry"]["outcome"] == "APPROVE" and a["entry"]["status"] == "approved"
    paid = pay_via_paypal(client, a["entry"]["id"]).json()
    assert paid["status"] == "paid" and paid["paypal_capture_id"]

    # 2) over ask-threshold -> HOLD -> human approves -> pay
    h = chat(client, "get a desk lamp")
    assert h["entry"]["outcome"] == "HOLD" and h["entry"]["status"] == "held"
    with_pay = client.post(f"/api/entries/{h['entry']['id']}/paypal/order")
    assert with_pay.status_code == 409  # can't pay before approval
    assert client.post(f"/api/entries/{h['entry']['id']}/approve").json()["status"] == "approved"
    assert pay_via_paypal(client, h["entry"]["id"]).json()["status"] == "paid"

    # 3) too expensive -> BLOCK, can never be paid
    b = chat(client, "buy noise cancelling headphones")
    assert b["entry"]["outcome"] == "BLOCK" and "per-item max" in b["entry"]["explanation"]
    assert client.post(f"/api/entries/{b['entry']['id']}/paypal/order").status_code == 409

    # budget reflects paid orders only: 14.49 + 27.99
    week = client.get("/api/budget").json()[0]
    assert week["spent_cents"] == 1449 + 2799 and week["left_cents"] == 15000 - 4248

    # 4) refund the first order -> budget released
    assert client.post(f"/api/entries/{a['entry']['id']}/refund").json()["status"] == "cancelled"
    assert client.get("/api/budget").json()[0]["spent_cents"] == 2799

    statuses = [e["status"] for e in client.get("/api/ledger").json()]
    assert statuses == ["blocked", "paid", "cancelled"]


def test_budget_exhaustion_blocks_and_hold_recheck(client):
    client.put("/api/policy", json={"policy": {"budgets": [{"period": "week", "limit_cents": 4000}],
                                               "ask_above_cents": 2000}})
    h = chat(client, "buy a desk lamp")             # 27.99 -> HOLD
    a = chat(client, "buy a power bank")            # 24.99 -> HOLD too
    assert h["entry"]["status"] == a["entry"]["status"] == "held"
    assert client.post(f"/api/entries/{h['entry']['id']}/approve").status_code == 200
    # second approval would exceed the budget now -> rejected on re-check
    r = client.post(f"/api/entries/{a['entry']['id']}/approve")
    assert r.status_code == 409 and "budget" in r.json()["detail"].lower()
    assert client.get(f"/api/entries/{a['entry']['id']}").json()["status"] == "rejected"


def test_reject_hold(client):
    h = chat(client, "get a desk lamp")
    assert client.post(f"/api/entries/{h['entry']['id']}/reject").json()["status"] == "rejected"
    assert client.post(f"/api/entries/{h['entry']['id']}/approve").status_code == 409


def test_capture_requires_matching_order(client):
    a = chat(client, "buy gel pens")
    client.post(f"/api/entries/{a['entry']['id']}/paypal/order")
    r = client.post(f"/api/entries/{a['entry']['id']}/paypal/capture", json={"order_id": "SOMEONEELSE"})
    assert r.status_code == 409


def test_capture_before_buyer_approval_marks_failed_then_retry(client):
    a = chat(client, "buy gel pens")
    eid = a["entry"]["id"]
    o = client.post(f"/api/entries/{eid}/paypal/order").json()
    r = client.post(f"/api/entries/{eid}/paypal/capture", json={"order_id": o["order_id"]})
    assert r.status_code == 502 and client.get(f"/api/entries/{eid}").json()["status"] == "failed"
    assert pay_via_paypal(client, eid).json()["status"] == "paid"   # retry works


def test_chat_with_no_match(client):
    r = chat(client, "buy a yacht")
    assert r["entry"] is None


def test_unknown_entry_404(client):
    assert client.get("/api/entries/999").status_code == 404


# ------------------------------------------------------------- autopay

def test_autopay_pays_approved_intents_immediately():
    c = make_client(autopay=True)
    a = chat(c, "buy gel pens")
    assert a["entry"]["status"] == "paid" and a["entry"]["paypal_order_id"]
    h = chat(c, "buy a desk lamp")
    assert h["entry"]["status"] == "held"              # holds still wait for the human
    assert c.post(f"/api/entries/{h['entry']['id']}/approve").json()["status"] == "paid"


def test_autopay_failure_leaves_entry_approved():
    class Broken(SimPayPal):
        def create_order(self, body, request_id):
            raise PayPalError("down", 503)
    c = make_client(autopay=True, paypal=Broken())
    a = chat(c, "buy gel pens")
    assert a["entry"]["status"] == "approved"


# ----------------------------------------------------------- agent API

def test_external_agent_intent_api_with_key(monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", "s3cret")
    c = make_client()
    body = {"items": [{"product_id": "esp32-board", "quantity": 2}], "agent": "autobasket", "reason": "restock"}
    assert c.post("/api/intents", json=body).status_code == 401
    r = c.post("/api/intents", json=body, headers={"X-Agent-Key": "s3cret"})
    assert r.status_code == 200 and r.json()["agent"] == "autobasket" and r.json()["total_cents"] == 1300


def test_external_agent_cannot_send_price(client):
    body = {"items": [{"product_id": "anc-headphones", "quantity": 1, "price_cents": 1}]}
    r = client.post("/api/intents", json=body).json()
    assert r["total_cents"] == 29900 and r["outcome"] == "BLOCK"


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
