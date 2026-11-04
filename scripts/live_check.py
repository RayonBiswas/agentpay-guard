"""Live check against the real PayPal sandbox and your local LLM.

    python scripts/live_check.py            # both checks
    python scripts/live_check.py paypal     # PayPal only
    python scripts/live_check.py llm        # LLM only

Reads PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET / LLM_* from .env.
The PayPal check creates a real sandbox order, asks you to approve it with your
sandbox *buyer* account in the browser, then captures and refunds it.
"""
from __future__ import annotations

import sys
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.catalog import Catalog  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.llm import LLMClient  # noqa: E402
from app.agent import parse_request  # noqa: E402
from app.models import IntentItem, Policy, PurchaseIntent  # noqa: E402
from app.paypal_client import (PayPalError, SandboxPayPal, approve_link, build_order_body,  # noqa: E402
                               capture_id, new_request_id)
from app.policy_engine import evaluate  # noqa: E402
from app.rule_compiler import compile_rules  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

OK, BAD = "✅", "❌"


def check_paypal() -> bool:
    s = get_settings()
    print("\n== PayPal sandbox ==")
    try:
        pp = SandboxPayPal(s.paypal_client_id, s.paypal_client_secret)
        pp._access_token()
        print(f"{OK} OAuth token received")
    except PayPalError as e:
        print(f"{BAD} {e} {e.details or ''}")
        return False
    cat = Catalog.load(s.catalog_path)
    intent = PurchaseIntent(items=[IntentItem(product_id="usb-c-cable"), IntentItem(product_id="a5-notebook")])
    d = evaluate(intent, Policy(), cat, {}, datetime.now(timezone.utc))
    order = pp.create_order(build_order_body(0, d.lines, d.total_cents), new_request_id("livecheck"))
    url = approve_link(order)
    print(f"{OK} Order {order['id']} created for ${d.total_cents/100:.2f} (status {order['status']})")
    print(f"\n   Open this link, log in with your SANDBOX BUYER account and click Pay/Continue:\n   {url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    for _ in range(60):  # wait up to 5 min
        status = pp.get_order(order["id"]).get("status")
        if status == "APPROVED":
            break
        time.sleep(5)
    else:
        print(f"{BAD} Order was not approved within 5 minutes (status {status})")
        return False
    print(f"{OK} Buyer approved")
    cap = pp.capture_order(order["id"], new_request_id("livecheck-cap"))
    cid = capture_id(cap)
    print(f"{OK} Captured: status {cap['status']}, capture {cid}")
    ref = pp.refund_capture(cid, new_request_id("livecheck-ref"))
    print(f"{OK} Refunded: {ref.get('status')}")
    print("   You can see this order in developer.paypal.com > Testing Tools > Sandbox Accounts > buyer > activity")
    return True


def check_llm() -> bool:
    s = get_settings()
    print("\n== LLM ==")
    llm = LLMClient(s.llm_base_url, s.llm_model, s.llm_api_key, timeout=120)
    if not llm.enabled:
        print(f"{BAD} LLM_BASE_URL / LLM_MODEL not set in .env")
        return False
    print(f"   Using {llm.label}")
    cat = Catalog.load(s.catalog_path)
    t = time.time()
    r = compile_rules("Max $50 per item and $100 per order. Weekly budget $150. Ask me before anything "
                      "over $20. No gaming or gift cards. No purchases between 11pm and 7am.", cat, llm, s.policy_timezone)
    p = r.policy
    good = (r.source == "llm" and p.per_item_max_cents == 5000 and p.ask_above_cents == 2000
            and "gaming" in p.blocked_categories)
    print(f"{OK if good else BAD} Rule compiler: source={r.source} ({time.time()-t:.1f}s) "
          f"item_max={p.per_item_max_cents} ask_above={p.ask_above_cents} blocked={p.blocked_categories}"
          + (f"  llm_error={r.llm_error}" if r.llm_error else ""))
    t = time.time()
    a = parse_request("get 3 oat milk and two gel pens", cat, llm)
    items = [(i.product_id, i.quantity) for i in a.intent.items] if a.intent else []
    good2 = a.source == "llm" and ("oat-milk", 3) in items and ("gel-pens", 2) in items
    print(f"{OK if good2 else BAD} Shopping agent: source={a.source} ({time.time()-t:.1f}s) items={items}")
    if not (good and good2):
        print("   Tip: if source=heuristic, the model failed or returned bad JSON; try a stronger model "
              "(e.g. qwen2.5:7b or llama3.1:8b) or check `ollama serve` is running.")
    return good and good2


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    results = []
    if which in ("all", "llm"):
        results.append(check_llm())
    if which in ("all", "paypal"):
        results.append(check_paypal())
    print("\nALL GOOD" if all(results) else "\nSOME CHECKS FAILED: send me the output")
    sys.exit(0 if all(results) else 1)
