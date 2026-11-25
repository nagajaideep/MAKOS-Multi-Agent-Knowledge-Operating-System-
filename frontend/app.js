// MAKOS portal – plain JavaScript, no framework.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let USERS = [];
let me = "ravi";
let tab = "ask";
let lastAnswer = null;
let selectedCase = null;

const KIND = {
  full_run: "Full run",
  reuse: "Reused – no new work",
  refresh: "Refreshed only what changed",
  new_scope: "Checks reused, wording written for your role",
};
const STATE = { computed: "Computed", reused: "Reused", refreshed: "Refreshed" };
const STATUS = { ok: "OK", fail: "Fails", cannot_confirm: "Cannot confirm", note: "Note", info: "Info" };

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  let body = null;
  try { body = await r.json(); } catch { /* not JSON */ }
  if (!r.ok) throw new Error((body && body.detail) || `${r.status} ${r.statusText}`);
  return body;
}
const post = (path, data) => api(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(data || {}) });

function flash(text, kind = "info") {
  const f = $("#flash");
  f.className = `flash notice ${kind}`;
  f.textContent = text;
  f.hidden = false;
  clearTimeout(flash.t);
  flash.t = setTimeout(() => (f.hidden = true), 6000);
}

const fmtTime = (ts) => (ts ? ts.replace("T", " ").slice(0, 19) : "");
const inr = (v) => (typeof v === "number" ? "₹" + v.toLocaleString("en-IN", { maximumFractionDigits: 0 }) : esc(v));
const user = (id) => USERS.find((u) => u.id === id) || {};

// ---------- source links (GFR clauses open in a dialog) ----------
function sourceLinks(sources) {
  return (sources || []).map((s) => {
    if (s.startsWith("GFR-")) return `<a href="#" class="src" data-clause="${esc(s)}">${esc(s.replace("GFR-", "GFR Rule "))}</a>`;
    if (s.startsWith("MCA:")) return `<span class="src">MCA ${esc(s.slice(4))}</span>`;
    if (s.startsWith("CPPP:")) return `<span class="src">CPPP award</span>`;
    if (s.startsWith("LIST:")) return `<span class="src">${esc(s.slice(5))}</span>`;
    return `<span class="src">${esc(s)}</span>`;
  }).join("") || '<span class="hint">no source</span>';
}

async function openClause(id) {
  try {
    const c = await api(`/api/gfr/${encodeURIComponent(id)}`);
    const notes = (c.amendment_notes || []).length ? `<p class="hint">Amendments: ${c.amendment_notes.map(esc).join("; ")}</p>` : "";
    modal(`<h3>GFR 2017 – Rule ${c.rule}: ${esc(c.title)}</h3><p class="hint">${esc(c.source)}, page ${c.page}</p>${notes}<div class="clause-text">${esc(c.text)}</div>`);
  } catch (e) { flash(e.message, "bad"); }
}

function modal(html) { $("#modal-body").innerHTML = html; $("#modal").hidden = false; }
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal" || e.target.classList.contains("modal-close")) $("#modal").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#modal").hidden = true; });
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-clause]");
  if (a) { e.preventDefault(); openClause(a.dataset.clause); }
});

