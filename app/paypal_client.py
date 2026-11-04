"""PayPal Orders v2 client with two interchangeable backends.

- SandboxPayPal: the real PayPal REST API (api-m.sandbox.paypal.com).
- SimPayPal: an in-memory imitation with the same states (CREATED -> APPROVED -> COMPLETED),
  so the demo and tests run with no keys and no network.
"""
from __future__ import annotations

import base64
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

import httpx

from .models import PricedLine

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"


class PayPalError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None, details: Any = None):
        super().__init__(message)
        self.status = status
        self.details = details


def _money(cents: int) -> dict[str, str]:
    return {"currency_code": "USD", "value": f"{cents / 100:.2f}"}


def build_order_body(entry_id: int, lines: list[PricedLine], total_cents: int,
                     return_url: Optional[str] = None, cancel_url: Optional[str] = None) -> dict[str, Any]:
    """Orders v2 body. Amounts come from the policy engine's priced lines (catalogue prices)."""
    if sum(l.line_cents for l in lines) != total_cents:
        raise ValueError("line totals do not add up to the order total")
    body: dict[str, Any] = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "reference_id": f"agentpay-{entry_id}",
            "custom_id": str(entry_id),
            "description": "AgentPay Guard purchase"[:127],
            "amount": {**_money(total_cents), "breakdown": {"item_total": _money(total_cents)}},
            "items": [{
                "name": l.name[:127],
                "sku": l.product_id[:127],
                "quantity": str(l.quantity),
                "unit_amount": _money(l.unit_cents),
                "category": "PHYSICAL_GOODS",
            } for l in lines],
        }],
    }
    if return_url and cancel_url:
        body["payment_source"] = {"paypal": {"experience_context": {
            "brand_name": "AgentPay Guard", "user_action": "PAY_NOW",
            "shipping_preference": "NO_SHIPPING",
            "return_url": return_url, "cancel_url": cancel_url,
        }}}
    return body


def approve_link(order: dict[str, Any]) -> Optional[str]:
    for link in order.get("links", []):
        if link.get("rel") in ("payer-action", "approve"):
            return link.get("href")
    return None


def capture_id(order: dict[str, Any]) -> Optional[str]:
    try:
        return order["purchase_units"][0]["payments"]["captures"][0]["id"]
    except (KeyError, IndexError, TypeError):
        return None


class PayPalBackend(Protocol):
    mode: str

    def create_order(self, body: dict[str, Any], request_id: str) -> dict[str, Any]: ...
    def get_order(self, order_id: str) -> dict[str, Any]: ...
    def capture_order(self, order_id: str, request_id: str) -> dict[str, Any]: ...
    def refund_capture(self, capture_id: str, request_id: str) -> dict[str, Any]: ...


# ------------------------------------------------------------------ sandbox

class SandboxPayPal:
    mode = "sandbox"

    def __init__(self, client_id: str, client_secret: str, base_url: str = SANDBOX_BASE,
                 transport: Optional[httpx.BaseTransport] = None, timeout: float = 30.0):
        if not client_id or not client_secret:
            raise PayPalError("PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are required for sandbox mode")
        self.client_id = client_id
        self._secret = client_secret
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(timeout=timeout, transport=transport)
        self._token: Optional[str] = None
        self._token_exp = 0.0
        self._lock = threading.Lock()

    def _access_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token_exp - 60:
                return self._token
            basic = base64.b64encode(f"{self.client_id}:{self._secret}".encode()).decode()
            r = self._http.post(f"{self.base_url}/v1/oauth2/token",
                                data={"grant_type": "client_credentials"},
                                headers={"Authorization": f"Basic {basic}"})
            if r.status_code != 200:
                raise PayPalError("PayPal authentication failed", r.status_code, _safe_json(r))
            data = r.json()
            self._token = data["access_token"]
            self._token_exp = time.time() + int(data.get("expires_in", 3000))
            return self._token

    def _call(self, method: str, path: str, request_id: Optional[str] = None,
              json_body: Optional[dict] = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._access_token()}", "Content-Type": "application/json",
                   "Prefer": "return=representation"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id  # idempotency
        r = self._http.request(method, f"{self.base_url}{path}", headers=headers, json=json_body)
        if r.status_code >= 400:
            d = _safe_json(r)
            name = d.get("name", "") if isinstance(d, dict) else ""
            raise PayPalError(f"PayPal {method} {path} failed: {r.status_code} {name}".strip(), r.status_code, d)
        return _safe_json(r) or {}

    def create_order(self, body, request_id):
        return self._call("POST", "/v2/checkout/orders", request_id, body)

    def get_order(self, order_id):
        return self._call("GET", f"/v2/checkout/orders/{order_id}")

    def capture_order(self, order_id, request_id):
        return self._call("POST", f"/v2/checkout/orders/{order_id}/capture", request_id, {})

    def refund_capture(self, capture_id, request_id):
        return self._call("POST", f"/v2/payments/captures/{capture_id}/refund", request_id, {})


