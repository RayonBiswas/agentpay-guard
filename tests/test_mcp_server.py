"""MCP tools exercised end-to-end against the real FastAPI app (in-process, no network)."""
import asyncio
import json

import httpx
import pytest

pytest.importorskip("mcp.server.mcpserver")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.ledger import Ledger  # noqa: E402
from app.llm import LLMClient  # noqa: E402
from app.main import create_app  # noqa: E402
from app.paypal_client import SimPayPal  # noqa: E402
from mcp_server.agentpay_mcp import GuardAPI, build_server  # noqa: E402
from tests.test_api import NOON_IST  # noqa: E402


@pytest.fixture
def server():
    app = create_app(settings=get_settings(), llm=LLMClient("", ""), paypal=SimPayPal(), ledger=Ledger(":memory:"),
                     now_fn=lambda: NOON_IST, autopay=True)
    tc = TestClient(app)

    def handler(request: httpx.Request) -> httpx.Response:
        r = tc.request(request.method, request.url.path, content=request.content,
                       headers={k: v for k, v in request.headers.items() if k.lower() != "host"})
        return httpx.Response(r.status_code, content=r.content, headers={"content-type": "application/json"})

    api = GuardAPI("http://guard", agent_name="claude-desktop", transport=httpx.MockTransport(handler))
    return build_server(api)


def call(server, name, args):
    res = asyncio.run(server.call_tool(name, args))
    assert not res.is_error, res
    return json.loads(res.content[0].text)


def test_lists_four_tools(server):
    names = sorted(t.name for t in asyncio.run(server.list_tools()))
    assert names == ["check_budget", "purchase_status", "request_purchase", "search_catalog"]


def test_search_then_buy_within_rules(server):
    found = call(server, "search_catalog", {"query": "esp32"})["results"]
    assert found[0]["product_id"] == "esp32-board"
    r = call(server, "request_purchase", {"items": [{"product_id": "esp32-board", "quantity": 2}], "reason": "robot"})
    assert r["verdict"] == "APPROVE" and r["status"] == "paid" and r["total"] == "$13.00"
    assert call(server, "purchase_status", {"purchase_id": r["purchase_id"]})["status"] == "paid"
    budget = call(server, "check_budget", {})
    assert budget["budgets"][0]["spent"] == "$13.00" and budget["per_item_max"] == "$50.00"


def test_blocked_purchase_tells_agent_not_to_retry(server):
    r = call(server, "request_purchase", {"items": [{"product_id": "game-giftcard", "quantity": 1}]})
    assert r["verdict"] == "BLOCK" and "Do not retry" in r["next_step"]
    assert any("gaming" in x for x in r["reasons"])


def test_hold_waits_for_human(server):
    r = call(server, "request_purchase", {"items": [{"product_id": "desk-lamp", "quantity": 1}]})
    assert r["verdict"] == "HOLD" and r["status"] == "held"
