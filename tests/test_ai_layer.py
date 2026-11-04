import json

import httpx
import pytest

from app.agent import explain, heuristic_parse, parse_request, template_explanation
from app.llm import LLMClient, extract_json
from app.models import IntentItem, Outcome, Policy, PurchaseIntent
from app.policy_engine import evaluate
from app.rule_compiler import compile_rules, heuristic_compile

TZ = "Asia/Kolkata"


class FakeLLM(LLMClient):
    """LLM stub: returns a canned JSON/text response."""
    def __init__(self, reply):
        super().__init__("http://fake/v1", "fake-model")
        self.reply = reply
        self.calls = 0

    def chat(self, system, user, json_mode=False, temperature=0.1):
        self.calls += 1
        if self.reply is None:
            self.last_error = "ConnectError: down"
            return None
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


# ---------------------------------------------------------------- llm client

def test_extract_json_handles_fences_and_chatter():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here it is: {"a": {"b": 2}} hope that helps') == {"a": {"b": 2}}
    assert extract_json("no json here") is None
    assert extract_json("[1,2]") is None


def test_llm_client_disabled_without_url():
    c = LLMClient("", "m")
    assert not c.enabled and c.chat("s", "u") is None


def test_llm_client_parses_openai_response_via_mock_transport():
    def handler(request: httpx.Request):
        body = json.loads(request.content)
        assert body["model"] == "m" and body["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})
    c = LLMClient("http://x/v1", "m", transport=httpx.MockTransport(handler))
    assert c.chat_json("s", "u") == {"ok": True}


def test_llm_client_returns_none_on_http_error():
    c = LLMClient("http://x/v1", "m", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert c.chat("s", "u") is None and "HTTPStatusError" in c.last_error


# ------------------------------------------------------- heuristic compiler

DEMO_RULES = ("Max $50 per item and $100 per order. Weekly budget $150. "
              "Ask me before anything over $20. No gaming or gift cards. "
              "At most 5 of each item. No purchases between 11pm and 7am.")


def test_heuristic_compiles_demo_rules(catalog):
    p, notes = heuristic_compile(DEMO_RULES, catalog, TZ)
    assert p.per_item_max_cents == 5000
    assert p.per_order_max_cents == 10000
    assert [(b.period, b.limit_cents) for b in p.budgets] == [("week", 15000)]
    assert p.ask_above_cents == 2000
    assert p.blocked_categories == ["gaming"]
    assert p.max_quantity_per_item == 5
    assert (p.quiet_hours.start, p.quiet_hours.end) == ("23:00", "07:00")
    assert len(notes) >= 6


@pytest.mark.parametrize("text,check", [
    ("$30 a day", lambda p: p.budgets[0].period == "day" and p.budgets[0].limit_cents == 3000),
    ("monthly limit of 200 dollars", lambda p: p.budgets[0].limit_cents == 20000),
    ("nothing over $25", lambda p: p.per_item_max_cents == 2500),
    ("only electronics and stationery, no gaming",
     lambda p: p.allowed_categories == ["electronics", "stationery"] and p.blocked_categories == ["gaming"]),
    ("never buy from PixelPlay", lambda p: p.blocked_merchants == ["pixelplay"]),
    ("only from VoltMart", lambda p: p.allowed_merchants == ["voltmart"]),
    ("confirm with me if an order is more than 15.50", lambda p: p.ask_above_cents == 1550),
    ("no food", lambda p: p.blocked_categories == ["groceries"]),
])
def test_heuristic_rule_variants(catalog, text, check):
    p, _ = heuristic_compile(text, catalog, TZ)
    assert check(p), p.model_dump()


def test_heuristic_unrecognised_text_gives_open_policy(catalog):
    p, notes = heuristic_compile("be sensible please", catalog, TZ)
    assert p == Policy(timezone=TZ) and "No rules recognised" in notes[0]


# ------------------------------------------------------------- LLM compiler

def test_llm_compile_converts_dollars_and_maps_synonyms(catalog):
    llm = FakeLLM({"per_item_max_usd": 49.99, "weekly_budget_usd": "150", "ask_above_usd": 20,
                   "blocked_categories": ["games", "crypto"], "allowed_merchants": None,
                   "blocked_merchants": ["PixelPlay"], "quiet_hours": {"start": "23:00", "end": "07:00"}})
    r = compile_rules("whatever", catalog, llm, TZ)
    assert r.source == "llm"
    assert r.policy.per_item_max_cents == 4999
    assert r.policy.budgets[0].limit_cents == 15000
    assert r.policy.blocked_categories == ["gaming"]
    assert r.policy.blocked_merchants == ["pixelplay"]
    assert any("crypto" in n for n in r.notes)


def test_llm_invalid_output_falls_back_to_heuristic(catalog):
    llm = FakeLLM({"quiet_hours": {"start": "late", "end": "early"}})  # fails validation
    r = compile_rules("Weekly budget $40", catalog, llm, TZ)
    assert r.source == "heuristic" and r.policy.budgets[0].limit_cents == 4000
    assert "validation" in r.llm_error


def test_llm_down_falls_back_to_heuristic(catalog):
    r = compile_rules("Max $10 per item", catalog, FakeLLM(None), TZ)
    assert r.source == "heuristic" and r.policy.per_item_max_cents == 1000 and r.llm_error


# --------------------------------------------------------------- the agent

def ids(result):
    return [(i.product_id, i.quantity) for i in result.intent.items]


@pytest.mark.parametrize("msg,expected", [
    ("Buy me a USB-C cable and a notebook", [("usb-c-cable", 1), ("a5-notebook", 1)]),
    ("get 3 oat milk, two gel pens", [("oat-milk", 3), ("gel-pens", 2)]),
    ("I need noise cancelling headphones", [("anc-headphones", 1)]),
    ("order an esp32 board plus jumper wires", [("esp32-board", 1), ("jumper-wires", 1)]),
])
def test_heuristic_agent_parses_requests(catalog, msg, expected):
    assert ids(heuristic_parse(msg, catalog)) == expected


def test_heuristic_agent_price_cap_picks_cheaper_match(catalog):
    r = heuristic_parse("buy a cable under $9", catalog)
    assert r.intent.items[0].product_id == "hdmi-cable"  # $7.99, the USB-C one is $9.99


def test_heuristic_agent_no_match(catalog):
    r = heuristic_parse("buy a yacht", catalog)
    assert r.intent is None and "couldn't match" in r.reply.lower()


def test_llm_agent_drops_hallucinated_ids(catalog):
    llm = FakeLLM({"items": [{"product_id": "usb-c-cable", "quantity": 2},
                             {"product_id": "free-tesla", "quantity": 1}], "reply": "Got it."})
    r = parse_request("buy cables", catalog, llm)
    assert r.source == "llm" and ids(r) == [("usb-c-cable", 2)]
    assert "free-tesla" in r.reply


def test_llm_agent_non_shopping_message(catalog):
    r = parse_request("hello there", catalog, FakeLLM({"items": [], "reply": "Hi! What should I buy?"}))
    assert r.intent is None and r.source == "llm"


def test_llm_agent_garbage_falls_back(catalog):
    r = parse_request("buy a notebook", catalog, FakeLLM("not json at all"))
    assert r.source == "heuristic" and ids(r) == [("a5-notebook", 1)]


def test_llm_cannot_set_prices(catalog, now):
    """Even if the LLM puts a price in its output, only catalogue prices are used."""
    llm = FakeLLM({"items": [{"product_id": "anc-headphones", "quantity": 1, "price": 0.01}], "reply": ""})
    r = parse_request("buy headphones", catalog, llm)
    d = evaluate(r.intent, Policy(per_item_max_cents=5000), catalog, {}, now)
    assert d.total_cents == 29900 and d.outcome == Outcome.BLOCK


# ------------------------------------------------------------ explanations

def _decision(catalog, now, pid, policy):
    return evaluate(PurchaseIntent(items=[IntentItem(product_id=pid)]), policy, catalog, {}, now)


def test_template_explanations(catalog, now):
    assert template_explanation(_decision(catalog, now, "gel-pens", Policy())).startswith("Approved")
    h = template_explanation(_decision(catalog, now, "desk-lamp", Policy(ask_above_cents=2000)))
    assert h.startswith("Waiting") and "$27.99" in h
    b = template_explanation(_decision(catalog, now, "anc-headphones", Policy(per_item_max_cents=5000)))
    assert b.startswith("Blocked") and "$299.00" in b


def test_explain_rejects_llm_rewrite_that_drops_numbers(catalog, now):
    d = _decision(catalog, now, "anc-headphones", Policy(per_item_max_cents=5000))
    assert explain(d, FakeLLM("All good, buy it!")) == template_explanation(d)
    ok = explain(d, FakeLLM("I stopped this $299.00 purchase because it's over your $50.00 item limit."))
    assert ok.startswith("I stopped")
