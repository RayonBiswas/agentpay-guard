"""Shopping agent: chat message -> PurchaseIntent (product IDs + quantities only).

The agent only *proposes*. The policy engine decides. Product IDs are checked
against the catalogue and anything unknown is dropped before evaluation.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel

from .catalog import Catalog
from .llm import LLMClient
from .models import Decision, IntentItem, Outcome, PurchaseIntent

_WORD_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
             "seven": 7, "eight": 8, "nine": 9, "ten": 10, "a pair of": 2, "a couple of": 2, "couple of": 2}
_FILLER = r"\b(?:please|pls|can you|could you|would you|i need|i want|i'd like|get me|buy me|order me|" \
          r"buy|get|order|purchase|grab|pick up|me|some|the|for|my|new|also|too)\b"
_SHOP_VERBS = re.compile(r"\b(buy|get|order|purchase|grab|need|want|pick up|add|restock|shop)\b", re.I)


class AgentResult(BaseModel):
    intent: Optional[PurchaseIntent]
    reply: str
    source: Literal["llm", "heuristic"]
    unmatched: list[str] = []


def _quantity(chunk: str) -> tuple[int, str]:
    m = re.match(r"\s*(\d{1,4})\s*(?:x\s*)?(.*)", chunk)
    if m:
        return max(1, int(m.group(1))), m.group(2)
    for phrase in sorted(_WORD_NUM, key=len, reverse=True):
        m = re.match(rf"\s*{phrase}\b\s*(.*)", chunk)
        if m:
            return _WORD_NUM[phrase], m.group(1)
    return 1, chunk


def heuristic_parse(message: str, catalog: Catalog) -> AgentResult:
    text = message.lower()
    cap = None
    m = re.search(r"(?:under|below|less than|max|up to|cheaper than)\s*\$?\s?(\d+(?:\.\d{1,2})?)", text)
    if m:
        cap = int(round(float(m.group(1)) * 100))
        text = text[:m.start()] + text[m.end():]
    text = re.sub(_FILLER, " ", text)
    chunks = [c.strip() for c in re.split(r",|\band\b|&|\bplus\b|\+|;|\bwith\b", text) if c.strip()]
    items: list[IntentItem] = []
    unmatched: list[str] = []
    picked: list[str] = []
    for chunk in chunks:
        qty, rest = _quantity(chunk)
        rest = rest.strip(" .!?")
        if not rest:
            continue
        hits = catalog.search(rest, limit=1, max_price_cents=cap) or catalog.search(rest, limit=1)
        if hits:
            items.append(IntentItem(product_id=hits[0].id, quantity=qty))
            picked.append(f"{qty} × {hits[0].name} (${hits[0].price_cents/100:.2f})")
        else:
            unmatched.append(rest)
    if not items:
        reply = "I couldn't match that to anything in the catalogue. Try e.g. “buy a USB-C cable and a notebook”."
        return AgentResult(intent=None, reply=reply, source="heuristic", unmatched=unmatched)
    reply = "I found: " + "; ".join(picked) + "."
    if unmatched:
        reply += " Couldn't find: " + ", ".join(unmatched) + "."
    return AgentResult(intent=PurchaseIntent(items=items, agent="chat-agent", reason=message.strip()[:200]),
                       reply=reply, source="heuristic", unmatched=unmatched)


_SYSTEM = """You are a shopping agent. Pick products from the catalogue that match the user's request.
Output ONLY JSON: {"items":[{"product_id":"<id from catalogue>","quantity":<int>}], "reply":"<one short sentence>"}
If nothing matches or the user is not asking to buy something, return {"items":[], "reply":"<helpful sentence>"}.
Use ONLY product_id values from this catalogue (id | name | price USD | category):
{catalog}
Respect price hints like "under $15" by choosing cheaper matching items. Never invent product IDs.
You do not decide whether the purchase is allowed; a separate policy engine does."""


def parse_request(message: str, catalog: Catalog, llm: Optional[LLMClient] = None) -> AgentResult:
    if llm is not None and llm.enabled:
        listing = "\n".join(f"{p.id} | {p.name} | {p.price_cents/100:.2f} | {p.category}" for p in catalog.all())
        data = llm.chat_json(_SYSTEM.replace("{catalog}", listing), message)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            items: list[IntentItem] = []
            dropped = []
            for raw in data["items"]:
                if not isinstance(raw, dict):
                    continue
                pid = str(raw.get("product_id", "")).strip()
                try:
                    qty = int(raw.get("quantity", 1))
                except (TypeError, ValueError):
                    qty = 1
                if catalog.get(pid) and 1 <= qty <= 1000:
                    items.append(IntentItem(product_id=pid, quantity=qty))
                else:
                    dropped.append(pid or "?")
            reply = str(data.get("reply") or "").strip()[:300]
            if items:
                if dropped:
                    reply += f" (ignored unknown items: {', '.join(dropped)})"
                return AgentResult(intent=PurchaseIntent(items=items, agent="chat-agent",
                                                         reason=message.strip()[:200]),
                                   reply=reply or "Here's what I found.", source="llm")
            if not _SHOP_VERBS.search(message):
                return AgentResult(intent=None, reply=reply or "What would you like me to buy?", source="llm")
            # LLM found nothing but it looks like shopping: try heuristics
    return heuristic_parse(message, catalog)


# ------------------------------------------------------------ explanations

def template_explanation(decision: Decision) -> str:
    total = f"${decision.total_cents/100:.2f}"
    if decision.outcome == Outcome.APPROVE:
        return f"Approved: {total} fits every rule ({len(decision.checked_rules)} checks passed)."
    if decision.outcome == Outcome.HOLD:
        return f"Waiting for you: {total}. " + " ".join(h.detail + "." for h in decision.hits)
    blocks = [h.detail for h in decision.hits if h.effect == Outcome.BLOCK]
    return f"Blocked ({total}): " + "; ".join(blocks) + "."


def explain(decision: Decision, llm: Optional[LLMClient] = None) -> str:
    """Friendly 1–2 sentence explanation. Display only; never changes the decision."""
    base = template_explanation(decision)
    if llm is None or not llm.enabled or decision.outcome == Outcome.APPROVE:
        return base
    text = llm.chat(
        "Rewrite this payment-policy decision as one or two friendly, plain sentences for the user. "
        "Keep every number exactly as given. Do not add advice or change the outcome.",
        base, temperature=0.2,
    )
    if not text:
        return base
    text = text.strip().strip('"')[:400]
    # guard: the rewrite must keep the outcome word and the total
    total = f"{decision.total_cents/100:.2f}"
    if total not in text:
        return base
    return text
