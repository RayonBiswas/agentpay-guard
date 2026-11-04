"""Core data models. All money is integer cents (USD) to avoid float errors."""
from __future__ import annotations

import re
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Product(BaseModel):
    id: str
    name: str
    category: str
    merchant: str
    price_cents: int = Field(gt=0)
    tags: list[str] = []


class IntentItem(BaseModel):
    product_id: str
    quantity: int = Field(default=1, ge=1, le=1000)


class PurchaseIntent(BaseModel):
    """What an agent wants to buy. Prices are NEVER taken from here."""
    items: list[IntentItem]
    agent: str = "chat-agent"
    reason: str = ""


class Budget(BaseModel):
    period: Literal["day", "week", "month"]
    limit_cents: int = Field(gt=0)


class QuietHours(BaseModel):
    start: str  # "HH:MM", local to policy timezone
    end: str

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not _HHMM.match(v):
            raise ValueError("time must be HH:MM (24h)")
        return v


def _norm(values: Optional[list[str]]) -> Optional[list[str]]:
    if values is None:
        return None
    return sorted({v.strip().lower() for v in values if v and v.strip()})


class Policy(BaseModel):
    """A user's spending rules. None means 'no limit / not restricted'."""
    per_item_max_cents: Optional[int] = Field(default=None, gt=0)
    per_order_max_cents: Optional[int] = Field(default=None, gt=0)
    budgets: list[Budget] = []
    allowed_categories: Optional[list[str]] = None   # None = all allowed
    blocked_categories: list[str] = []
    allowed_merchants: Optional[list[str]] = None
    blocked_merchants: list[str] = []
    ask_above_cents: Optional[int] = Field(default=None, ge=0)
    max_quantity_per_item: Optional[int] = Field(default=None, ge=1)
    quiet_hours: Optional[QuietHours] = None
    timezone: str = "Asia/Kolkata"
    currency: Literal["USD"] = "USD"

    @model_validator(mode="after")
    def _normalise(self) -> "Policy":
        self.allowed_categories = _norm(self.allowed_categories)
        self.blocked_categories = _norm(self.blocked_categories) or []
        self.allowed_merchants = _norm(self.allowed_merchants)
        self.blocked_merchants = _norm(self.blocked_merchants) or []
        periods = [b.period for b in self.budgets]
        if len(periods) != len(set(periods)):
            raise ValueError("only one budget per period")
        return self


class Outcome(str, Enum):
    APPROVE = "APPROVE"
    HOLD = "HOLD"
    BLOCK = "BLOCK"


class RuleHit(BaseModel):
    rule: str
    effect: Outcome           # HOLD or BLOCK
    detail: str


class PricedLine(BaseModel):
    product_id: str
    name: str
    category: str
    merchant: str
    unit_cents: int
    quantity: int
    line_cents: int


class Decision(BaseModel):
    outcome: Outcome
    total_cents: int
    lines: list[PricedLine]
    hits: list[RuleHit]
    checked_rules: list[str]   # every rule evaluated, for transparency
