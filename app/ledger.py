"""SQLite ledger + audit log of every decision and payment state."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from .models import Decision, Outcome, PurchaseIntent
from .periods import PERIODS, period_start

# Status lifecycle
#   APPROVE -> approved -> paid            (or -> failed / cancelled)
#   HOLD    -> held -> approved -> paid    (or -> rejected)
#   BLOCK   -> blocked
COMMITTED = ("approved", "paid")       # counts against budgets (prevents double spend)
STATUSES = ("approved", "held", "blocked", "paid", "rejected", "cancelled", "failed")
_TRANSITIONS = {
    "held": {"approved", "rejected"},
    "approved": {"paid", "failed", "cancelled"},
    "paid": {"cancelled"},   # refund
    "failed": {"approved"},  # retry
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    agent TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL,
    status TEXT NOT NULL,
    total_cents INTEGER NOT NULL,
    intent_json TEXT NOT NULL,
    decision_json TEXT NOT NULL,
    explanation TEXT NOT NULL DEFAULT '',
    paypal_order_id TEXT,
    paypal_capture_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_decisions_created ON decisions(created_at);
"""

_INITIAL_STATUS = {Outcome.APPROVE: "approved", Outcome.HOLD: "held", Outcome.BLOCK: "blocked"}


class InvalidTransition(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Ledger:
    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def record(self, intent: PurchaseIntent, decision: Decision,
               explanation: str = "", now: Optional[datetime] = None) -> int:
        ts = (now or _utcnow()).astimezone(timezone.utc).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO decisions (created_at, updated_at, agent, reason, outcome, status, total_cents,"
                " intent_json, decision_json, explanation) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts, ts, intent.agent, intent.reason, decision.outcome.value,
                 _INITIAL_STATUS[decision.outcome], decision.total_cents,
                 intent.model_dump_json(), decision.model_dump_json(), explanation),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def set_status(self, entry_id: int, status: str, *, paypal_order_id: Optional[str] = None,
                   paypal_capture_id: Optional[str] = None) -> dict[str, Any]:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        with self._lock:
            row = self._conn.execute("SELECT status FROM decisions WHERE id=?", (entry_id,)).fetchone()
            if row is None:
                raise KeyError(entry_id)
            if status not in _TRANSITIONS.get(row["status"], set()):
                raise InvalidTransition(f"cannot move entry {entry_id} from {row['status']} to {status}")
            self._conn.execute(
                "UPDATE decisions SET status=?, updated_at=?,"
                " paypal_order_id=COALESCE(?, paypal_order_id),"
                " paypal_capture_id=COALESCE(?, paypal_capture_id) WHERE id=?",
                (status, _utcnow().isoformat(), paypal_order_id, paypal_capture_id, entry_id),
            )
            self._conn.commit()
        return self.get(entry_id)

    def set_explanation(self, entry_id: int, text: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE decisions SET explanation=? WHERE id=?", (text, entry_id))
            self._conn.commit()

    def get(self, entry_id: int) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM decisions WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise KeyError(entry_id)
        return self._to_dict(row)

    def list(self, status: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM decisions", []
        if status:
            q += " WHERE status=?"
            args.append(status)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [self._to_dict(r) for r in rows]

    def spent_by_period(self, tz: str, now: Optional[datetime] = None) -> dict[str, int]:
        """Committed spend (approved + paid) since the start of each window."""
        now = now or _utcnow()
        out: dict[str, int] = {}
        placeholders = ",".join("?" * len(COMMITTED))
        with self._lock:
            for p in PERIODS:
                start = period_start(p, now, tz).isoformat()
                row = self._conn.execute(
                    f"SELECT COALESCE(SUM(total_cents),0) FROM decisions"
                    f" WHERE status IN ({placeholders}) AND created_at >= ?",
                    (*COMMITTED, start),
                ).fetchone()
                out[p] = int(row[0])
        return out

    @staticmethod
    def _to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["intent"] = json.loads(d.pop("intent_json"))
        d["decision"] = json.loads(d.pop("decision_json"))
        return d
