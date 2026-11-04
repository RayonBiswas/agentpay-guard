"""FastAPI app: REST API + static single-page UI.

Run:  uvicorn app.main:app --reload     then open http://localhost:8000
"""
from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .catalog import Catalog
from .config import Settings, get_settings
from .ledger import InvalidTransition, Ledger
from .llm import LLMClient
from .models import Policy, PurchaseIntent
from .paypal_client import PayPalBackend, make_backend
from .service import GuardService, ServiceError

STATIC = Path(__file__).resolve().parent / "static"


class RulesIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class PolicyIn(BaseModel):
    policy: Policy
    rules_text: str = ""


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


class CaptureIn(BaseModel):
    order_id: str = Field(min_length=1, max_length=64)


def create_app(settings: Optional[Settings] = None, llm: Optional[LLMClient] = None,
               paypal: Optional[PayPalBackend] = None, ledger: Optional[Ledger] = None,
               now_fn: Optional[Callable[[], datetime]] = None, autopay: Optional[bool] = None) -> FastAPI:
    settings = settings or get_settings()
    catalog = Catalog.load(settings.catalog_path)
    ledger = ledger or Ledger(settings.db_path)
    llm = llm or LLMClient(settings.llm_base_url, settings.llm_model, settings.llm_api_key)
    paypal = paypal or make_backend(settings.paypal_mode, settings.paypal_client_id, settings.paypal_client_secret)
    if autopay is None and settings.autopay in ("on", "off"):
        autopay = settings.autopay == "on"
    svc = GuardService(settings, catalog, ledger, llm, paypal,
                       now_fn=now_fn or (lambda: datetime.now(timezone.utc)), autopay=autopay)
    agent_key = os.getenv("AGENT_API_KEY", "")

    app = FastAPI(title="AgentPay Guard", version="1.0.0",
                  description="Spending guardrails for AI shopping agents, paid through PayPal.")
    app.state.svc = svc

    @app.exception_handler(ServiceError)
    async def _svc_err(_: Request, e: ServiceError):
        return JSONResponse(status_code=e.status, content={"detail": str(e)})

    @app.exception_handler(InvalidTransition)
    async def _transition_err(_: Request, e: InvalidTransition):
        return JSONResponse(status_code=409, content={"detail": str(e)})

    def require_agent_key(x_agent_key: str = Header(default="")) -> None:
        if agent_key and not hmac.compare_digest(x_agent_key, agent_key):
            raise HTTPException(401, "invalid or missing X-Agent-Key")

    # ------------------------------------------------------------ meta
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "paypal_mode": paypal.mode, "llm": llm.label, "autopay": svc.autopay}

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        return {"paypal_mode": paypal.mode,
                "paypal_client_id": settings.paypal_client_id if paypal.mode == "sandbox" else "",
                "llm": llm.label, "autopay": svc.autopay, "timezone": settings.policy_timezone}

    @app.get("/api/catalog")
    def get_catalog() -> list[dict[str, Any]]:
        return [p.model_dump() for p in catalog.all()]

    # ---------------------------------------------------------- policy
    @app.get("/api/policy")
    def get_policy() -> dict[str, Any]:
        policy, text = svc.get_policy()
        return {"policy": policy.model_dump(mode="json"), "rules_text": text}

    @app.put("/api/policy")
    def put_policy(body: PolicyIn) -> dict[str, Any]:
        p = svc.set_policy(body.policy, body.rules_text)
        return {"policy": p.model_dump(mode="json"), "rules_text": body.rules_text}

    @app.post("/api/rules/compile")
    def compile_rules(body: RulesIn) -> dict[str, Any]:
        r = svc.compile(body.text)
        return {"policy": r.policy.model_dump(mode="json"), "source": r.source, "notes": r.notes,
                "llm_error": r.llm_error}

    # ---------------------------------------------------------- agents
    @app.post("/api/chat")
    def chat(body: ChatIn) -> dict[str, Any]:
        result, entry = svc.chat(body.message)
        return {"reply": result.reply, "source": result.source, "unmatched": result.unmatched, "entry": entry}

    @app.post("/api/intents", dependencies=[Depends(require_agent_key)])
    def submit_intent(intent: PurchaseIntent) -> dict[str, Any]:
        """For external agents: submit product IDs + quantities, get a decision back."""
        return svc.submit(intent)

    # ---------------------------------------------------------- ledger
    @app.get("/api/ledger")
    def ledger_list(status: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        return svc.ledger.list(status=status, limit=max(1, min(limit, 500)))

    @app.get("/api/entries/{entry_id}")
    def entry(entry_id: int) -> dict[str, Any]:
        return svc._get(entry_id)

    @app.get("/api/budget")
    def budget() -> list[dict[str, Any]]:
        return svc.budget_status()

    @app.post("/api/entries/{entry_id}/approve")
    def approve(entry_id: int) -> dict[str, Any]:
        return svc.approve_hold(entry_id)

    @app.post("/api/entries/{entry_id}/reject")
    def reject(entry_id: int) -> dict[str, Any]:
        return svc.reject_hold(entry_id)

    # ---------------------------------------------------------- PayPal
    @app.post("/api/entries/{entry_id}/paypal/order")
    def paypal_order(entry_id: int) -> dict[str, Any]:
        return svc.create_paypal_order(entry_id)

    @app.post("/api/entries/{entry_id}/paypal/capture")
    def paypal_capture(entry_id: int, body: CaptureIn) -> dict[str, Any]:
        return svc.capture(entry_id, body.order_id)

    @app.post("/api/entries/{entry_id}/refund")
    def refund(entry_id: int) -> dict[str, Any]:
        return svc.refund(entry_id)

    @app.post("/api/sim/paypal/{order_id}/approve")
    def sim_approve(order_id: str) -> dict[str, Any]:
        return svc.sim_approve(order_id)

    # -------------------------------------------------------------- UI
    if STATIC.exists():
        app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        page = STATIC / "index.html"
        if page.exists():
            return FileResponse(page)
        return {"message": "AgentPay Guard API. See /docs"}

    return app


app = create_app()