// ---------- ASK ----------
function renderAnswer(d) {
  const masked = d.scope === "masked";
  const tok = d.tokens;
  const tokText = tok.total === 0 ? "0 tokens" : `${tok.total.toLocaleString("en-IN")} tokens${tok.mode === "offline-estimate" ? " (estimated)" : ""}`;
  let html = "";
  if (d.joined) html += `<div class="notice info">${esc(d.joined.message)}</div>`;
  if (d.changes_since_last_time && d.changes_since_last_time.length) {
    html += `<div class="notice warn"><b>What changed since you last asked</b> (${esc(fmtTime(d.changes_since_last_time[0].since))}):<br>` +
      d.changes_since_last_time.map((c) => `${esc(labelOf(c.part))}: <b>${esc(c.before)}</b> → <b>${esc(c.after)}</b>`).join("<br>") + "</div>";
  }
  html += `<div class="decision ${esc(d.decision.decision)}"><span class="label">${esc(d.decision.label)}</span>
    <span>${esc(d.vendor.name)}${d.amount ? " · " + inr(d.amount) : ""} · Case #${d.case_id}</span></div>`;
  html += `<div class="meta">
    <span class="tag ${esc(d.kind)}">${esc(KIND[d.kind] || d.kind)}</span>
    <span class="tag">${d.parts_refreshed} of ${d.parts_total} parts refreshed</span>
    <span class="tag">${tokText}</span>
    <span class="tag">Without MAKOS: about ${d.tokens.without_makos.toLocaleString("en-IN")} tokens</span>
    <span class="tag">${d.ms} ms</span>
    ${masked ? '<span class="tag masked">Masked for your role</span>' : '<span class="tag">Full details (your role)</span>'}
    <span class="tag">${esc(d.chain)}</span></div>`;
  html += `<div class="section-label">Checks</div><table class="data"><thead><tr><th>Check</th><th>Result</th><th>Status</th><th>Source</th><th>This time</th></tr></thead><tbody>`;
  for (const p of d.parts) {
    const extra = [];
    if (p.details.bank_account) extra.push(`Bank account on file (demo value): ${esc(p.details.bank_account)}`);
    if (p.details.total_value !== undefined && p.details.count) extra.push(`Total awarded: ${inr(p.details.total_value)}`);
    if (p.details.amount !== undefined) extra.push(`Order value: ${inr(p.details.amount)} · ${esc(p.details.rules_version)}`);
    if (p.masked_fields && p.masked_fields.length) extra.push(`<span class="tag masked">masked: ${p.masked_fields.map(esc).join(", ")}</span>`);
    html += `<tr><td><b>${esc(p.label)}</b></td>
      <td>${esc(p.message)}${extra.length ? `<div class="hint">${extra.join(" · ")}</div>` : ""}${awardsList(p)}</td>
      <td><span class="tag ${esc(p.status)}">${esc(STATUS[p.status] || p.status)}</span></td>
      <td>${sourceLinks(p.sources)}</td>
      <td><span class="tag ${esc(p.state)}">${esc(STATE[p.state])}</span><div class="hint">${p.state === "computed" ? "" : "first by " + esc(p.computed_for || "")}</div></td></tr>`;
  }
  html += "</tbody></table>";
  html += `<div class="section-label">Answer</div><div class="answer-text" style="border-top:1px solid var(--line)">${esc(d.answer)}</div>`;
  const pa = d.proposed_action;
  if (pa) {
    if (pa.action === "hold_vendor") {
      html += `<div class="notice ${pa.status === "done" ? "ok" : "info"}" style="margin-top:12px"><b>Follow-up (high risk):</b> put ${esc(pa.params.vendor_name)} on hold in the vendor portal.
        Request #${pa.approval_id} – <b>${esc(pa.status)}</b>, assigned to ${esc(pa.assigned_to_name)}. ${pa.status === "pending" ? "It will run only after approval." : ""}</div>`;
    } else {
      html += `<div class="notice ok" style="margin-top:12px"><b>Follow-up (low risk):</b> a review note was added to the case automatically.</div>`;
    }
  }
  $("#answer").innerHTML = html;
}

function awardsList(p) {
  const a = p.details.top_awards;
  if (!a || !a.length) return "";
  return `<details class="hint"><summary>Show awards</summary><ul>${a.map((x) =>
    `<li>${esc(x.date ? x.date.slice(0, 11) : "")} · ${esc(x.org)} · ${esc(x.title)} · ${inr(x.contract_value)} · ${x.bids ?? "?"} bid(s)${x.url ? ` · <a href="${esc(x.url)}" target="_blank" rel="noopener">CPPP record</a>` : ""}</li>`).join("")}</ul></details>`;
}

function labelOf(part) {
  return { registry: "Company registry status", debarment: "Debarment lists", history: "Past awards", rule: "Purchase rule" }[part] || part;
}

