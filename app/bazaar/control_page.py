"""Control Tower — single-page governance console served at GET /control.

Presentation edge only: the page speaks to the same public JSON APIs agents
use (/governance/*, /orders, /audit/recent, /catalog, /healthz). No new
business logic; approve/execute still flow through bazaar.proposals.
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chai Bazaar · Control Tower</title>
<style>
  :root {
    --bg: #12100d; --panel: #1c1915; --panel2: #242019; --line: #35302a;
    --ink: #ede7dd; --dim: #9a9184; --amber: #e8863a; --amber-dim: #b3541e;
    --ok: #58b368; --bad: #d95f5f; --info: #6aa7c8;
    font-size: 15px;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font-family: ui-sans-serif, system-ui, "Segoe UI", sans-serif;
  }
  code, .mono, td.hash, .pid { font-family: ui-monospace, Consolas, monospace; }
  header {
    display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
    padding: 14px 22px; border-bottom: 1px solid var(--line);
    background: linear-gradient(180deg, #1a1712, var(--bg));
  }
  header h1 { font-size: 1.05rem; margin: 0; letter-spacing: .06em; }
  header h1 span { color: var(--amber); }
  .pill {
    font-size: .72rem; padding: 3px 10px; border-radius: 999px;
    border: 1px solid var(--line); color: var(--dim); white-space: nowrap;
  }
  .pill.ok   { color: var(--ok);  border-color: var(--ok); }
  .pill.bad  { color: var(--bad); border-color: var(--bad); }
  main { padding: 18px 22px 60px; max-width: 1280px; margin: 0 auto; }
  .grid { display: grid; grid-template-columns: 3fr 2fr; gap: 16px; }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
  section {
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 10px; overflow: hidden; margin-bottom: 16px;
  }
  section h2 {
    margin: 0; padding: 11px 16px; font-size: .8rem; letter-spacing: .1em;
    text-transform: uppercase; color: var(--dim);
    border-bottom: 1px solid var(--line); background: var(--panel2);
    display: flex; justify-content: space-between; align-items: center;
  }
  table { width: 100%; border-collapse: collapse; font-size: .82rem; }
  th {
    text-align: left; color: var(--dim); font-weight: 500; font-size: .7rem;
    text-transform: uppercase; letter-spacing: .07em;
    padding: 7px 12px; border-bottom: 1px solid var(--line);
  }
  td { padding: 8px 12px; border-bottom: 1px solid #26221c; vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  .chip {
    display: inline-block; font-size: .68rem; padding: 2px 8px;
    border-radius: 999px; border: 1px solid var(--line); color: var(--dim);
    white-space: nowrap;
  }
  .chip.pending_review { color: var(--amber); border-color: var(--amber-dim); }
  .chip.approved, .chip.paid, .chip.auto_executed-ok { color: var(--ok); border-color: var(--ok); }
  .chip.rejected, .chip.failed { color: var(--bad); border-color: var(--bad); }
  .chip.auto_executed, .chip.executed { color: var(--info); border-color: var(--info); }
  .chip.created { color: var(--dim); }
  .rule {
    display: inline-block; font-family: ui-monospace, monospace;
    font-size: .68rem; color: var(--amber); background: #2a2016;
    border: 1px solid var(--amber-dim); border-radius: 4px;
    padding: 1px 6px; margin-right: 4px;
  }
  button {
    font: inherit; font-size: .75rem; cursor: pointer; border-radius: 6px;
    padding: 4px 12px; border: 1px solid var(--line);
    background: var(--panel2); color: var(--ink);
  }
  button:hover { border-color: var(--dim); }
  button.go { border-color: var(--ok); color: var(--ok); }
  button.stop { border-color: var(--bad); color: var(--bad); }
  button.exec { border-color: var(--info); color: var(--info); }
  button:disabled { opacity: .45; cursor: default; }
  .clamp { color: var(--amber); }
  .asked { color: var(--dim); text-decoration: line-through; }
  details { padding: 10px 16px; border-bottom: 1px solid var(--line); }
  summary { cursor: pointer; color: var(--dim); font-size: .8rem; }
  textarea, input, select {
    width: 100%; background: var(--bg); color: var(--ink);
    border: 1px solid var(--line); border-radius: 6px;
    padding: 7px 9px; font: inherit; font-size: .8rem;
  }
  textarea.mono { font-family: ui-monospace, monospace; min-height: 70px; }
  .row2 { display: flex; gap: 8px; margin-top: 8px; align-items: center; }
  .row2 > * { flex: 1; }
  .row2 button { flex: 0 0 auto; }
  .msg { font-size: .78rem; margin-top: 8px; min-height: 1em; color: var(--dim); }
  .msg.ok { color: var(--ok); } .msg.bad { color: var(--bad); }
  .empty { padding: 18px 16px; color: var(--dim); font-size: .82rem; }
  .flash { animation: f 1.2s ease-out; }
  @keyframes f { from { background: #2a2318; } to { background: transparent; } }
</style>
</head>
<body>
<header>
  <h1>CHAI BAZAAR <span>· CONTROL TOWER</span></h1>
  <span class="pill" id="p-env">env …</span>
  <span class="pill" id="p-db">db …</span>
  <span class="pill" id="p-rzp">razorpay …</span>
  <span class="pill" id="p-wh">webhook secret …</span>
  <span class="pill" id="p-llm">llm …</span>
  <span class="pill" id="p-chain">chain …</span>
</header>

<main>
  <div class="grid">
    <div>
      <section>
        <h2>Proposals — human gate <span id="pending-count"></span></h2>
        <details>
          <summary>Propose an action manually (watch the policy engine bound it)</summary>
          <div style="padding-top:10px">
            <select id="m-action">
              <option value="apply_discount">apply_discount</option>
              <option value="create_bundle">create_bundle</option>
              <option value="restock_alert">restock_alert</option>
              <option value="send_offer">send_offer</option>
            </select>
            <textarea id="m-params" class="mono">{"sku": "masala-chai-250g", "percent_off": 40, "days": 30}</textarea>
            <div class="row2">
              <input id="m-actor" value="demo-judge">
              <button class="go" onclick="manualPropose()">Submit proposal</button>
            </div>
            <div class="msg" id="m-msg"></div>
          </div>
        </details>
        <div id="proposals"><div class="empty">loading…</div></div>
      </section>

      <section>
        <h2>Audit ledger <span class="mono" style="text-transform:none" id="chain-note"></span></h2>
        <div id="audit"><div class="empty">loading…</div></div>
      </section>
    </div>

    <div>
      <section>
        <h2>Live orders</h2>
        <div id="orders"><div class="empty">loading…</div></div>
      </section>
      <section>
        <h2>Catalog prices (public view)</h2>
        <div id="catalog"><div class="empty">loading…</div></div>
      </section>
    </div>
  </div>
</main>

<script>
"use strict";
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const rs = p => "₹" + (p / 100).toFixed(2);
const ago = t => {
  const s = Math.max(0, (Date.now() - new Date(t)) / 1000);
  return s < 90 ? Math.round(s) + "s ago"
       : s < 7200 ? Math.round(s / 60) + "m ago"
       : Math.round(s / 3600) + "h ago";
};
async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || r.status);
  return body;
}
const post = (path, body) => api(path, {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify(body || {}),
});

/* ---------------- health ---------------- */
async function pollHealth() {
  try {
    const h = await api("/healthz");
    $("p-env").textContent = "env " + h.env;
    $("p-db").textContent = h.db;
    $("p-db").className = "pill " + (h.status === "ok" ? "ok" : "bad");
    $("p-rzp").textContent = h.razorpay_configured ? "razorpay keys set" : "razorpay MISSING";
    $("p-rzp").className = "pill " + (h.razorpay_configured ? "ok" : "bad");
    $("p-wh").textContent = h.webhook_secret_set ? "webhook secret set" : "webhook MISSING";
    $("p-wh").className = "pill " + (h.webhook_secret_set ? "ok" : "bad");
    $("p-llm").textContent = "llm " + h.llm_provider;
  } catch { $("p-env").textContent = "core unreachable"; $("p-env").className = "pill bad"; }
}

/* ---------------- proposals ---------------- */
function paramSpan(p) {
  if (!p) return "";
  const bits = Object.entries(p).map(([k, v]) =>
    k === "new_price_paise" ? `new ${rs(v)}` : `${esc(k)} ${esc(v)}`);
  return bits.join(" · ");
}
function proposalCard(p) {
  let d = {};
  try { d = JSON.parse(p.decision_json || "{}"); } catch {}
  const clamped = d.status === "clamp";
  const rules = (d.rule_ids || []).map(r => `<span class="rule">${esc(r)}</span>`).join("");
  const reason = (d.reasons || []).join("; ");
  const human = p.human_decision
    ? `<div style="margin-top:4px;color:var(--dim)">human (${esc(p.decided_by)}): ${esc(p.human_decision)}</div>` : "";
  let actions = "";
  if (p.status === "pending_review") {
    actions = `<button class="go" onclick="decide('${p.id}',true)">Approve</button>
               <button class="stop" onclick="decide('${p.id}',false)">Reject</button>`;
  } else if (p.status === "approved") {
    actions = `<button class="exec" onclick="execute('${p.id}')">Execute</button>`;
  }
  return `<tr class="flash">
    <td>
      <div><span class="chip ${esc(p.status)}">${esc(p.status)}</span>
           <b>${esc(p.action_type)}</b> <span style="color:var(--dim)">by ${esc(p.actor)}</span>
           <span style="float:right;color:var(--dim)">${ago(p.created_at)}</span></div>
      <div style="margin-top:4px">
        <span class="${clamped ? "clamp" : ""}">${esc(paramSpan(d.final_params))}</span>
      </div>
      ${reason ? `<div style="color:var(--dim);margin-top:3px">${esc(reason)}</div>` : ""}
      <div style="margin-top:4px">${rules}
        <span class="mono pid" style="color:var(--dim);font-size:.7rem">${esc(p.id)} · corr ${esc(p.correlation_id)}</span></div>
      ${human}
    </td>
    <td style="white-space:nowrap;text-align:right">${actions}</td>
  </tr>`;
}
async function pollProposals() {
  try {
    const data = await api("/governance/proposals?limit=25");
    const list = data.proposals || [];
    const pend = list.filter(p => p.status === "pending_review").length;
    $("pending-count").textContent = pend ? `${pend} awaiting human` : "";
    $("proposals").innerHTML = list.length
      ? `<table>${list.map(proposalCard).join("")}</table>`
      : '<div class="empty">no proposals yet — run scripts/run_growth_agent.py or submit one above</div>';
  } catch (e) { $("proposals").innerHTML = `<div class="empty bad">${esc(e.message)}</div>`; }
}
async function decide(id, ok) {
  await post(`/governance/proposals/${id}/decide`,
             {decided_by: "human@control-tower", approved: ok});
  pollProposals();
}
async function execute(id) {
  await post(`/governance/proposals/${id}/execute`);
  pollProposals(); pollCatalog(); setTimeout(pollAudit, 400);
}
async function manualPropose() {
  const msg = $("m-msg");
  try {
    const params = JSON.parse($("m-params").value);
    const res = await post("/governance/proposals", {
      actor: $("m-actor").value || "anonymous",
      action_type: $("m-action").value, params,
    });
    msg.className = "msg ok";
    msg.textContent = `policy verdict: ${res.decision.status}` +
      (res.decision.rule_ids?.length ? ` (${res.decision.rule_ids.join(", ")})` : "");
    pollProposals();
  } catch (e) { msg.className = "msg bad"; msg.textContent = e.message; }
}

/* ---------------- orders ---------------- */
async function pollOrders() {
  try {
    const data = await api("/orders?limit=14");
    const rows = (data.orders || []).map(o => `
      <tr>
        <td><span class="chip ${esc(o.status)}">${esc(o.status)}</span></td>
        <td><span class="mono">${esc(o.id.slice(0, 14))}</span>
            <div style="color:var(--dim);font-size:.72rem">${esc(o.channel)} · ${esc(o.buyer_session_id.slice(0, 20))}</div></td>
        <td style="text-align:right">${rs(o.total_paise)}
            <div style="color:var(--dim);font-size:.72rem">${ago(o.created_at)}</div></td>
      </tr>`).join("");
    $("orders").innerHTML = rows
      ? `<table>${rows}</table>` : '<div class="empty">no orders yet</div>';
  } catch (e) { $("orders").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

/* ---------------- audit ---------------- */
async function pollAudit() {
  try {
    const data = await api("/audit/recent?limit=30");
    $("p-chain").textContent = data.chain_ok
      ? `chain verified · ${data.records_checked}`
      : `CHAIN BROKEN @ ${data.first_bad_seq}`;
    $("p-chain").className = "pill " + (data.chain_ok ? "ok" : "bad");
    $("chain-note").textContent = data.chain_ok ? "sha256 intact" : "tamper detected";
    const rows = (data.records || []).slice().reverse().map(r => `
      <tr>
        <td class="mono" style="color:var(--dim)">${r.seq}</td>
        <td>${esc(r.action_type)}
          <div style="color:var(--dim);font-size:.72rem">${esc(r.actor)} · ${esc(r.correlation_id).slice(0, 22)}</div></td>
        <td class="hash" style="color:var(--dim);font-size:.7rem">${esc(r.self_hash.slice(0, 12))}…</td>
      </tr>`).join("");
    $("audit").innerHTML = `<table>${rows}</table>`;
  } catch (e) { $("audit").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

/* ---------------- catalog ---------------- */
async function pollCatalog() {
  try {
    const data = await api("/catalog");
    const rows = (data.products || []).map(p => `
      <tr>
        <td>${esc(p.title)}<div style="color:var(--dim);font-size:.72rem">${esc(p.kind)}</div></td>
        <td style="text-align:right">${rs(p.price_paise)}
          <div style="color:${p.in_stock ? "var(--dim)" : "var(--bad)"};font-size:.72rem">${p.in_stock ? "in stock" : "out"}</div></td>
      </tr>`).join("");
    $("catalog").innerHTML = `<table>${rows}</table>`;
  } catch (e) { $("catalog").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

function tick() { if (!document.hidden) { pollHealth(); pollProposals(); pollOrders(); pollAudit(); } }
pollCatalog(); tick();
setInterval(tick, 5000);
setInterval(pollCatalog, 30000);
</script>
</body>
</html>"""
