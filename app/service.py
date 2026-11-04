"""Service layer: wires the agent, policy engine, ledger and PayPal together.

Flow: intent -> evaluate (deterministic) -> record -> [hold approval] -> PayPal order -> capture -> paid
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .agent import AgentResult, explain, parse_request
from .catalog import Catalog
from .config import Settings
from .ledger import InvalidTransition, Ledger
from .llm import LLMClient
from .models import Decision, Outcome, Policy, PricedLine, PurchaseIntent
from .paypal_client import (PayPalBackend, PayPalError, SimPayPal, build_order_body, capture_id,
                            approve_link, new_request_id)
from .policy_engine import default_policy, evaluate
from .rule_compiler import CompileResult, compile_rules

DEFAULT_RULES_TEXT = ("Max $50 per item and $100 per order. Weekly budget $150. "
                      "Ask me before anything over $20. No gaming or gift cards. At most 5 of each item.")


class ServiceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class GuardService:
    def __init__(self, settings: Settings, catalog: Catalog, ledger: Ledger, llm: LLMClient,
                 paypal: PayPalBackend, now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 autopay: Optional[bool] = None):
        self.settings = settings
        self.catalog = catalog
        self.ledger = ledger
        self.llm = llm
        self.paypal = paypal
        self.now = now_fn
        # Autopay = agent pays approved intents without a popup. Real autonomous payment needs a
        # PayPal-vaulted payment method; in sim mode we simulate that saved wallet.
        self.autopay = (paypal.mode == "sim") if autopay is None else autopay

    # ------------------------------------------------------------ policy
    def get_policy(self) -> tuple[Policy, str]:
        raw = self.ledger.kv_get("policy")
        if raw:
            data = json.loads(raw)
            return Policy(**data["policy"]), data.get("rules_text", "")
        return default_policy(self.settings.policy_timezone), DEFAULT_RULES_TEXT

    def set_policy(self, policy: Policy, rules_text: str = "") -> Policy:
        self.ledger.kv_set("policy", json.dumps({"policy": policy.model_dump(mode="json"),
                                                 "rules_text": rules_text}))
        return policy

    def compile(self, text: str) -> CompileResult:
        if not text.strip():
            raise ServiceError("rules text is empty")
        return compile_rules(text, self.catalog, self.llm, self.settings.policy_timezone)

    # ----------------------------------------------------------- intents
    def chat(self, message: str) -> tuple[AgentResult, Optional[dict[str, Any]]]:
        if not message.strip():
            raise ServiceError("message is empty")
        result = parse_request(message, self.catalog, self.llm)
        entry = self.submit(result.intent) if result.intent else None
        return result, entry

    def submit(self, intent: PurchaseIntent, use_llm_explanation: bool = True) -> dict[str, Any]:
        policy, _ = self.get_policy()
        now = self.now()
        spent = self.ledger.spent_by_period(policy.timezone, now)
        decision = evaluate(intent, policy, self.catalog, spent, now)
        text = explain(decision, self.llm if use_llm_explanation else None)
        entry_id = self.ledger.record(intent, decision, text, now=now)
        if decision.outcome == Outcome.APPROVE and self.autopay:
            try:
                self._autopay(entry_id)
            except (PayPalError, ServiceError):
                pass  # stays 'approved'; user can pay manually
        return self.ledger.get(entry_id)

    # ----------------------------------------------------------- approvals
    def approve_hold(self, entry_id: int) -> dict[str, Any]:
        """Human approves a HOLD. Re-checks BLOCK rules against *current* spend first."""
        entry = self._get(entry_id)
        if entry["status"] != "held":
            raise ServiceError(f"entry {entry_id} is {entry['status']}, not held", 409)
        policy, _ = self.get_policy()
        intent = PurchaseIntent(**entry["intent"])
        now = self.now()
        recheck = evaluate(intent, policy, self.catalog, self.ledger.spent_by_period(policy.timezone, now), now)
        if recheck.outcome == Outcome.BLOCK:
            self.ledger.set_status(entry_id, "rejected")
            reasons = "; ".join(h.detail for h in recheck.hits if h.effect == Outcome.BLOCK)
            self.ledger.set_explanation(entry_id, f"Rejected on re-check: {reasons}.")
            raise ServiceError(f"Can't approve any more: {reasons}", 409)
        self.ledger.set_status(entry_id, "approved")
        self.ledger.set_explanation(entry_id, entry["explanation"] + " You approved it.")
        if self.autopay:
            try:
                self._autopay(entry_id)
            except (PayPalError, ServiceError):
                pass
        return self.ledger.get(entry_id)

    def reject_hold(self, entry_id: int) -> dict[str, Any]:
        entry = self._get(entry_id)
        if entry["status"] != "held":
            raise ServiceError(f"entry {entry_id} is {entry['status']}, not held", 409)
        self.ledger.set_status(entry_id, "rejected")
        return self.ledger.get(entry_id)

    # -------------------------------------------------------------- PayPal
    def create_paypal_order(self, entry_id: int, return_url: Optional[str] = None,
                            cancel_url: Optional[str] = None) -> dict[str, Any]:
        entry = self._get(entry_id)
        if entry["status"] not in ("approved", "failed"):
            raise ServiceError(f"entry {entry_id} is {entry['status']}; only approved purchases can be paid", 409)
        decision = Decision(**entry["decision"])
        body = build_order_body(entry_id, decision.lines, decision.total_cents, return_url, cancel_url)
        try:
            order = self.paypal.create_order(body, new_request_id(f"create-{entry_id}"))
        except PayPalError as e:
            raise ServiceError(str(e), 502) from e
        if entry["status"] == "failed":
            self.ledger.set_status(entry_id, "approved")
        self.ledger.attach_order(entry_id, order["id"])
        return {"order_id": order["id"], "status": order.get("status"), "approve_url": approve_link(order),
                "mode": self.paypal.mode}

    def capture(self, entry_id: int, order_id: str) -> dict[str, Any]:
        entry = self._get(entry_id)
        if entry["status"] != "approved":
            raise ServiceError(f"entry {entry_id} is {entry['status']}, not approved", 409)
        if entry["paypal_order_id"] != order_id:
            raise ServiceError("PayPal order does not belong to this purchase", 409)
        try:
            result = self.paypal.capture_order(order_id, new_request_id(f"capture-{entry_id}"))
        except PayPalError as e:
            self.ledger.set_status(entry_id, "failed")
            raise ServiceError(f"Capture failed: {e}", 502) from e
        if result.get("status") != "COMPLETED":
            self.ledger.set_status(entry_id, "failed")
            raise ServiceError(f"Capture status {result.get('status')}", 502)
        captured = _captured_cents(result)
        if captured is not None and captured != entry["total_cents"]:
            self.ledger.set_status(entry_id, "failed")
            raise ServiceError("Captured amount does not match the approved total", 502)
        return self.ledger.set_status(entry_id, "paid", paypal_capture_id=capture_id(result))

    def refund(self, entry_id: int) -> dict[str, Any]:
        entry = self._get(entry_id)
        if entry["status"] != "paid" or not entry["paypal_capture_id"]:
            raise ServiceError(f"entry {entry_id} is not a paid purchase", 409)
        try:
            self.paypal.refund_capture(entry["paypal_capture_id"], new_request_id(f"refund-{entry_id}"))
        except PayPalError as e:
            raise ServiceError(f"Refund failed: {e}", 502) from e
        return self.ledger.set_status(entry_id, "cancelled")

    def sim_approve(self, order_id: str) -> dict[str, Any]:
        if not isinstance(self.paypal, SimPayPal):
            raise ServiceError("only available in sim mode", 404)
        try:
            return self.paypal.approve(order_id)
        except PayPalError as e:
            raise ServiceError(str(e), 404) from e

    def _autopay(self, entry_id: int) -> None:
        order = self.create_paypal_order(entry_id)
        if isinstance(self.paypal, SimPayPal):
            self.paypal.approve(order["order_id"])  # simulated saved wallet
        self.capture(entry_id, order["order_id"])

    # --------------------------------------------------------------- views
    def budget_status(self) -> list[dict[str, Any]]:
        policy, _ = self.get_policy()
        spent = self.ledger.spent_by_period(policy.timezone, self.now())
        return [{"period": b.period, "limit_cents": b.limit_cents, "spent_cents": spent[b.period],
                 "left_cents": max(0, b.limit_cents - spent[b.period])} for b in policy.budgets]

    def _get(self, entry_id: int) -> dict[str, Any]:
        try:
            return self.ledger.get(entry_id)
        except KeyError:
            raise ServiceError(f"entry {entry_id} not found", 404)


def _captured_cents(order: dict[str, Any]) -> Optional[int]:
    try:
        cap = order["purchase_units"][0]["payments"]["captures"][0]
        return int(round(float(cap["amount"]["value"]) * 100))
    except (KeyError, IndexError, TypeError, ValueError):
        return None