$("#ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#q").value.trim();
  if (!q) return;
  $("#answer").innerHTML = '<p class="hint">Working…</p>';
  try {
    lastAnswer = await post("/api/ask", { user: me, question: q });
    renderAnswer(lastAnswer);
    refreshCount();
  } catch (err) {
    $("#answer").innerHTML = `<div class="notice bad">${esc(err.message)}</div>`;
  }
});
$$(".examples a").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); $("#q").value = a.dataset.q; $("#q").focus(); }));

// ---------- CASES ----------
async function loadCases() {
  const d = await api(`/api/cases?user=${me}`);
  const u = user(me);
  const why = { officer: "Officers see only the cases they are part of.", manager: "As manager you see every case of your team, including their chats.",
    auditor: "The Vigilance Officer sees every case (read-only).", admin: "The administrator manages data and has no access to cases." }[u.role];
  $("#cases-note").textContent = `You can see ${d.visible} of ${d.total} case(s). ${why}`;
  $("#cases-table tbody").innerHTML = d.cases.map((c) =>
    `<tr class="clickable ${c.case_id === selectedCase ? "selected" : ""}" data-case="${c.case_id}"><td>${c.case_id}</td><td>${esc(c.vendor_name)}</td>
     <td>${esc(c.created_by_name)}</td><td>${c.members.map(esc).join(", ")}</td><td>${esc(c.status)}</td></tr>`).join("") ||
    '<tr><td colspan="5" class="hint">No cases visible to you.</td></tr>';
  $$("#cases-table tr[data-case]").forEach((tr) => tr.addEventListener("click", () => openCase(+tr.dataset.case)));
  if (selectedCase && d.cases.some((c) => c.case_id === selectedCase)) openCase(selectedCase);
  else { selectedCase = null; $("#case-detail").innerHTML = "Select a case to see its conversation."; $("#case-detail").className = "box muted-box"; }
}

async function openCase(id) {
  selectedCase = id;
  $$("#cases-table tr").forEach((tr) => tr.classList.toggle("selected", +tr.dataset.case === id));
  const box = $("#case-detail");
  box.className = "box";
  try {
    const d = await api(`/api/cases/${id}?user=${me}`);
    box.innerHTML = `<h3>Case #${d.case.case_id} – ${esc(d.case.vendor_name)}</h3>
      <p class="hint">Members: ${d.members.map((m) => `${esc(m.name)} (${esc(m.how)})`).join(", ")}${d.viewer_scope === "masked" ? ' · <span class="tag masked">masked for your role</span>' : ""}</p>
      ${d.messages.map((m) => `<div class="msg ${esc(m.sender)}"><span class="who">${m.sender === "makos" ? "MAKOS" : m.sender === "system" ? "System" : esc(m.user_name)}</span>
        ${m.sender === "makos" ? `<span class="tag ${esc(m.meta.kind)}">${esc(KIND[m.meta.kind] || "")}</span> <span class="hint">for ${esc(m.user_name)}</span>` : ""}
        <span class="when">${esc(fmtTime(m.created_at))}</span><div class="body">${esc(m.text)}</div></div>`).join("")}`;
  } catch (e) { box.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
}

$("#start-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const r = await post("/api/cases/start", { user: me, vendor: $("#start-vendor").value });
    flash(r.joined ? r.joined.message : `Case #${r.case_id} started for ${r.vendor}.`, r.joined ? "info" : "ok");
    selectedCase = r.case_id;
    loadCases();
  } catch (err) { flash(err.message, "bad"); }
});

