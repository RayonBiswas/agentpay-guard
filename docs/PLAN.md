# AgentPay Guard: Plan

> **AI agents can shop for you. AgentPay Guard makes sure they only spend what you allow.**
> You write spending rules in plain English, and an LLM compiles them into a strict policy. Agents send purchase intents, deterministic code approves, holds or blocks each one, and approved intents are paid through PayPal. Every decision is logged and explained.

Event: **PayPal AI Hackathon**, "Build What's Next with PayPal and AI" (Devpost, open track)
Deadline: **Thu 12 Nov 2026, 12:00 PT = Fri 13 Nov, 01:30 IST** · Target submit: **Thu 12 Nov, 21:00 IST**
Owner: Rayon Biswas (solo)

---

## 1. Rules → what we must show

| Rule / criterion | How AgentPay Guard meets it | Evidence for judges |
|---|---|---|
| **Stage 1 (pass/fail):** PayPal central | Every approved purchase is a PayPal Orders v2 order (create → approve → capture), with refunds on cancel | Order IDs in the audit log, plus the sandbox dashboard in the video |
| **Stage 1:** AI meaningfully used | The LLM compiles plain-English rules into a policy, turns chat into purchase intents, picks products and explains decisions | Side-by-side in the UI: "your words → compiled policy JSON" |
| Technological Implementation | FastAPI service, typed policy engine, PayPal client with simulation mode, pytest suite, validated LLM output with heuristic fallback | Test count badge, architecture diagram |
| Design | One clean dashboard with Chat, Rules, Approvals, Ledger and a live budget meter | Screenshots + hero GIF |
| Potential Impact | Agentic commerce needs guardrails before anyone hands an agent their wallet; this is a reusable layer any agent can call | README problem section, API for other agents |
| Innovation / Idea | "Policy-as-language" with a deterministic gate: the LLM never makes the final money decision | Video moment where the agent tries to overspend and gets blocked |
| Presentation | Video under 3 minutes with a clear story: setup → happy path → blocked attempt → approval → ledger | YouTube link |
| Deliverables | Public repo with MIT licence, working demo (run in 2 minutes + optional hosted URL), YouTube video under 3 minutes, description of AI tools used | Submission checklist §6 |
| Existing-project rule | Brand-new repo started 4 Nov 2026 (inside the submission period) | Git history |

**Awards targeted:** Best Use of Agentic Commerce ($5k) → Best Use of PayPal + AI ($5k) → overall placing. Sponsor stack: **Render** (deploy the hosted demo, credits prize). Stretch: Postman collection for the API.
Only one Grand Prize or Honourable Mention per project, plus one sponsor prize.

---

## 2. How it works

```
User chat / external agent ──► Intent Agent (LLM) ──► PurchaseIntent{items, merchant, total, reason}
                                                            │
Plain-English rules ──► Rule Compiler (LLM) ──► Policy JSON (validated against a schema; user confirms)
                                                            ▼
                                        Policy Engine (pure Python, deterministic)
                                     ┌──────────────┼──────────────┐
                                  APPROVE          HOLD          BLOCK
                                     │       (needs human OK)      │
                                     ▼              ▼              ▼
                           PayPal Orders v2   Approvals queue   Logged with reason
                           create → approve → capture
                                     ▼
                         Ledger + audit log (SQLite) ◄── LLM explanation (display only)
```

**Safety principle:** the LLM proposes and the code decides. The LLM output is schema-validated, and if validation fails, a regex/heuristic parser takes over. Amounts are always recomputed from catalogue prices and never taken from LLM text.

**Policy rules supported (MVP):** per-item max · per-order max · daily/weekly/monthly budget · allowed/blocked categories · allowed/blocked merchants · "ask me above X" threshold · max quantity per item · quiet hours (no purchases between set times).

**PayPal flow:**
- *Interactive (MVP):* the server creates the order, the user approves it in the PayPal sandbox popup (JS SDK buttons), and the server captures it. HOLD items wait for this approval.
- *Autonomous (stretch):* the user saves PayPal once with Vault (payment tokens v3). Auto-APPROVE intents then charge the saved method with no popup. This is real agent-initiated payment.
- *Simulation mode:* when `PAYPAL_MODE=sim`, the client returns realistic fake order IDs and states, so the demo and tests run with no keys.

---

## 3. MVP checklist

