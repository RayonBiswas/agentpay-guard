# Demo video: recording guide

**Target:** 2:00–2:45 (hard limit: under 3:00). Public on YouTube. No copyrighted music.
**Guide cut:** `agentpay-guard-guide-video.mp4` (1:36, captioned, simulation mode). Follow its shots and pacing. It also works as a backup submission if the live recording fails.

## Before you hit record (5 min)

- [ ] `.env`: `PAYPAL_MODE=sandbox`, your keys, `LLM_MODEL=<your model>`, **`AUTOPAY=off`** (so the PayPal popup shows)
- [ ] `ollama serve` running; `python scripts/live_check.py` printed **ALL GOOD**
- [ ] `python scripts/demo_reset.py` → `uvicorn app.main:app` → open http://localhost:8000
- [ ] Header chips read **PayPal: Sandbox** and **AI: <your model>**
- [ ] Record **before 23:00 IST**: the demo rules include "no purchases 11pm–7am", which would block everything
- [ ] Browser: one tab, 100% zoom, full screen (F11), bookmarks bar hidden, notifications off (Windows Focus Assist)
- [ ] Logged out of PayPal in that browser, so the popup asks for the **sandbox buyer** login (have the email/password ready)
- [ ] Recorder: OBS (free) at 1920×1080, 30 fps, MP4; mic tested. Or record silent and add the voice-over after.
- [ ] Do one dry run. LLM replies can take a few seconds on CPU, so pause the voice-over or cut the wait in editing.

## Shot list + voice-over

| Time | Screen | Say (roughly) |
|---|---|---|
| 0:00–0:08 | Title card (or your face) | "AI agents can buy things for us now. Would you give one your wallet with no limits?" |
| 0:08–0:15 | App header | "This is AgentPay Guard. Your agent shops. Your rules decide." |
| 0:15–0:40 | **Rules** tab: type the rules → *Turn into limits* → highlighted changes → *Save* | "I write my spending rules in plain English. The AI (a local model running on my laptop) turns them into exact limits. Changes are highlighted, and nothing is saved until I confirm." |
| 0:40–1:05 | **Agent** tab: "Buy me a USB-C cable and a notebook" → APPROVED stamp → *Pay with PayPal* → **real sandbox popup** → log in as buyer → pay → "PAID · order …" | "My shopping agent picks the products, but plain code, not the AI, checks every rule. Prices come from the catalogue, never from the AI. Approved purchases go through PayPal." |
| 1:05–1:20 | "Buy noise cancelling headphones" → BLOCKED, the ✕ lines | "If the agent tries to overspend, it's blocked. Every broken rule is listed, and the AI can't argue its way past them." |
| 1:20–1:45 | "Get a desk lamp" → HELD → **Approvals** → *Approve* → back to Agent → pay in PayPal | "Above my ask-me limit, it waits for me. Approving re-checks my budget first, so two purchases can't sneak past it together. Then one tap in PayPal." |
| 1:45–2:00 | **Ledger** (order IDs, budget meter) → *Refund* one | "Every decision is logged with its reason and the PayPal order ID, and refunds go back through PayPal." |
| 2:00–2:10 | *(optional, strong)* **developer.paypal.com** → sandbox buyer/merchant activity showing the same order IDs | "And here are the same orders in PayPal's sandbox." |
| 2:10–2:25 | Architecture card | "Any AI agent can plug in through our MCP server or REST API, and the guard stays in charge. Webhooks are signature-verified, and there are 110 tests." |
| 2:25–2:35 | End card | "AgentPay Guard: the LLM plans, code decides, PayPal pays." |

The title, architecture and end cards are in `scripts/video/cards/` (open them in a browser and screenshot them), or cut them from the guide video.

## YouTube upload

**Title:** AgentPay Guard: spending guardrails for AI shopping agents (PayPal AI Hackathon 2026)

**Description:**
```
AI agents can shop for you. AgentPay Guard makes sure they only spend what you allow.

• Write spending rules in plain English; an LLM turns them into exact limits you confirm
• Your shopping agent (or any agent via MCP/REST) proposes purchases
• Deterministic code, not the AI, approves, holds or blocks each one
• Approved purchases are paid through PayPal Orders v2 (sandbox), with verified webhooks
• Full audit ledger, refunds, live budget meter

Code: https://github.com/RayonBiswas/agentpay-guard
Built by Rayon Biswas for the PayPal AI Hackathon 2026.
```
Visibility **Public**, "Not made for kids", and add English captions (YouTube can auto-generate them; check the product names).

## Recreating the guide video

`scripts/video/record.js` films the app scene by scene (Playwright, JPEG frames). See the comments at the top of that file and the ffmpeg commands in it. Start the app in sim mode with `AUTOPAY=off` on a fresh database first.