// ---------- APPROVALS ----------
async function loadApprovals() {
  const rows = await api(`/api/approvals?user=${me}`);
  $("#appr-table tbody").innerHTML = rows.map((a) => {
    const open = a.status === "pending" || a.status === "escalated";
    return `<tr><td>${a.approval_id}</td><td>${a.action === "hold_vendor" ? "Put vendor on hold" : esc(a.action)}<div class="hint">${esc(a.reason)}</div></td>
      <td>${esc(a.params.vendor_name)}</td><td><span class="tag ${a.risk === "high" ? "fail" : "ok"}">${esc(a.risk)}</span></td>
      <td>${esc(a.requested_for_name)}</td><td>${esc(a.assigned_to_name)}</td>
      <td><span class="tag ${a.status === "done" ? "ok" : open ? "note" : ""}">${esc(a.status)}</span>${a.decided_by_name ? `<div class="hint">by ${esc(a.decided_by_name)}</div>` : ""}</td>
      <td class="hint">${esc(fmtTime(a.due_at))}</td>
      <td>${open ? `<button class="btn ok" data-approve="${a.approval_id}">Approve</button> <button class="btn bad" data-reject="${a.approval_id}">Reject</button>` : ""}
          ${a.action_id ? `<button class="btn" data-replay="${a.action_id}">Replay</button>` : ""}</td></tr>`;
  }).join("") || '<tr><td colspan="9" class="hint">No approval requests visible to you.</td></tr>';
  $$("[data-approve]").forEach((b) => b.addEventListener("click", () => decide(+b.dataset.approve, true, b)));
  $$("[data-reject]").forEach((b) => b.addEventListener("click", () => decide(+b.dataset.reject, false, b)));
  $$("[data-replay]").forEach((b) => b.addEventListener("click", () => replay(+b.dataset.replay)));
}

async function decide(id, approve, btn) {
  btn.disabled = true;
  if (approve) btn.textContent = "Running browser agent…";
  try {
    const r = await post(`/api/approvals/${id}/decide`, { user: me, approve, note: approve ? "Approved in portal" : "Rejected in portal" });
    if (r.action) {
      flash(r.action.verified ? "Approved. The browser agent put the vendor on hold and verified it by re-opening the record." :
        "Approved, but the action could not be verified. It was escalated.", r.action.verified ? "ok" : "bad");
      await loadApprovals();
      replay(r.action.action_id);
    } else { flash(approve ? "Approved." : "Rejected. Nothing was executed.", "info"); loadApprovals(); }
    refreshCount();
  } catch (err) { flash(err.message, "bad"); btn.disabled = false; btn.textContent = approve ? "Approve" : "Reject"; }
}

