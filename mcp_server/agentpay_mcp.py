"""AgentPay Guard as an MCP server: any MCP-capable agent can shop, but only within your rules.

The tools are thin wrappers over the AgentPay Guard REST API, so the policy
engine stays the single place where money decisions are made.

Claude Desktop config (stdio):
{
  "mcpServers": {
    "agentpay-guard": {
      "command": "python",
      "args": ["-m", "mcp_server.agentpay_mcp"],
      "cwd": "/path/to/agentpay-guard",
      "env": {"AGENTPAY_URL": "http://localhost:8000", "AGENT_API_KEY": ""}
    }
  }
}
"""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx
from mcp.server.mcpserver import MCPServer

from app.catalog import Catalog
from app.models import Product


class GuardAPI:
    def __init__(self, base_url: str, agent_key: str = "", agent_name: str = "mcp-agent",
                 transport: Optional[httpx.BaseTransport] = None):
        self.base_url = base_url.rstrip("/")
        self.agent_name = agent_name
        headers = {"X-Agent-Key": agent_key} if agent_key else {}
        self._http = httpx.Client(base_url=self.base_url, headers=headers, timeout=30, transport=transport)

    def _req(self, method: str, path: str, **kw) -> Any:
        r = self._http.request(method, path, **kw)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text[:200]
            raise RuntimeError(f"AgentPay Guard {r.status_code}: {detail}")
        return r.json()

    def catalog(self) -> list[dict]:
        return self._req("GET", "/api/catalog")

    def submit(self, items: list[dict], reason: str) -> dict:
        return self._req("POST", "/api/intents", json={"items": items, "agent": self.agent_name, "reason": reason})

    def budget(self) -> list[dict]:
        return self._req("GET", "/api/budget")

    def policy(self) -> dict:
        return self._req("GET", "/api/policy")

    def entry(self, entry_id: int) -> dict:
        return self._req("GET", f"/api/entries/{entry_id}")


def _usd(c: Optional[int]) -> Optional[str]:
    return None if c is None else f"${c / 100:.2f}"


def summarize(entry: dict) -> dict:
    d = entry["decision"]
    next_step = {
        "paid": "Done. Paid through PayPal.",
        "approved": "Allowed. Waiting for payment through PayPal in the AgentPay Guard app.",
        "held": "Waiting for the human to approve it in the AgentPay Guard app. Do not retry.",
        "blocked": "Not allowed by the user's rules. Do not retry the same purchase.",
    }.get(entry["status"], entry["status"])
    return {
        "purchase_id": entry["id"],
        "verdict": d["outcome"],
        "status": entry["status"],
        "total": _usd(entry["total_cents"]),
        "items": [f'{l["quantity"]} x {l["name"]} ({_usd(l["line_cents"])})' for l in d["lines"]],
        "reasons": [h["detail"] for h in d["hits"]],
        "paypal_order_id": entry.get("paypal_order_id"),
        "next_step": next_step,
    }


def build_server(api: GuardAPI) -> MCPServer:
    server = MCPServer(
        "agentpay-guard",
        instructions=("Use these tools to buy things for the user. Every purchase is checked against the "
                      "user's spending rules. Respect the verdict: never try to split or rephrase a blocked "
                      "purchase to get around a rule."),
    )

    @server.tool(description="Search the product catalogue. Returns product IDs to use with request_purchase.")
    def search_catalog(query: str, max_price_usd: Optional[float] = None, limit: int = 5) -> dict:
        cat = Catalog([Product(**p) for p in api.catalog()])
        cap = int(round(max_price_usd * 100)) if max_price_usd else None
        hits = cat.search(query, limit=max(1, min(limit, 20)), max_price_cents=cap)
        return {"results": [{"product_id": p.id, "name": p.name, "price": _usd(p.price_cents),
                             "category": p.category, "merchant": p.merchant} for p in hits]}

    @server.tool(description=("Ask to buy products. items = [{product_id, quantity}]. Returns the verdict "
                              "(APPROVE / HOLD / BLOCK) from the user's spending rules and the payment status."))
    def request_purchase(items: list[dict], reason: str = "") -> dict:
        clean = [{"product_id": str(i.get("product_id", "")), "quantity": int(i.get("quantity", 1))} for i in items]
        return summarize(api.submit(clean, reason[:200]))

    @server.tool(description="See the user's spending limits and how much budget is left.")
    def check_budget() -> dict:
        p = api.policy()["policy"]
        return {
            "budgets": [{"period": b["period"], "limit": _usd(b["limit_cents"]), "spent": _usd(b["spent_cents"]),
                         "left": _usd(b["left_cents"])} for b in api.budget()],
            "per_item_max": _usd(p.get("per_item_max_cents")),
            "per_order_max": _usd(p.get("per_order_max_cents")),
            "ask_user_above": _usd(p.get("ask_above_cents")),
            "blocked_categories": p.get("blocked_categories", []),
        }

    @server.tool(description="Check the status of an earlier purchase request by its purchase_id.")
    def purchase_status(purchase_id: int) -> dict:
        return summarize(api.entry(purchase_id))

    return server


def main() -> None:
    api = GuardAPI(os.getenv("AGENTPAY_URL", "http://localhost:8000"), os.getenv("AGENT_API_KEY", ""),
                   os.getenv("AGENT_NAME", "mcp-agent"))
    build_server(api).run("stdio")


if __name__ == "__main__":
    main()
