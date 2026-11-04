import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.ledger import Ledger
from app.llm import LLMClient
from app.main import create_app
from app.paypal_client import SandboxPayPal, SimPayPal
from tests.test_api import NOON_IST


def client_with(paypal):
    app = create_app(settings=get_settings(), llm=LLMClient("", ""), paypal=paypal, ledger=Ledger(":memory:"),
                     now_fn=lambda: NOON_IST, autopay=False)
    return TestClient(app), app.state.svc


def capture_event(entry_id, value, order_id, etype="PAYMENT.CAPTURE.COMPLETED"):
    return {"event_type": etype, "resource": {
        "id": "CAPWEBHOOK1", "custom_id": str(entry_id), "amount": {"currency_code": "USD", "value": value},
        "supplementary_data": {"related_ids": {"order_id": order_id}}}}


def approved_with_order(c):
    e = c.post("/api/chat", json={"message": "buy gel pens"}).json()["entry"]
    o = c.post(f"/api/entries/{e['id']}/paypal/order").json()
    return e["id"], o["order_id"]


def test_webhook_marks_paid_when_browser_never_captured():
    c, _ = client_with(SimPayPal())
    eid, oid = approved_with_order(c)
    r = c.post("/api/paypal/webhook", json=capture_event(eid, "3.50", oid)).json()
    assert r == {"handled": True, "entry_id": eid, "status": "paid"}
    assert c.get(f"/api/entries/{eid}").json()["paypal_capture_id"] == "CAPWEBHOOK1"
    # replay is harmless
    assert c.post("/api/paypal/webhook", json=capture_event(eid, "3.50", oid)).json()["note"] == "already paid"


@pytest.mark.parametrize("mutate,reason", [
    (lambda ev: ev["resource"]["amount"].update(value="0.01"), "amount mismatch"),
    (lambda ev: ev["resource"]["supplementary_data"]["related_ids"].update(order_id="OTHER"), "order id mismatch"),
    (lambda ev: ev["resource"].update(custom_id="999"), "no matching purchase"),
    (lambda ev: ev.update(event_type="CUSTOMER.DISPUTE.CREATED"), "ignored"),
])
def test_webhook_rejects_inconsistent_events(mutate, reason):
    c, _ = client_with(SimPayPal())
    eid, oid = approved_with_order(c)
    ev = capture_event(eid, "3.50", oid)
    mutate(ev)
    r = c.post("/api/paypal/webhook", json=ev).json()
    assert r["handled"] is False and reason in r["reason"]
    assert c.get(f"/api/entries/{eid}").json()["status"] == "approved"


def test_webhook_cannot_pay_a_blocked_purchase():
    c, _ = client_with(SimPayPal())
    e = c.post("/api/chat", json={"message": "buy noise cancelling headphones"}).json()["entry"]
    r = c.post("/api/paypal/webhook", json=capture_event(e["id"], "299.00", None)).json()
    assert r["handled"] is False and c.get(f"/api/entries/{e['id']}").json()["status"] == "blocked"


def test_webhook_refund_event():
    c, _ = client_with(SimPayPal())
    eid, oid = approved_with_order(c)
    c.post(f"/api/sim/paypal/{oid}/approve")
    c.post(f"/api/entries/{eid}/paypal/capture", json={"order_id": oid})
    r = c.post("/api/paypal/webhook", json=capture_event(eid, "3.50", oid, "PAYMENT.CAPTURE.REFUNDED")).json()
    assert r["status"] == "cancelled"


def _sandbox(verdict):
    def handler(req: httpx.Request):
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "T", "expires_in": 3600})
        if req.url.path == "/v1/notifications/verify-webhook-signature":
            body = json.loads(req.content)
            assert body["webhook_id"] == "WH-1" and body["transmission_id"] == "tx"
            return httpx.Response(200, json={"verification_status": verdict})
        return httpx.Response(404)
    return SandboxPayPal("cid", "sec", transport=httpx.MockTransport(handler))


SIG_HEADERS = {"PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/cert",
               "PAYPAL-TRANSMISSION-ID": "tx", "PAYPAL-TRANSMISSION-SIG": "sig", "PAYPAL-TRANSMISSION-TIME": "now"}


def test_sandbox_webhook_requires_configured_id(monkeypatch):
    monkeypatch.delenv("PAYPAL_WEBHOOK_ID", raising=False)
    c, _ = client_with(_sandbox("SUCCESS"))
    assert c.post("/api/paypal/webhook", json={"event_type": "X"}, headers=SIG_HEADERS).status_code == 503


def test_sandbox_webhook_signature_checked(monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WH-1")
    ok, _ = client_with(_sandbox("SUCCESS"))
    assert ok.post("/api/paypal/webhook", json={"event_type": "X"}, headers=SIG_HEADERS).status_code == 200
    bad, _ = client_with(_sandbox("FAILURE"))
    assert bad.post("/api/paypal/webhook", json={"event_type": "X"}, headers=SIG_HEADERS).status_code == 401
    assert bad.post("/api/paypal/webhook", json={"event_type": "X"}).status_code == 401  # no headers