async function replay(actionId, step = 0) {
  try {
    const a = await api(`/api/actions/${actionId}?user=${me}`);
    const shots = a.screenshots;
    if (!shots.length) { $("#replay").innerHTML = `<div class="notice bad">No screenshots recorded. ${esc((a.log || []).join(" "))}</div>`; return; }
    const s = shots[step];
    $("#replay").innerHTML = `<div class="replay"><h3 style="margin-top:0">Screenshot replay – action #${a.action_id}
      <span class="tag ${a.verified ? "ok" : "fail"}">${a.verified ? "Verified" : "Not verified"}</span></h3>
      <div class="replay-nav"><button class="btn" id="rp-prev" ${step === 0 ? "disabled" : ""}>Previous</button>
      <span><b>Step ${s.step} of ${shots.length}:</b> ${esc(s.label)}</span>
      <button class="btn" id="rp-next" ${step === shots.length - 1 ? "disabled" : ""}>Next</button></div>
      <img src="${esc(s.file)}" alt="${esc(s.label)}">
      <p class="steps">${a.log.map(esc).join(" · ")}</p></div>`;
    $("#rp-prev").onclick = () => replay(actionId, step - 1);
    $("#rp-next").onclick = () => replay(actionId, step + 1);
  } catch (e) { $("#replay").innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
}

async function refreshCount() {
  try {
    const rows = await api(`/api/approvals?user=${me}`);
    const n = rows.filter((a) => (a.status === "pending" || a.status === "escalated") && a.assigned_to === me).length;
    $("#appr-count").textContent = n;
    $("#appr-count").hidden = n === 0;
  } catch { $("#appr-count").hidden = true; }
}

// ---------- AUDIT ----------
async function loadAudit() {
  const rows = await api(`/api/audit?user=${me}&limit=150`);
  const u = user(me);
  $("#audit-note").textContent = { officer: "You see only events started by you.", manager: "You see events started by you and your team.",
    auditor: "You see every event.", admin: "You see every event." }[u.role] + " Each row records the person on whose behalf the agents acted.";
  const cls = (o) => /BLOCK|DENIED|STOP|NOT VERIFIED/.test(o) ? "fail" : /FLAG|WAIT|ESCAL|NO_VENDOR/.test(o) ? "note" : "ok";
  $("#audit-table tbody").innerHTML = rows.map((r) => `<tr><td>${esc(fmtTime(r.ts))}</td><td>${esc(r.chain)}</td><td>${esc(r.actor)}</td>
    <td>${esc(r.action)}${auditDetail(r)}</td><td><span class="tag ${cls(r.outcome)}">${esc(r.outcome)}</span></td><td>${r.case_id ?? ""}</td></tr>`).join("") ||
    '<tr><td colspan="6" class="hint">No events.</td></tr>';
}
function auditDetail(r) {
  const d = r.detail || {};
  const bits = [];
  if (d.question) bits.push(`“${d.question}”`);
  if (d.kind) bits.push(KIND[d.kind] || d.kind);
  if (d.tokens !== undefined) bits.push(`${d.tokens} tokens`);
  if (d.changed_sources) bits.push(`changed: ${d.changed_sources.join(", ")}`);
  if (d.reason) bits.push(d.reason);
  if (d.sql) bits.push(d.sql);
  if (d.instruction_like_lines && d.instruction_like_lines.length) bits.push(`flagged: “${d.instruction_like_lines[0].text}”`);
  return bits.length ? `<div class="hint">${esc(bits.join(" · "))}</div>` : "";
}

// ---------- SAVINGS ----------
async function loadSavings() {
  const [s, led] = await Promise.all([api("/api/savings"), api("/api/ledger?limit=25")]);
  $("#sav-note").textContent = `Without MAKOS every question would be a full run. ${s.price_note}.` +
    (s.llm_modes.includes("offline-estimate") ? " Token counts are estimated (offline mode, no API key set)." : "");
  const tiles = [
    [s.questions, "Questions asked"], [s.full_runs, "Full runs"], [s.reused, "Answered from shared answers"],
    [s.parts_refreshed, "Parts refreshed"], [s.percent_saved + "%", "Tokens saved"],
    ["₹" + s.cost_inr, `Cost (₹${s.cost_without_makos_inr} without MAKOS)`],
    [s.avg_ms_reused != null ? s.avg_ms_reused + " ms" : "–", "Avg. time, reused answer"],
    [s.avg_ms_full_run != null ? s.avg_ms_full_run + " ms" : "–", "Avg. time, full run"],
  ];
  $("#stats").innerHTML = tiles.map(([n, l]) => `<div class="stat"><div class="n">${esc(n)}</div><div class="l">${esc(l)}</div></div>`).join("");
  const max = Math.max(s.tokens_without_makos, s.tokens_used, 1);
  $("#compare").innerHTML = `
    <div class="bar-row">With MAKOS: <b>${s.tokens_used.toLocaleString("en-IN")}</b> tokens<div class="bar"><div style="width:${(100 * s.tokens_used) / max}%"></div></div></div>
    <div class="bar-row">Without MAKOS: <b>${s.tokens_without_makos.toLocaleString("en-IN")}</b> tokens<div class="bar without"><div style="width:${(100 * s.tokens_without_makos) / max}%"></div></div></div>
    <p class="hint">Saved ${s.tokens_saved.toLocaleString("en-IN")} tokens across ${s.questions} question(s).</p>`;
  $("#budgets").innerHTML = s.budgets.map((b) => `<div class="bar-row">${esc(b.dept)}: ${b.used_tokens.toLocaleString("en-IN")} of ${b.limit_tokens.toLocaleString("en-IN")} tokens
    <div class="bar"><div style="width:${Math.min(100, (100 * b.used_tokens) / b.limit_tokens)}%"></div></div></div>`).join("") +
    `<p class="hint">${s.blocked_or_stopped} agent run(s) stopped automatically by the budget guard.</p>`;
  const KL = { ...KIND, stopped: "Stopped by guard" };
  $("#ledger-table tbody").innerHTML = led.map((r) => `<tr><td>${esc(fmtTime(r.ts))}</td><td>${esc(r.user_name)}</td><td>${esc(r.question)}</td>
    <td><span class="tag ${esc(r.kind)}">${esc(KL[r.kind] || r.kind)}</span></td><td>${r.parts_refreshed ?? 0} of ${r.parts_total ?? 0}</td>
    <td>${(r.tokens_in + r.tokens_out).toLocaleString("en-IN")}</td><td>${r.ms ?? ""}</td></tr>`).join("") || '<tr><td colspan="7" class="hint">No questions yet.</td></tr>';
}

// ---------- DEMO ----------
async function loadChanges() {
  const rows = await api("/api/admin/change-log");
  $("#changes-table tbody").innerHTML = rows.map((r) => `<tr><td>${esc(fmtTime(r.ts))}</td><td>${esc(r.source_key)}</td><td>${esc(r.field)}</td>
    <td>${esc(r.old_value)}</td><td>${esc(r.new_value)}</td><td>${esc(r.note)}</td></tr>`).join("");
}

async function uploadFile(file) {
  const fd = new FormData();
  fd.append("user", me);
  fd.append("file", file);
  const r = await api("/api/upload", { method: "POST", body: fd });
  return `<div class="notice ${r.flags.length ? "warn" : "ok"}"><b>${esc(r.filename)}</b> uploaded as ${esc(user(me).name)}. ${esc(r.note)}</div>` +
    (r.flags.length ? `<table class="data small"><thead><tr><th>Line</th><th>Text found in the document</th><th>Action taken</th></tr></thead><tbody>` +
      r.flags.map((f) => `<tr><td>${f.line}</td><td>${esc(f.text)}</td><td>None – stored as data</td></tr>`).join("") + "</tbody></table>" : "");
}

const DEMO = {
  reset: async () => { await post("/api/admin/reset?user=admin"); lastAnswer = null; $("#answer").innerHTML = ""; return '<div class="notice ok">Demo reset. Registry snapshot 1 loaded, current GFR limits.</div>'; },
  "reset-before": async () => { await post("/api/admin/reset?user=admin&start_rules=before"); $("#answer").innerHTML = ""; return '<div class="notice ok">Demo reset with the GFR limits in force before 10.07.2024.</div>'; },
  registry: async () => {
    const r = await post("/api/admin/registry-update?user=admin");
    return `<div class="notice info">${r.changed_rows.length ? r.changed_rows.map((c) => `${esc(c.name)}: <b>${esc(c.from)}</b> → <b>${esc(c.to)}</b> (${esc(c.label)})`).join("<br>") : "No changes."}<br>${esc(r.note)}</div>`;
  },
  gfr: async () => { const r = await post("/api/admin/gfr-amendment?user=admin"); return `<div class="notice ${r.changed ? "info" : "warn"}">${r.changed ? "GFR amendment of 10.07.2024 applied. Only answers that used the purchase rule will refresh, when asked again." : esc(r.note)}</div>`; },
  upload: async () => { const blob = await (await fetch("/api/demo/sample-quotation.pdf")).blob(); return uploadFile(new File([blob], "quotation_sri_lakshmi_traders.pdf", { type: "application/pdf" })); },
  perm: async () => {
    const rows = await post("/api/demo/permission-test");
    return `<table class="data small"><thead><tr><th>Agent</th><th>Tried to</th><th>Result</th><th>Reason</th></tr></thead><tbody>${rows.map((r) =>
      `<tr><td>${esc(r.agent)}</td><td>${esc(r.tried)}</td><td><span class="tag ${r.result === "BLOCKED" ? "fail" : "ok"}">${esc(r.result)}</span></td><td>${esc(r.reason || "")}</td></tr>`).join("")}</tbody></table>`;
  },
  expire: async () => {
    const r = await post("/api/demo/expire-approvals");
    return `<div class="notice ${r.escalated ? "warn" : "info"}">${r.escalated ? `${r.escalated} pending request(s) escalated to Priya (Vigilance Officer).` : "No pending requests to escalate."}</div>`;
  },
  stuck: async () => {
    const r = await post(`/api/demo/stuck-agent?user=${me === "admin" || me === "priya" ? "ravi" : me}`);
    return `<div class="notice bad"><b>Agent stopped automatically:</b> ${esc(r.reason)} after ${r.attempts.length} attempts and ${r.tokens_spent.toLocaleString("en-IN")} tokens. ${esc(r.note)}</div>`;
  },
};
$$("[data-demo]").forEach((b) => b.addEventListener("click", async () => {
  b.disabled = true;
  try { $("#demo-out").innerHTML = await DEMO[b.dataset.demo](); loadChanges(); refreshCount(); loadStatus(); }
  catch (e) { $("#demo-out").innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
  b.disabled = false;
}));
$("#own-file").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  try { $("#demo-out").innerHTML = await uploadFile(f); } catch (err) { $("#demo-out").innerHTML = `<div class="notice bad">${esc(err.message)}</div>`; }
  e.target.value = "";
});

