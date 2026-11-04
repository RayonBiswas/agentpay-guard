// AgentPay Guard UI: vanilla JS, no build step.
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const usd = (c) => (c == null ? "—" : "$" + (c / 100).toFixed(2));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const state = { config: null, policy: null, rulesText: "", draft: null, paypalReady: null };

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      method: opts.method || "GET",
      headers: opts.body ? { "Content-Type": "application/json" } : {},
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch { /* empty */ }
    if (!res.ok) {
      const d = data && data.detail;
      throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join("; ") : `Request failed (${res.status})`);
    }
    return data;
  }

  let toastTimer;
  function toast(msg, isError = false) {
    const t = $("#toast");
    t.textContent = msg; t.hidden = false; t.classList.toggle("error", isError);
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 4200);
  }

  // ------------------------------------------------------------ tabs
  function showTab(name) {
    $$(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    $$(".panel").forEach((p) => (p.hidden = p.id !== `panel-${name}`));
    if (name === "ledger") loadLedger();
    if (name === "approvals") loadApprovals();
    if (name === "rules") renderLimits();
  }
  $$(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

  // ------------------------------------------------------------ header
  async function loadConfig() {
    state.config = await api("/api/config");
    const pp = $("#chip-paypal");
    pp.textContent = state.config.paypal_mode === "sandbox" ? "PayPal: Sandbox" : "PayPal: Simulated";
    pp.classList.toggle("live", state.config.paypal_mode === "sandbox");
    const llm = state.config.llm;
    $("#chip-llm").textContent = llm.startsWith("heuristic") ? "AI: rule parser (no LLM)" : "AI: " + llm.split(" @ ")[0];
    $("#chip-llm").title = llm;
    if (state.config.paypal_mode === "sandbox" && state.config.paypal_client_id) loadPayPalSdk(state.config.paypal_client_id);
  }

  async function loadBudget() {
    const rows = await api("/api/budget");
    const m = $("#meter");
    if (!rows.length) { m.innerHTML = '<span class="muted small mono">No budget set</span>'; return; }
    const names = { day: "Today", week: "This week", month: "This month" };
    m.innerHTML = rows.map((b) => {
      const pct = Math.min(100, (b.spent_cents / b.limit_cents) * 100);
      const cls = pct >= 100 ? "over" : pct >= 75 ? "warn" : "";
      return `<div class="meter-item" title="${names[b.period]}: ${usd(b.spent_cents)} spent of ${usd(b.limit_cents)}">
        <div class="meter-label"><span>${names[b.period]}</span><span><strong>${usd(b.spent_cents)}</strong> / ${usd(b.limit_cents)}</span></div>
        <div class="meter-track" role="progressbar" aria-label="${names[b.period]} budget used" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct.toFixed(0)}"><div class="meter-fill ${cls}" style="width:${pct}%"></div></div>
      </div>`;
    }).join("");
  }

  async function refreshCounts() {
    const held = await api("/api/ledger?status=held");
    const b = $("#held-count");
    b.textContent = held.length; b.hidden = held.length === 0;
  }

  // ------------------------------------------------------------ slips
  function ruleLabel(rule, p) {
    const map = {
      known_products: "Every item is in the catalogue",
      non_empty: "Order has items",
      categories: "Categories allowed",
      merchants: "Merchants allowed",
      per_item_max: `Each item ≤ ${usd(p?.per_item_max_cents)}`,
      max_quantity: `At most ${p?.max_quantity_per_item} of each item`,
      per_order_max: `Order total ≤ ${usd(p?.per_order_max_cents)}`,
      budget_day: "Within today's budget",
      budget_week: "Within this week's budget",
      budget_month: "Within this month's budget",
      quiet_hours: "Outside quiet hours",
      ask_above: `Under your ask-me limit (${usd(p?.ask_above_cents)})`,
    };
    return map[rule] || rule;
  }

  // Template explanations just restate the rule list; AI rewrites and re-check notes add information.
  const isTemplate = (t) => /^(Approved: \$|Blocked \(|Waiting for you: \$)/.test(t || "") && !/You approved it|Rejected on re-check/.test(t);
  const STAMP = { APPROVE: "Approved", HOLD: "Held", BLOCK: "Blocked" };

  function slip(entry, { animate = false } = {}) {
    const d = entry.decision;
    const el = document.createElement("article");
    el.className = "slip" + (animate ? " shake" : "");
    el.dataset.entry = entry.id;
    const when = new Date(entry.created_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
    const lines = d.lines.map((l) => `<tr><td>${l.quantity} × ${esc(l.name)}<div class="sub">${esc(l.merchant)} · ${esc(l.category)} · ${usd(l.unit_cents)} each</div></td><td class="num">${usd(l.line_cents)}</td></tr>`).join("");
    const hitsByRule = {};
    d.hits.forEach((h) => (hitsByRule[h.rule] = hitsByRule[h.rule] || []).push(h));
    const checks = d.checked_rules.map((r) => {
      const hs = hitsByRule[r];
      if (!hs) return `<li>${esc(ruleLabel(r, state.policy))}</li>`;
      return hs.map((h) => `<li class="hit-${h.effect}">${esc(h.detail)}</li>`).join("");
    }).join("");
    el.innerHTML = `
      <div class="stamp ${d.outcome}${animate ? " land" : ""}" aria-hidden="true">${STAMP[d.outcome]}</div>
      <div class="slip-head"><span class="no">PURCHASE #${entry.id}</span><span>${esc(when)}</span></div>
      <div class="muted small">Requested by <strong>${esc(entry.agent)}</strong>${entry.reason ? `: “${esc(entry.reason)}”` : ""}</div>
      <table class="slip-lines" aria-label="Items">${lines || '<tr><td class="muted">No items</td></tr>'}</table>
      <div class="slip-total"><span>TOTAL</span><span>${usd(entry.total_cents)}</span></div>
      <ul class="slip-checks" aria-label="Rules checked">${checks}</ul>
      ${isTemplate(entry.explanation) ? `<p class="sr-only">Verdict: ${STAMP[d.outcome]}</p>` : `<p class="slip-why"><span class="sr-only">Verdict: ${STAMP[d.outcome]}. </span>${esc(entry.explanation)}</p>`}
      <div class="slip-pay"></div>`;
    renderPayArea(el, entry);
    return el;
  }

  function renderPayArea(el, entry) {
    const area = $(".slip-pay", el);
    area.innerHTML = "";
    const s = entry.status;
    if (s === "paid") {
      area.innerHTML = `<span class="paid">PAID through PayPal · order ${esc(entry.paypal_order_id)}</span>`;
    } else if (s === "approved" || s === "failed") {
      area.innerHTML = `<span class="small muted">${s === "failed" ? "Payment didn't go through. Try again:" : "Allowed by your rules. Pay to complete:"}</span><div class="paypal-slot"></div>`;
      mountPayButton($(".paypal-slot", area), entry);
    } else if (s === "held") {
      area.innerHTML = `<span class="small muted">Waiting for you:</span>
        <button class="btn approve" data-act="approve">Approve</button>
        <button class="btn danger" data-act="reject">Reject</button>`;
      $('[data-act="approve"]', area).onclick = () => act(entry.id, "approve");
      $('[data-act="reject"]', area).onclick = () => act(entry.id, "reject");
    } else if (s === "cancelled") {
      area.innerHTML = `<span class="small muted">Refunded through PayPal.</span>`;
    } else if (s === "rejected") {
      area.innerHTML = `<span class="small muted">Rejected. Nothing was charged.</span>`;
    } else {
      area.innerHTML = `<span class="small muted">Not paid. Your rules stopped this purchase.</span>`;
    }
  }

  async function act(id, what) {
    try {
      const e = await api(`/api/entries/${id}/${what}`, { method: "POST" });
      toast(what === "approve" ? (e.status === "paid" ? `Approved and paid: purchase #${id}` : `Approved: purchase #${id}`) : `Rejected: purchase #${id}`);
      replaceSlips(e);
    } catch (err) {
      toast(err.message, true);
      const e = await api(`/api/entries/${id}`); replaceSlips(e);
    }
    await Promise.all([loadBudget(), refreshCounts()]);
    if (!$("#panel-approvals").hidden) loadApprovals();
  }

  function replaceSlips(entry) {
    $$(`.slip[data-entry="${entry.id}"]`).forEach((old) => old.replaceWith(slip(entry)));
  }

  // ------------------------------------------------------------ PayPal
  function loadPayPalSdk(clientId) {
    state.paypalReady = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = `https://www.paypal.com/sdk/js?client-id=${encodeURIComponent(clientId)}&currency=USD&intent=capture&components=buttons`;
      s.onload = () => resolve(window.paypal);
      s.onerror = () => reject(new Error("Couldn't load the PayPal SDK"));
      document.head.appendChild(s);
    });
  }

  async function mountPayButton(slot, entry) {
    if (state.config.paypal_mode === "sandbox" && state.paypalReady) {
      try {
        const paypal = await state.paypalReady;
        paypal.Buttons({
          style: { layout: "horizontal", tagline: false, height: 40 },
          createOrder: async () => (await api(`/api/entries/${entry.id}/paypal/order`, { method: "POST" })).order_id,
          onApprove: async (data) => {
            const e = await api(`/api/entries/${entry.id}/paypal/capture`, { method: "POST", body: { order_id: data.orderID } });
            toast(`Paid: purchase #${entry.id}`); replaceSlips(e); loadBudget();
          },
          onError: (err) => toast(String(err?.message || err), true),
        }).render(slot);
        return;
      } catch (err) { toast(err.message, true); }
    }
    const btn = document.createElement("button");
    btn.className = "btn primary"; btn.textContent = `Pay ${usd(entry.total_cents)} with PayPal`;
    btn.onclick = () => simCheckout(entry, btn);
    slot.appendChild(btn);
  }

  async function simCheckout(entry, btn) {
    btn.disabled = true;
    try {
      const order = await api(`/api/entries/${entry.id}/paypal/order`, { method: "POST" });
      $("#sim-amount").textContent = usd(entry.total_cents);
      $("#sim-order").textContent = order.order_id;
      const dlg = $("#sim-checkout");
      dlg.returnValue = "";
      dlg.showModal();
      $("#sim-pay").focus();
      await new Promise((r) => dlg.addEventListener("close", r, { once: true }));
      if (dlg.returnValue !== "pay") { toast("Payment cancelled. Nothing was charged."); return; }
      await api(`/api/sim/paypal/${order.order_id}/approve`, { method: "POST" });
      const e = await api(`/api/entries/${entry.id}/paypal/capture`, { method: "POST", body: { order_id: order.order_id } });
      toast(`Paid: purchase #${entry.id}`);
      replaceSlips(e); loadBudget();
    } catch (err) { toast(err.message, true); }
    finally { btn.disabled = false; }
  }

  // ------------------------------------------------------------ chat
  function addMsg(text, who, src) {
    const m = document.createElement("div");
    m.className = `msg ${who}`;
    m.textContent = text;
    if (src) { const s = document.createElement("span"); s.className = "src"; s.textContent = src; m.appendChild(s); }
    $("#chat-log").appendChild(m);
    $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
  }

  async function send(message) {
    addMsg(message, "user");
    const input = $("#chat-input"); input.value = ""; input.disabled = true;
    try {
      const r = await api("/api/chat", { method: "POST", body: { message } });
      addMsg(r.reply, "bot", r.source === "llm" ? "picked by AI" : "picked by keyword search");
      if (r.entry) {
        const stage = $("#slip-stage");
        stage.innerHTML = "";
        stage.appendChild(slip(r.entry, { animate: true }));
        addMsg(r.entry.explanation, "bot");
        await Promise.all([loadBudget(), refreshCounts()]);
      }
    } catch (err) { addMsg(err.message, "bot"); }
    finally { input.disabled = false; input.focus(); }
  }

  $("#chat-form").addEventListener("submit", (e) => { e.preventDefault(); const v = $("#chat-input").value.trim(); if (v) send(v); });
  $$("#suggestions button").forEach((b) => b.addEventListener("click", () => send(b.dataset.say)));

  // ------------------------------------------------------------ rules
  function limitRows(p) {
    const list = (v) => (v && v.length ? v.join(", ") : null);
    const budget = (per) => p.budgets.find((b) => b.period === per)?.limit_cents;
    return [
      ["Each item at most", p.per_item_max_cents != null ? usd(p.per_item_max_cents) : null],
      ["Each order at most", p.per_order_max_cents != null ? usd(p.per_order_max_cents) : null],
      ["Daily budget", budget("day") != null ? usd(budget("day")) : null],
      ["Weekly budget", budget("week") != null ? usd(budget("week")) : null],
      ["Monthly budget", budget("month") != null ? usd(budget("month")) : null],
      ["Ask me above", p.ask_above_cents != null ? usd(p.ask_above_cents) : null],
      ["Max of each item", p.max_quantity_per_item],
      ["Only these categories", list(p.allowed_categories)],
      ["Never these categories", list(p.blocked_categories)],
      ["Only these merchants", list(p.allowed_merchants)],
      ["Never these merchants", list(p.blocked_merchants)],
      ["No purchases between", p.quiet_hours ? `${p.quiet_hours.start}–${p.quiet_hours.end}` : null],
    ];
  }

  function renderLimits() {
    const showing = state.draft ? state.draft.policy : state.policy;
    if (!showing) return;
    const base = limitRows(state.policy);
    const rows = limitRows(showing);
    $("#limits").innerHTML = rows.map(([k, v], i) => {
      const changed = state.draft && String(v) !== String(base[i][1]);
      const empty = k.startsWith("Only") || k.startsWith("Never") || k.startsWith("No purchases") ? "none" : "no limit";
      return `<dt>${k}</dt><dd class="${v == null ? "none" : ""}${changed ? " changed" : ""}">${v == null ? empty : esc(v)}</dd>`;
    }).join("");
    $("#policy-json").textContent = JSON.stringify(showing, null, 2);
    $("#save-btn").hidden = $("#discard-btn").hidden = !state.draft;
    const cap = $("#limits-caption");
    if (state.draft) {
      const src = state.draft.source === "llm" ? "Read by the AI" : "Read by the rule parser";
      cap.innerHTML = `${src}. Changes are highlighted. Check them, then save.` +
        (state.draft.notes?.length ? `<ul class="notes">${state.draft.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : "");
    } else cap.textContent = "These are the limits in force now.";
  }

  async function loadPolicy() {
    const r = await api("/api/policy");
    state.policy = r.policy; state.rulesText = r.rules_text;
    if (!$("#rules-text").value) $("#rules-text").value = r.rules_text;
    renderLimits();
  }

  $("#compile-btn").addEventListener("click", async () => {
    const text = $("#rules-text").value.trim();
    if (!text) { toast("Write at least one rule first.", true); return; }
    const btn = $("#compile-btn"); btn.disabled = true; $("#compile-status").textContent = "Reading your rules…";
    try {
      state.draft = await api("/api/rules/compile", { method: "POST", body: { text } });
      $("#compile-status").textContent = "";
      renderLimits();
    } catch (err) { $("#compile-status").textContent = ""; toast(err.message, true); }
    finally { btn.disabled = false; }
  });

  $("#save-btn").addEventListener("click", async () => {
    try {
      const r = await api("/api/policy", { method: "PUT", body: { policy: state.draft.policy, rules_text: $("#rules-text").value.trim() } });
      state.policy = r.policy; state.draft = null;
      renderLimits(); loadBudget();
      toast("Limits saved. Your agent follows them from now on.");
    } catch (err) { toast(err.message, true); }
  });
  $("#discard-btn").addEventListener("click", () => { state.draft = null; renderLimits(); });

  // ------------------------------------------------------------ approvals
  async function loadApprovals() {
    const held = await api("/api/ledger?status=held");
    const wrap = $("#approvals"); wrap.innerHTML = "";
    if (!held.length) {
      wrap.innerHTML = `<div class="card empty-note"><h2>Nothing waiting</h2><p class="hint">Purchases above your ask-me limit appear here for you to approve or reject.</p></div>`;
      return;
    }
    held.forEach((e) => wrap.appendChild(slip(e)));
  }

  // ------------------------------------------------------------ ledger
  async function loadLedger() {
    const rows = await api("/api/ledger");
    const body = $("#ledger-body");
    if (!rows.length) { body.innerHTML = `<tr><td colspan="9" class="muted">No purchases yet. Ask the agent to buy something.</td></tr>`; return; }
    body.innerHTML = rows.map((e) => `<tr>
      <td class="mono">${e.id}</td>
      <td class="small">${esc(new Date(e.created_at).toLocaleString([], { dateStyle: "short", timeStyle: "short" }))}</td>
      <td>${esc(e.agent)}</td>
      <td>${e.decision.lines.map((l) => `${l.quantity} × ${esc(l.name)}`).join("<br>") || "—"}</td>
      <td class="num">${usd(e.total_cents)}</td>
      <td><span class="mini-stamp ${e.outcome}">${STAMP[e.outcome].toUpperCase()}</span></td>
      <td><span class="status ${e.status}">${e.status}</span></td>
      <td class="mono">${esc(e.paypal_order_id || "—")}</td>
      <td>${e.status === "paid" ? `<button class="btn ghost small" data-refund="${e.id}">Refund</button>` : ""}</td>
    </tr>`).join("");
    $$("[data-refund]", body).forEach((b) => b.addEventListener("click", async () => {
      b.disabled = true;
      try { await api(`/api/entries/${b.dataset.refund}/refund`, { method: "POST" }); toast(`Refunded: purchase #${b.dataset.refund}`); }
      catch (err) { toast(err.message, true); }
      loadLedger(); loadBudget();
    }));
  }

  // ------------------------------------------------------------ boot
  (async () => {
    try {
      await loadConfig();
      await loadPolicy();
      await Promise.all([loadBudget(), refreshCounts()]);
    } catch (err) { toast("Couldn't reach the server: " + err.message, true); }
  })();
})();
