"""Reset the demo to a clean state before each recording take.

    python scripts/demo_reset.py           # wipe purchases, restore the default rules
    python scripts/demo_reset.py --keep    # keep purchases, only restore the default rules

Stop the server first (or restart it after), since SQLite is opened by the app.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.ledger import Ledger  # noqa: E402
from app.policy_engine import default_policy  # noqa: E402
from app.service import DEFAULT_RULES_TEXT  # noqa: E402


def main() -> None:
    s = get_settings()
    db = Path(s.db_path)
    if "--keep" not in sys.argv and db.exists():
        db.unlink()
        print(f"Deleted {db}")
    led = Ledger(str(db))
    import json
    led.kv_set("policy", json.dumps({"policy": default_policy(s.policy_timezone).model_dump(mode="json"),
                                     "rules_text": DEFAULT_RULES_TEXT}))
    print("Default rules restored. Start the app with: uvicorn app.main:app")


if __name__ == "__main__":
    main()