// ---------- tabs, user switch, text size ----------
const LOADERS = { cases: loadCases, approvals: loadApprovals, audit: loadAudit, savings: loadSavings, demo: loadChanges };
function show(name) {
  tab = name;
  $("#flash").hidden = true;
  $$(".mainnav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((s) => (s.hidden = s.id !== `tab-${name}`));
  $("#crumb-here").textContent = $(`.mainnav button[data-tab="${name}"]`).firstChild.textContent.trim();
  if (LOADERS[name]) LOADERS[name]().catch((e) => flash(e.message, "bad"));
  try { localStorage.setItem("makos-tab", name); } catch { /* storage unavailable */ }
}
$$(".mainnav button").forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));

function setUser(id) {
  me = id;
  const u = user(id);
  $("#who").value = id;
  $("#who-role").textContent = `${u.title} · ${u.dept} · sees ${u.scope === "full" ? "full details" : "masked details"}`;
  try { localStorage.setItem("makos-user", id); } catch { /* storage unavailable */ }
  if ($("#answer").innerHTML.trim()) $("#answer").innerHTML = `<div class="notice info">Now logged in as ${esc(u.name)}. Ask the question again to see this role's answer.</div>`;
  lastAnswer = null;
  $("#replay").innerHTML = "";
  refreshCount();
  if (LOADERS[tab]) LOADERS[tab]().catch((e) => flash(e.message, "bad"));
}
$("#who").addEventListener("change", (e) => setUser(e.target.value));