def _safe_json(r: httpx.Response) -> Any:
    try:
        return r.json()
    except ValueError:
        return {"raw": r.text[:500]}


# ---------------------------------------------------------------- simulator

@dataclass
class _SimOrder:
    id: str
    body: dict[str, Any]
    status: str = "CREATED"
    capture_id: Optional[str] = None
    refunded: bool = False
    created: float = field(default_factory=time.time)


class SimPayPal:
    """Behaves like Orders v2 closely enough for demos: same states, IDs, links and errors."""
    mode = "sim"

    def __init__(self, approve_base_url: str = "/sim/paypal/approve"):
        self.approve_base_url = approve_base_url
        self._orders: dict[str, _SimOrder] = {}
        self._by_request: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _new_id(n: int = 17) -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789"
        return "".join(secrets.choice(alphabet) for _ in range(n))

    def _view(self, o: _SimOrder) -> dict[str, Any]:
        pu = dict(o.body["purchase_units"][0])
        if o.capture_id:
            pu["payments"] = {"captures": [{
                "id": o.capture_id, "status": "REFUNDED" if o.refunded else "COMPLETED",
                "amount": pu["amount"]}]}
        return {
            "id": o.id, "status": o.status, "intent": "CAPTURE", "purchase_units": [pu],
            "links": [
                {"rel": "self", "href": f"/v2/checkout/orders/{o.id}", "method": "GET"},
                {"rel": "approve", "href": f"{self.approve_base_url}/{o.id}", "method": "GET"},
                {"rel": "capture", "href": f"/v2/checkout/orders/{o.id}/capture", "method": "POST"},
            ],
            "simulated": True,
        }

    def create_order(self, body, request_id):
        with self._lock:
            if request_id in self._by_request:
                return self._by_request[request_id]
            if body.get("intent") != "CAPTURE" or not body.get("purchase_units"):
                raise PayPalError("INVALID_REQUEST", 400)
            o = _SimOrder(id=self._new_id(), body=body)
            self._orders[o.id] = o
            view = self._view(o)
            self._by_request[request_id] = view
            return view

    def approve(self, order_id: str) -> dict[str, Any]:
        """Stands in for the buyer clicking 'Pay' in the PayPal popup."""
        with self._lock:
            o = self._get(order_id)
            if o.status == "CREATED":
                o.status = "APPROVED"
            return self._view(o)

    def get_order(self, order_id):
        with self._lock:
            return self._view(self._get(order_id))

    def capture_order(self, order_id, request_id):
        with self._lock:
            key = f"cap:{request_id}"
            if key in self._by_request:
                return self._by_request[key]
            o = self._get(order_id)
            if o.status == "COMPLETED":
                raise PayPalError("ORDER_ALREADY_CAPTURED", 422)
            if o.status != "APPROVED":
                raise PayPalError("ORDER_NOT_APPROVED", 422)
            o.status = "COMPLETED"
            o.capture_id = self._new_id()
            view = self._view(o)
            self._by_request[key] = view
            return view

    def refund_capture(self, capture_id, request_id):
        with self._lock:
            o = next((x for x in self._orders.values() if x.capture_id == capture_id), None)
            if o is None:
                raise PayPalError("RESOURCE_NOT_FOUND", 404)
            if o.refunded:
                raise PayPalError("CAPTURE_FULLY_REFUNDED", 422)
            o.refunded = True
            return {"id": self._new_id(), "status": "COMPLETED"}

    def _get(self, order_id: str) -> _SimOrder:
        o = self._orders.get(order_id)
        if o is None:
            raise PayPalError("RESOURCE_NOT_FOUND", 404)
        return o


def make_backend(mode: str, client_id: str = "", client_secret: str = "") -> PayPalBackend:
    if mode == "sandbox":
        return SandboxPayPal(client_id, client_secret)
    if mode == "sim":
        return SimPayPal()
    raise ValueError(f"PAYPAL_MODE must be 'sim' or 'sandbox', got {mode!r}")


def new_request_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"
