# Devpost submission draft: AgentPay Guard

**Tagline (≤ 200 chars):** AI agents can shop for you. AgentPay Guard makes sure they only spend what you allow: plain-English rules, a deterministic policy engine, and PayPal payments.

**Prize categories to tick:** Best Use of Agentic Commerce · Best Use of PayPal + AI · Render (if deployed)

## Inspiration
Agents that buy things for us are coming fast, but nobody should hand an LLM their wallet with no limits. LLMs misread requests, invent products and prices, and can be prompt-injected. We wanted the payment layer to enforce limits that a person can state in their own words, with a final decision-maker that can't be talked out of them.

## What it does
- You write rules like "Max $50 per item, weekly budget $150, ask me above $20, no gaming, nothing between 11pm and 7am". The AI turns them into exact limits, and you confirm before anything is saved.
- A shopping agent (chat), or any AI agent over **MCP** or REST, proposes purchases as product IDs and quantities only.
- A **deterministic policy engine** approves, holds or blocks each purchase and prints a receipt listing every rule it checked. Prices always come from the catalogue, never from the AI.
- Approved purchases are paid through **PayPal Orders v2**. Held ones wait for you and are re-checked against your current spend when you approve.
- A full ledger with PayPal order and capture IDs, refunds, a live budget meter and signature-verified **PayPal webhooks**.

## How we built it
FastAPI + Pydantic + SQLite backend; PayPal Orders v2 REST (OAuth, idempotent create/capture/refund) with JS SDK buttons; webhook verification through PayPal's verify-webhook-signature API; an OpenAI-compatible LLM client (local Llama 3.2 via Ollama by default) with deterministic fallbacks for every AI step; an MCP server (MCP Python SDK 2.x); a vanilla JS/CSS single-page UI; 110 pytest tests plus Playwright browser runs.

## AI tools used and how
- **Rule compiler (LLM):** plain English → policy JSON. Validated by Pydantic, regex fallback, user confirms.
- **Shopping agent (LLM):** chat → catalogue product IDs and quantities. Unknown IDs dropped, no prices accepted.
- **Explanations (LLM):** one-sentence rewrite of the decision, discarded if it changes any number.
- The LLM never makes the money decision; plain code does.

## Challenges we ran into
Keeping the AI useful without letting it touch money: we had to design the boundary so the LLM's output can only ever be product IDs, quantities and a draft policy the user confirms. We also had to stop two held purchases from passing a budget together, which led to re-checking every approval against current spend.

## Accomplishments we're proud of
- A decision layer that can't be prompt-injected into overspending, demonstrated with tests that try (fake prices, hallucinated products, over-budget pairs).
- An MCP server, so the same guardrails protect any agent, not just ours.

## What we learned
PayPal Orders v2 lifecycle and idempotency, webhook verification, and that "LLM proposes, code decides" is a clean pattern for agentic commerce.

## What's next
PayPal Vault for fully autonomous approved payments, real merchant catalogues, per-agent budgets and keys, and using it as the payment layer for an IoT smart-pantry reorder system.

## New during the submission period
Everything. The repo was created on 4 Nov 2026 (see the git history).

## Links
- Repo: https://github.com/RayonBiswas/agentpay-guard
- Video: _YouTube link_
- Live demo: _Render URL (optional)_
