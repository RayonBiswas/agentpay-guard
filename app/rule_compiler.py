"""Plain-English spending rules -> validated Policy.

Path 1: LLM returns JSON (dollar amounts), we convert to cents and validate with Pydantic.
Path 2: deterministic regex parser (used when no LLM, or the LLM output is invalid).
Either way the user sees the compiled policy and confirms it before it is saved.
"""
from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ValidationError

from .catalog import Catalog
from .llm import LLMClient
from .models import Policy

# words people use -> catalogue category
CATEGORY_SYNONYMS = {
    "electronics": ["electronics", "electronic", "gadgets", "gadget", "tech", "computer stuff"],
    "stationery": ["stationery", "stationary", "office supplies", "pens", "notebooks"],
    "groceries": ["groceries", "grocery", "food", "snacks", "drinks"],
    "home": ["home", "household", "home goods"],
    "gaming": ["gaming", "games", "game", "video games", "gift cards", "giftcards", "entertainment"],
}


class CompileResult(BaseModel):
    policy: Policy
    source: Literal["llm", "heuristic"]
    notes: list[str]
    llm_error: Optional[str] = None


def _cents(usd: Any) -> Optional[int]:
    if usd is None or usd == "":
        return None
    try:
        v = float(str(usd).replace("$", "").replace(",", ""))
    except ValueError:
        return None
    return int(round(v * 100)) if v > 0 else None


# ------------------------------------------------------------------ LLM path

_SYSTEM = """You convert a user's plain-English spending rules for an AI shopping agent into JSON.
Output ONLY a JSON object with these keys (use null when the user did not mention a rule):
{
 "per_item_max_usd": number|null,        // max price of any single item
 "per_order_max_usd": number|null,       // max total of one order
 "daily_budget_usd": number|null,
 "weekly_budget_usd": number|null,
 "monthly_budget_usd": number|null,
 "ask_above_usd": number|null,           // orders above this need the user's approval
 "max_quantity_per_item": integer|null,
 "allowed_categories": [string]|null,    // ONLY these categories; null = all
 "blocked_categories": [string],
 "allowed_merchants": [string]|null,
 "blocked_merchants": [string],
 "quiet_hours": {"start":"HH:MM","end":"HH:MM"}|null   // 24h, no purchases in this window
}
Valid categories: {categories}. Valid merchants: {merchants}.
Map synonyms to valid categories (e.g. "games" -> "gaming", "food" -> "groceries"). Never invent rules."""


def _from_llm_json(data: dict[str, Any], catalog: Catalog, tz: str) -> tuple[Policy, list[str]]:
    notes: list[str] = []
    cats = set(catalog.categories())
    mers = {m.lower(): m for m in catalog.merchants()}

    def clean_cats(values: Any, label: str) -> Optional[list[str]]:
        if values is None:
            return None
        out = []
        for v in values if isinstance(values, list) else [values]:
            v = str(v).strip().lower()
            mapped = next((c for c, syns in CATEGORY_SYNONYMS.items() if v == c or v in syns), None)
            if mapped and mapped in cats:
                out.append(mapped)
            else:
                notes.append(f"Ignored unknown {label} '{v}'")
        return out

    def clean_mers(values: Any, label: str) -> Optional[list[str]]:
        if values is None:
            return None
        out = []
        for v in values if isinstance(values, list) else [values]:
            key = str(v).strip().lower()
            if key in mers:
                out.append(mers[key])
            else:
                notes.append(f"Ignored unknown {label} '{v}'")
        return out

    budgets = []
    for period, key in (("day", "daily_budget_usd"), ("week", "weekly_budget_usd"), ("month", "monthly_budget_usd")):
        c = _cents(data.get(key))
        if c:
            budgets.append({"period": period, "limit_cents": c})

    qty = data.get("max_quantity_per_item")
    try:
        qty = int(qty) if qty not in (None, "") else None
    except (TypeError, ValueError):
        qty = None

    allowed = clean_cats(data.get("allowed_categories"), "category")
    policy = Policy(
        per_item_max_cents=_cents(data.get("per_item_max_usd")),
        per_order_max_cents=_cents(data.get("per_order_max_usd")),
        budgets=budgets,
        ask_above_cents=_cents(data.get("ask_above_usd")),
        max_quantity_per_item=qty if qty and qty > 0 else None,
        allowed_categories=allowed if allowed else None,
        blocked_categories=clean_cats(data.get("blocked_categories") or [], "category") or [],
        allowed_merchants=clean_mers(data.get("allowed_merchants"), "merchant") or None,
        blocked_merchants=clean_mers(data.get("blocked_merchants") or [], "merchant") or [],
        quiet_hours=data.get("quiet_hours") or None,
        timezone=tz,
    )
    return policy, notes


