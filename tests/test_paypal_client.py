import json

import httpx
import pytest

from app.models import PricedLine
from app.paypal_client import (PayPalError, SandboxPayPal, SimPayPal, approve_link, build_order_body,
                               capture_id, make_backend)

LINES = [
    PricedLine(product_id="usb-c-cable", name="USB-C cable", category="electronics", merchant="VoltMart",
               unit_cents=999, quantity=2, line_cents=1998),
    PricedLine(product_id="a5-notebook", name="A5 notebook", category="stationery", merchant="PaperTrail",
               unit_cents=450, quantity=1, line_cents=450),
]


def test_order_body_amounts_and_items():
    b = build_order_body(7, LINES, 2448)
    pu = b["purchase_units"][0]
    assert b["intent"] == "CAPTURE"
    assert pu["amount"] == {"currency_code": "USD", "value": "24.48",
                            "breakdown": {"item_total": {"currency_code": "USD", "value": "24.48"}}}
    assert pu["items"][0]["unit_amount"]["value"] == "9.99" and pu["items"][0]["quantity"] == "2"
    assert pu["custom_id"] == "7"
    assert "payment_source" not in b


def test_order_body_rejects_mismatched_total():
    with pytest.raises(ValueError):
        build_order_body(1, LINES, 9999)


def test_order_body_with_redirect_urls():
    b = build_order_body(1, LINES, 2448, "http://x/ok", "http://x/cancel")
    ctx = b["payment_source"]["paypal"]["experience_context"]
    assert ctx["return_url"] == "http://x/ok" and ctx["user_action"] == "PAY_NOW"


# -------------------------------------------------------------- simulator

def test_sim_full_lifecycle():
    pp = SimPayPal()
    o = pp.create_order(build_order_body(1, LINES, 2448), "req-1")
    assert o["status"] == "CREATED" and approve_link(o).endswith(o["id"])
    with pytest.raises(PayPalError, match="NOT_APPROVED"):
        pp.capture_order(o["id"], "cap-0")
    pp.approve(o["id"])
    c = pp.capture_order(o["id"], "cap-1")
    assert c["status"] == "COMPLETED" and capture_id(c)
    with pytest.raises(PayPalError, match="ALREADY_CAPTURED"):
        pp.capture_order(o["id"], "cap-2")
    assert pp.refund_capture(capture_id(c), "ref-1")["status"] == "COMPLETED"
    with pytest.raises(PayPalError):
        pp.refund_capture(capture_id(c), "ref-2")


def test_sim_is_idempotent_per_request_id():
    pp = SimPayPal()
    a = pp.create_order(build_order_body(1, LINES, 2448), "same")
    b = pp.create_order(build_order_body(1, LINES, 2448), "same")
    assert a["id"] == b["id"]


def test_sim_unknown_order():
    with pytest.raises(PayPalError) as e:
        SimPayPal().get_order("NOPE")
    assert e.value.status == 404


def test_make_backend_modes():
    assert make_backend("sim").mode == "sim"
    with pytest.raises(PayPalError):
        make_backend("sandbox")  # no keys
    with pytest.raises(ValueError):
        make_backend("live")


# ---------------------------------------------------------------- sandbox

def _sandbox(handler):
    return SandboxPayPal("cid", "secret", transport=httpx.MockTransport(handler))


def test_sandbox_auth_create_capture_refund_requests():
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.method, req.url.path, dict(req.headers)))
        if req.url.path == "/v1/oauth2/token":
            assert req.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "TOK", "expires_in": 32400})
        assert req.headers["authorization"] == "Bearer TOK"
        if req.url.path == "/v2/checkout/orders":
            body = json.loads(req.content)
            assert body["purchase_units"][0]["amount"]["value"] == "24.48"
            return httpx.Response(201, json={"id": "5O190127TN364715T", "status": "CREATED",
                                             "links": [{"rel": "approve", "href": "https://sandbox.paypal.com/checkoutnow?token=5O19"}]})
        if req.url.path.endswith("/capture"):
            return httpx.Response(201, json={"id": "5O190127TN364715T", "status": "COMPLETED",
                                             "purchase_units": [{"payments": {"captures": [{"id": "3C679366HH908993F"}]}}]})
        if req.url.path.endswith("/refund"):
            return httpx.Response(201, json={"id": "1JU08902781691411", "status": "COMPLETED"})
        return httpx.Response(404)

    pp = _sandbox(handler)
    o = pp.create_order(build_order_body(1, LINES, 2448), "req-1")
    assert approve_link(o).startswith("https://sandbox.paypal.com")
    c = pp.capture_order(o["id"], "cap-1")
    assert capture_id(c) == "3C679366HH908993F"
    assert pp.refund_capture("3C679366HH908993F", "ref-1")["status"] == "COMPLETED"
    # token fetched once and reused; idempotency header sent on writes
    assert [p for _, p, _ in seen].count("/v1/oauth2/token") == 1
    assert seen[1][2]["paypal-request-id"] == "req-1"


def test_sandbox_errors_are_wrapped():
    def handler(req):
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "T", "expires_in": 100})
        return httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "ORDER_NOT_APPROVED"}]})
    with pytest.raises(PayPalError) as e:
        _sandbox(handler).capture_order("X", "r")
    assert e.value.status == 422 and "UNPROCESSABLE_ENTITY" in str(e.value)


def test_sandbox_bad_credentials():
    pp = _sandbox(lambda req: httpx.Response(401, json={"error": "invalid_client"}))
    with pytest.raises(PayPalError, match="authentication failed"):
        pp.get_order("X")
