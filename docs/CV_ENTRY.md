# CV entry: AgentPay Guard

## Resume (Industrial / ATS: Jake's Resume format)

**AgentPay Guard** | Python, FastAPI, PayPal Orders v2, LLM agents (Ollama), MCP, SQLite, Playwright | Nov 2026
*PayPal AI Hackathon 2026 (Devpost)* · github.com/RayonBiswas/agentpay-guard

- Built a **spending-guardrail layer for AI shopping agents**: an LLM compiles plain-English rules into a validated policy, and a **deterministic policy engine** (10 rule types incl. timezone-aware budgets and quiet hours) approves, holds or blocks every purchase before payment.
- Integrated **PayPal Orders v2** end to end (OAuth, idempotent create/capture/refund, JS SDK checkout, **signature-verified webhooks**) with amount and ownership checks, so the LLM can never set a price or bypass a limit.
- Exposed the guard to any AI agent through an **MCP server** and a REST intent API; shipped with **110 automated tests** (pytest, mocked PayPal/LLM, Playwright UI runs), Docker/Render deploy and a Postman collection. *[Add result: e.g. "Finalist / winner, PayPal AI Hackathon" or "N users / stars" once known.]*

## Elegant (Awesome-CV) variant

**AgentPay Guard** — Spending guardrails for AI shopping agents, paid via PayPal · *Nov 2026*
- LLM turns plain-English rules into policy; deterministic engine makes every money decision; PayPal Orders v2 + webhooks; MCP server for third-party agents; 110 tests.

## LinkedIn / Upwork portfolio blurb

> **AgentPay Guard: AI agents can shop for you, but only within your rules.**
> I built a payment-guardrail layer for agentic commerce at the PayPal AI Hackathon. You state spending rules in plain English, an LLM compiles them into exact limits, and deterministic code (not the AI) approves, holds or blocks every purchase before it's paid through PayPal. It includes PayPal Orders v2 + webhooks, an MCP server so any AI agent can plug in, a full audit trail and 110 tests.
> **Skills shown:** LLM agent design with safety guardrails · payment API integration · FastAPI backends · MCP tooling · test-driven development.

## ATS keywords

Python, FastAPI, REST API, Pydantic, SQLite, PayPal API, payment integration, webhooks, OAuth 2.0, idempotency, LLM, AI agents, agentic AI, Model Context Protocol (MCP), Ollama, prompt engineering, guardrails, pytest, Playwright, test automation, Docker, Render, JavaScript, HTML/CSS, Git