# ------------------------------------------------------------ heuristic path

_NUM = r"\$?\s?(\d+(?:\.\d{1,2})?)(?:\s?(?:usd|dollars?|bucks))?"
_TIME = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"


def _to_hhmm(h: str, m: Optional[str], ampm: Optional[str]) -> Optional[str]:
    hour, minute = int(h), int(m or 0)
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _find_categories(fragment: str, catalog: Catalog) -> list[str]:
    found = []
    frag = f" {fragment.lower()} "
    for cat, syns in CATEGORY_SYNONYMS.items():
        if cat not in catalog.categories():
            continue
        if any(re.search(rf"\b{re.escape(s)}\b", frag) for s in syns):
            found.append(cat)
    return found


def _find_merchants(fragment: str, catalog: Catalog) -> list[str]:
    return [m for m in catalog.merchants() if re.search(rf"\b{re.escape(m.lower())}\b", fragment.lower())]


def heuristic_compile(text: str, catalog: Catalog, tz: str) -> tuple[Policy, list[str]]:
    t = " " + text.lower().replace("’", "'") + " "
    notes: list[str] = []
    fields: dict[str, Any] = {"budgets": [], "blocked_categories": [], "blocked_merchants": []}

    def money(pattern: str) -> Optional[int]:
        m = re.search(pattern, t)
        return _cents(m.group(1)) if m else None

    # ask-me threshold (check first so its numbers aren't mistaken for limits)
    ask = money(rf"(?:ask|check with|confirm with|approval|approve|confirm)[^.;\n$\d]{{0,30}}(?:above|over|more than|>|exceeds?|beyond)\s*{_NUM}")
    if ask:
        fields["ask_above_cents"] = ask
        notes.append(f"Ask before orders above ${ask/100:.2f}")

    item = money(rf"{_NUM}\s*(?:per|each|a|/|for any|for each)\s*(?:single\s+)?(?:item|product|thing)\b") \
        or money(rf"(?:per|each|any|single|one)\s+(?:item|product|thing)\s*(?:max(?:imum)?|limit|cap|is|of|:|=|under|below|up to)?\s*{_NUM}") \
        or money(rf"(?:no|nothing|no item|no single item)\s+(?:item\s+)?(?:over|above|more than|costing more than)\s*{_NUM}") \
        or money(rf"items?\s+(?:under|below|up to|max(?:imum)?|at most|no more than)\s*{_NUM}")
    if item:
        fields["per_item_max_cents"] = item
        notes.append(f"Max ${item/100:.2f} per item")

    order = money(rf"{_NUM}\s*(?:per|each|a|/|for any|for each)\s*(?:single\s+)?(?:order|purchase|checkout|transaction)\b") \
        or money(rf"(?:per|each|any|single|one)\s+(?:order|purchase|checkout|transaction)\s*(?:max(?:imum)?|limit|cap|is|of|:|=|under|below|up to)?\s*{_NUM}") \
        or money(rf"orders?\s+(?:under|below|up to|max(?:imum)?|at most|no more than|capped at)\s*{_NUM}")
    if order:
        fields["per_order_max_cents"] = order
        notes.append(f"Max ${order/100:.2f} per order")

    for period, words in (("day", "day|daily|today"), ("week", "week|weekly"), ("month", "month|monthly")):
        c = money(rf"(?:{words})\s+(?:budget|limit|cap|spend(?:ing)?)?\s*(?:of|is|=|:)?\s*{_NUM}") \
            or money(rf"{_NUM}\s*(?:a|per|each|/|every)\s*(?:{words})\b")
        if c:
            fields["budgets"].append({"period": period, "limit_cents": c})
            notes.append(f"{period.capitalize()} budget ${c/100:.2f}")

    q = re.search(r"(?:max(?:imum)?|no more than|at most|up to|limit(?: of)?)\s+(\d{1,3})\s*(?:of each|per item|units?|pieces?|qty|of any(?: one)? item|each)", t)
    if q:
        fields["max_quantity_per_item"] = int(q.group(1))
        notes.append(f"Max quantity {q.group(1)} per item")

    # category / merchant whitelists: "only electronics and stationery", "only from VoltMart"
    for m in re.finditer(r"\bonly\b([^.;\n]*)", t):
        # stop at a negation so "only electronics, no gaming" doesn't whitelist gaming
        frag = re.split(r"\b(?:no|never|block|ban|avoid|not|but|except)\b", m.group(1))[0]
        if re.search(r"\bfrom\b|\bat\b", frag):
            mers = _find_merchants(frag, catalog)
            if mers:
                fields["allowed_merchants"] = mers
                notes.append("Only merchants: " + ", ".join(mers))
                continue
        cats = _find_categories(frag, catalog)
        if cats:
            fields["allowed_categories"] = cats
            notes.append("Only categories: " + ", ".join(cats))

    # blocks: "no gaming", "never buy games", "block gift cards", "never from PixelPlay"
    for m in re.finditer(r"\b(?:no|never(?: buy)?|block|ban|avoid|don't buy|do not buy|not)\b([^.;\n]*)", t):
        frag = m.group(1)[:60]
        if re.search(r"\bfrom\b|\bat\b", frag):
            for mer in _find_merchants(frag, catalog):
                if mer not in fields["blocked_merchants"]:
                    fields["blocked_merchants"].append(mer)
                    notes.append(f"Blocked merchant: {mer}")
        for cat in _find_categories(frag, catalog):
            if cat not in fields["blocked_categories"] and cat not in (fields.get("allowed_categories") or []):
                fields["blocked_categories"].append(cat)
                notes.append(f"Blocked category: {cat}")

    # quiet hours: "between 11pm and 7am", "after 11pm until 7am", "11pm-7am"
    qh = re.search(rf"(?:between|from|after)\s+{_TIME}\s*(?:and|to|until|till|-|–)\s*{_TIME}", t) \
        or re.search(rf"{_TIME}\s*(?:-|–|to)\s*{_TIME}\s*(?:quiet|no (?:purchases|shopping|buying))", t)
    if qh:
        h1, m1, a1, h2, m2, a2 = qh.groups()
        # "11-7am": share the trailing am/pm only if the first has none and it makes sense
        start, end = _to_hhmm(h1, m1, a1 or (None if a2 is None else ("pm" if a2 == "am" and int(h1) > int(h2) else a2))), _to_hhmm(h2, m2, a2)
        if start and end and re.search(r"no (?:purchas|shop|buy|spend|order)|quiet|don't|do not|block|pause|sleep", t):
            fields["quiet_hours"] = {"start": start, "end": end}
            notes.append(f"No purchases {start}–{end}")

    if not notes:
        notes.append("No rules recognised; the policy has no limits")
    return Policy(timezone=tz, **fields), notes


# ------------------------------------------------------------------ public

def compile_rules(text: str, catalog: Catalog, llm: Optional[LLMClient] = None,
                  tz: str = "Asia/Kolkata") -> CompileResult:
    llm_error = None
    if llm is not None and llm.enabled:
        system = _SYSTEM.replace("{categories}", ", ".join(catalog.categories())) \
                        .replace("{merchants}", ", ".join(catalog.merchants()))
        data = llm.chat_json(system, text)
        if data is not None:
            try:
                policy, notes = _from_llm_json(data, catalog, tz)
                return CompileResult(policy=policy, source="llm", notes=notes or ["Compiled by LLM"])
            except (ValidationError, ValueError, TypeError) as e:
                llm_error = f"LLM output failed validation: {e.__class__.__name__}"
        else:
            llm_error = llm.last_error or "LLM returned no JSON"
    policy, notes = heuristic_compile(text, catalog, tz)
    return CompileResult(policy=policy, source="heuristic", notes=notes, llm_error=llm_error)