### Must (by Sun 8 Nov)
- [ ] Project scaffold: FastAPI app, SQLite, `.env.example`, MIT licence, `requirements.txt`
- [ ] Policy model (Pydantic) + **policy engine** returning APPROVE / HOLD / BLOCK with every rule that fired
- [ ] Budget tracking over rolling windows from the ledger
- [ ] Mock catalogue (≈30 products, 5 categories, 3 merchants) as JSON
- [ ] **Rule Compiler**: LLM → policy JSON, with a heuristic fallback parser
- [ ] **Intent Agent**: chat → catalogue search → PurchaseIntent, with a heuristic fallback
- [ ] LLM client: OpenAI-compatible endpoint, default local Ollama, configured by env (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`)
- [ ] **PayPal client**: OAuth token, create order, capture order, refund; `sim` and `sandbox` modes
- [ ] REST API: `/rules/compile`, `/policy`, `/intents`, `/approvals/{id}`, `/ledger`, `/paypal/capture`
- [ ] Single-page UI with Chat · Rules · Approvals · Ledger tabs, a budget meter and the PayPal JS SDK button for HOLD items
- [ ] Tests: policy engine (each rule), budget windows, compiler fallback, agent fallback, PayPal client mocked, API happy path + blocked path

### Stretch (only after Must is green)
- [ ] Vault-saved payment for autonomous auto-approve purchases
- [ ] **External-agent API + MCP tool** (`request_purchase`) so any agent (later AutoBasket) can call the guard
- [ ] PayPal webhook listener (`CHECKOUT.ORDER.APPROVED`, `PAYMENT.CAPTURE.COMPLETED`)
- [ ] Hosted demo on Render (sim mode + a hosted OpenAI-compatible LLM)
- [ ] Postman collection
- [ ] Weekly spend summary written by the LLM

### Out of scope
Real merchants/scraping, multi-user auth, production PayPal, AutoBasket integration (later, separately).

---

## 4. Timeline (IST)

| Day | Date | Goal | Done when |
|---|---|---|---|
| D-8 | Wed 4 Nov | Intake, calendar, idea, **this plan** | PLAN.md approved |
| D-7 | Thu 5 Nov | Scaffold, policy engine, catalogue, ledger + tests | Engine tests green |
| D-6 | Fri 6 Nov | LLM client, Rule Compiler, Intent Agent (+ fallbacks) + tests | Chat → intent → decision in the terminal |
| D-5 | Sat 7 Nov | PayPal client (sim + sandbox), API routes + tests | Sandbox order captured end-to-end |
| D-4 | Sun 8 Nov | UI (four tabs, budget meter, PayPal button) | Full flow in the browser |
| D-3 | Mon 9 Nov | Playwright verification, bug fixes, polish; stretch: Vault / MCP | Screens clean, tests green |
| D-2 | Tue 10 Nov | **Record video**, GIF, screenshots, architecture PNG; optional Render deploy | Video uploaded (unlisted) |
| D-1 | Wed 11 Nov | README, CV entry, novelty gate, pre-public check, **draft Devpost submission** | Draft saved on Devpost |
| D-0 | Thu 12 Nov | Final fixes, repo public, video public, **submit by 21:00 IST** | Submitted ✅ |

Buffer: the cutoff is 01:30 IST on 13 Nov. Don't use that buffer unless something breaks.

---

## 5. Video script (target 2:45, hard cap 2:55)

| Time | Shot | Voice-over (approx.) |
|---|---|---|
| 0:00–0:15 | Title card → news-style text "AI agents can now buy things for you" | "AI agents are starting to shop for us. Would you hand one your PayPal with no limits? Neither would I." |
| 0:15–0:35 | Rules tab: typing plain-English rules → compiled policy appears | "With AgentPay Guard I write my rules in plain English. The AI compiles them into a strict policy, and I confirm it." |
| 0:35–1:05 | Chat: "Get me a USB-C cable and a notebook" → cart → **APPROVED** → PayPal sandbox order captured | "My agent finds the items. The policy engine, plain code and not the AI, approves them, and PayPal processes the payment." |
| 1:05–1:30 | Chat: "Buy the $300 headphones" → **BLOCKED** with the fired rules listed | "When the agent tries to overspend, it's blocked. The AI can explain the decision but can't override it." |
| 1:30–1:55 | $15 item over the ask-me threshold → **HOLD** → Approvals tab → PayPal button → captured | "Anything above my threshold waits for me. One tap in PayPal and it goes through." |
| 1:55–2:15 | Ledger + budget meter + PayPal sandbox dashboard showing the same order IDs | "Every decision is logged with its reason and PayPal order ID, and I can see my budget in real time." |
| 2:15–2:35 | Architecture diagram + "any agent can call this API" (+ MCP if built) | "It's a guard layer, so any shopping agent can plug in. The LLM plans and the code decides." |
| 2:35–2:45 | Repo URL, tech badges, name | "AgentPay Guard: agentic commerce with guardrails. Built on PayPal and AI. Thanks for watching." |

Recording notes: 1080p, no copyrighted music (YouTube Audio Library or silence), sandbox only, no real keys visible on screen.

---

## 6. Submission checklist (Devpost)

- [ ] Project name, tagline, cover image
- [ ] Text description: features, functionality, **what's new during the submission period**
- [ ] "Built with": PayPal Orders v2 (+ JS SDK, Vault if done), FastAPI, Python, Ollama/OpenAI-compatible LLM, SQLite, Render
- [ ] **AI tools used and how**: model name, what it does (rule compiling, intent parsing, explanations), what it is NOT allowed to do (final decisions, amounts)
- [ ] Public GitHub repo, MIT licence showing in About, run-in-2-minutes instructions (sim mode needs no keys)
- [ ] Optional hosted demo URL, free to test until 15 Dec 2026
- [ ] YouTube video, public, under 3 minutes
- [ ] Prize categories ticked: Best Use of Agentic Commerce, Best Use of PayPal + AI, Render
- [ ] Final read-through, then submit by **21:00 IST, 12 Nov**

---

## 7. Open items for Rayon
1. Create a free PayPal sandbox app at developer.paypal.com → send the **client ID + secret** (I'll put them in `.env`, never committed). Until then everything runs in sim mode.
2. Which local model to default to? (Proposal: whatever already runs well on your laptop in Ollama, e.g. a 3–8B instruct model. The heuristic fallback covers the rest.)
3. GitHub username (for the repo and badges).