$$(".txt").forEach((b) => b.addEventListener("click", () => {
  const n = +b.dataset.size;
  document.documentElement.style.setProperty("--fs", n === 0 ? "15px" : `${Math.max(12, Math.min(19, parseFloat(getComputedStyle(document.documentElement).fontSize) + n))}px`);
}));

async function loadStatus() {
  const st = await api("/api/status");
  $("#foot-mode").textContent = st.llm_mode !== "offline" ? `Answers written by ${st.model}` : "Offline mode: answers from templates, tokens estimated";
  $("#foot-rules").textContent = st.rules_version === "after" ? "GFR 2017 as amended on 10.07.2024" : "GFR 2017 before the 10.07.2024 amendment";
}

(async function init() {
  USERS = await api("/api/users");
  $("#who").innerHTML = USERS.map((u) => `<option value="${u.id}">${esc(u.name)} – ${esc(u.title)}</option>`).join("");
  await loadStatus();
  $("#foot-date").textContent = new Date().toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
  let saved = "ravi", savedTab = "ask";
  try { saved = localStorage.getItem("makos-user") || "ravi"; savedTab = localStorage.getItem("makos-tab") || "ask"; } catch { /* storage unavailable */ }
  setUser(USERS.some((u) => u.id === saved) ? saved : "ravi");
  show(savedTab);
})();
