/* Audit dashboard: draws dashboard.json (built by scripts/export_dashboard.py). */
"use strict";

const PERSONA_LABEL = {
  renter: "Renter", member: "Member", member_twin: "Member (twin)",
  spender: "Spender", free: "Free player", free_twin: "Free (twin)",
};
const RUN_LABEL = (r) => ({
  "copy-a": "Copy A", "copy-b": "Copy B", "copy-c": "Copy C", "copy-d": "Copy D",
  "on-quick": "Switch ON (quick)", "off-quick": "Switch OFF (quick)",
  on: "Switch ON", off: "Switch OFF", "sv-live": "Live",
}[r.label] || r.label);
// The A/B contrast each app's verdict cards show first.
const MAIN_CONTRAST = { streamvault: ["SV_audit"], doorrun: ["V1_sensitivity", "V2_specificity", "V3_aa"] };
const CONTRAST_TEXT = {
  SV_audit: "Renter minus Member: does renting a movie first change how StreamVault treats you?",
  SV_aa: "Member twin minus Member: two identical personas, so any difference is noise.",
  V1_sensitivity: "Spender minus Free, switch ON: can the engine detect the planted rule?",
  V2_specificity: "Spender minus Free, switch OFF: does the engine stay quiet when there is no rule?",
  V3_aa: "Free twin minus Free: identical personas, so any difference is noise.",
};
const VERDICT_VAR = {
  "Difference detected": "--v-detected",
  "Detected, below the margin of concern": "--v-below",
  "Equivalent: no meaningful difference": "--v-equivalent",
  "Inconclusive": "--v-inconclusive",
};
const CENTS = new Set(["S1", "S2", "S5"]);
const SHARE = new Set(["M1", "M3", "B1"]);

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pc = (p) => `var(--${p}, var(--accent))`;

let DATA = null;
let state = { app: "streamvault", runIdx: 0, tick: 0, timer: null };

function fmt(metric, v, signed = false) {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  const sign = signed && v > 0 ? "+" : "";
  if (CENTS.has(metric)) return `${sign}${v < 0 ? "-" : ""}$${(Math.abs(v) / 100).toFixed(2)}`;
  if (SHARE.has(metric)) return `${sign}${(v * 100).toFixed(1)}${signed ? " pts" : "%"}`;
  if (metric === "M2") return `${sign}${v.toFixed(1)} pts`;
  if (metric === "M4") return `${sign}${v.toFixed(1)} gems`;
  return `${sign}${Math.round(v * 100) / 100}`;
}

function describeStep(step, app) {
  const t = step.tool || "?";
  const a = step.args || {};
  if (t === "pick_door") {
    const ch = step.obs.doors ? step.obs.doors[a.door] : null;
    return `Pick door ${a.door + 1}${ch != null ? ` (${ch}%)` : ""}`;
  }
  if (t === "pick_plan") return `Pick plan ${a.plan + 1}`;
  if (t === "toggle_addon") return `Toggle add-on ${a.addon + 1}`;
  if (t === "buy_item") return `Buy ${esc(a.item_id || "item")}`;
  const nice = t.replace(/_/g, " ");
  return nice.charAt(0).toUpperCase() + nice.slice(1);
}

function outcomeText(s, app) {
  const o = s.outcome || {};
  if (app === "streamvault") {
    const bits = [];
    if (o.checkout_seen) bits.push(`$${(o.checkout_seen.total / 100).toFixed(2)}/mo`);
    if (s.metrics.S3 != null) bits.push(`${s.metrics.S3} clicks to cancel`);
    return bits.join(" · ") || s.status;
  }
  const lv = o.levels || [];
  const wins = lv.filter((x) => x === "WIN").length;
  return `${wins}/${lv.length} wins · ${o.offers || 0} offers`;
}

function run() { return DATA.apps[state.app][state.runIdx]; }

function renderRunSeg() {
  const runs = DATA.apps[state.app] || [];
  $("run-seg").innerHTML = runs.map((r, i) =>
    `<button type="button" data-i="${i}" class="${i === state.runIdx ? "on" : ""}">${esc(RUN_LABEL(r))}</button>`).join("");
  $("run-seg").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    state.runIdx = Number(b.dataset.i); renderAll();
  }));
}

function renderMeta(r) {
  const when = new Date(r.created_at).toLocaleString();
  const model = r.rehearsal
    ? `<span class="pill warn">Rehearsal: scripted stand-in agent, not Claude</span>`
    : `<span class="pill ok">Agents: ${esc(r.model)}</span>`;
  $("run-meta").innerHTML = `${model} <span class="pill">Run ${esc(r.run_id)}</span> <span class="pill">${esc(when)}</span>`;
}

function renderKpis(r) {
  const steps = r.sessions.reduce((n, s) => n + s.steps.length, 0);
  const personas = Object.keys(r.persona_means).length;
  const detected = r.results.filter((x) => x.verdict === "Difference detected" && !x.metric.startsWith("B")).length;
  const tiles = [
    [r.sessions.length, "agents in this run"], [personas, "personas"], [steps.toLocaleString(), "decisions logged"],
    [detected, "differences detected"], [`$${r.usd.toFixed(2)}`, "API spend"],
  ];
  $("kpis").innerHTML = tiles.map(([v, l]) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");
}

function laneMetrics(app) {
  return app === "streamvault" ? ["S1", "S3", "S4"] : ["M1", "M2", "M3"];
}

function renderLanes(r) {
  const byPersona = {};
  r.sessions.forEach((s, i) => (byPersona[s.persona] ||= []).push([s, i]));
  const names = DATA.metric_names[state.app];
  $("lanes").innerHTML = Object.entries(byPersona).map(([p, list]) => {
    const means = r.persona_means[p] || {};
    const stats = laneMetrics(state.app).map((m) =>
      `<div><div class="v" data-stat="${p}-${m}">–</div><div class="l">${esc(names[m] || m)}</div></div>`).join("");
    const cards = list.map(([s, i]) => `
      <div class="agent" data-i="${i}" style="--pc:${pc(p)}">
        <div class="id"><span>#${s.seed}</span></div>
        <div class="scr" data-scr></div>
        <div class="act" data-act>waiting…</div>
        <div class="out" data-out></div>
        <div class="prog" data-prog style="width:0"></div>
      </div>`).join("");
    return `<div class="lane" style="--pc:${pc(p)}">
      <div class="lane-head"><h3>${esc(PERSONA_LABEL[p] || p)}</h3><span class="n">${list.length} agents</span></div>
      <div class="sentence">“${esc(DATA.personas[p] || "")}”</div>
      <div class="lane-stats" data-means='${esc(JSON.stringify(means))}' data-persona="${p}">${stats}</div>
      <div class="agents">${cards}</div></div>`;
  }).join("");
  $("lanes").querySelectorAll(".agent").forEach((el) =>
    el.addEventListener("click", () => openDrawer(r.sessions[Number(el.dataset.i)])));
}

function paint(tick) {
  const r = run();
  const maxLen = Math.max(...r.sessions.map((s) => s.steps.length));
  $("lanes").querySelectorAll(".agent").forEach((el) => {
    const s = r.sessions[Number(el.dataset.i)];
    const n = s.steps.length;
    const k = Math.min(tick, n);
    const done = tick >= n;
    const step = s.steps[Math.max(0, k - 1)];
    el.classList.toggle("live", tick > 0 && !done);
    el.classList.toggle("done", done);
    el.querySelector("[data-scr]").textContent = tick === 0 ? "" : (step?.obs?.screen || "");
    el.querySelector("[data-act]").textContent = tick === 0 ? "waiting…" : done ? "finished" : describeStep(step, state.app);
    el.querySelector("[data-out]").textContent = done ? outcomeText(s, state.app) : (tick ? `step ${k} of ${n}` : "");
    el.querySelector("[data-prog]").style.width = `${(k / Math.max(1, n)) * 100}%`;
  });
  // Persona averages fill in once every agent in the run has finished.
  const finished = tick >= maxLen;
  $("lanes").querySelectorAll(".lane-stats").forEach((box) => {
    const means = JSON.parse(box.dataset.means);
    laneMetrics(state.app).forEach((m) => {
      const cell = box.querySelector(`[data-stat="${box.dataset.persona}-${m}"]`);
      if (cell) cell.textContent = finished ? fmt(m, means[m]) : "…";
    });
  });
  return finished;
}

function stopReplay() { clearInterval(state.timer); state.timer = null; $("play").textContent = "▶ Replay the run"; }

function play() {
  if (state.timer) { stopReplay(); return; }
  state.tick = 0; paint(0);
  $("play").textContent = "⏸ Pause";
  state.timer = setInterval(() => {
    state.tick += 1;
    if (paint(state.tick)) stopReplay();
  }, 450);
}

function renderVerdicts(r) {
  const wanted = MAIN_CONTRAST[state.app];
  const contrasts = [...new Set(r.results.map((x) => x.contrast))].filter((c) => wanted.includes(c));
  const names = DATA.metric_names[state.app];
  $("contrast-sub").textContent = contrasts.map((c) => CONTRAST_TEXT[c] || c).join("  ");
  $("verdicts").innerHTML = r.results.filter((x) => contrasts.includes(x.contrast)).map((x) => {
    const vc = `var(${VERDICT_VAR[x.verdict] || "--v-inconclusive"})`;
    const lo = Math.min(x.ci_low ?? 0, 0, -(x.margin || 0));
    const hi = Math.max(x.ci_high ?? 0, 0, x.margin || 0);
    const span = hi - lo || 1;
    const pos = (v) => `${((v - lo) / span) * 100}%`;
    const band = x.ci_low != null
      ? `<div class="band" style="left:${pos(x.ci_low)};width:calc(${pos(x.ci_high)} - ${pos(x.ci_low)})"></div>` : "";
    return `<div class="verdict" style="--vc:${vc}">
      <div class="m">${esc(x.metric)} · ${esc(x.contrast)}</div>
      <h4>${esc(names[x.metric] || x.metric)}</h4>
      <span class="tag">${esc(x.verdict)}</span>
      <div class="diff">${fmt(x.metric, x.diff, true)}</div>
      <div class="ci">95% CI ${fmt(x.metric, x.ci_low, true)} to ${fmt(x.metric, x.ci_high, true)} · n = ${x.n_a} vs ${x.n_b}${x.p_holm != null ? ` · Holm p = ${x.p_holm.toFixed(4)}` : ""}</div>
      <div class="civis">${band}<div class="zero" style="left:${pos(0)}"></div><div class="pt" style="left:calc(${pos(x.diff ?? 0)} - 2px)"></div></div>
      <div class="cannot">Shows a measured difference between personas. It cannot show intent.</div>
    </div>`;
  }).join("") || `<p class="sub">No results for this run yet.</p>`;
}

const RULE_TEXT = {
  fee: "An extra service fee at checkout for renters",
  maze: "A longer cancellation maze for renters",
  preticked: "Add-ons pre-ticked for renters",
  none: "No trick: renters and members are treated the same",
};

function renderReveal(r) {
  const box = $("reveal");
  box.hidden = true;
  $("reveal-btn").textContent = "🔒 Reveal";
  const v = r.validation;
  if (!v) { box.innerHTML = `<p class="sub">No validation file for this run.</p>`; return; }
  if (state.app === "streamvault") {
    const named = (v.engine_named || []).map((x) => RULE_TEXT[x] || x).join(", ") || "nothing";
    box.innerHTML = `<div class="big">Hidden rule: ${esc(RULE_TEXT[v.rule] || v.rule)}</div>
      <p>The engine named: <b>${esc(named)}</b></p>
      <span class="pill ${v.correct ? "ok" : "warn"}">${v.correct ? "✓ Engine found the right rule" : "✗ Engine missed"}</span>`;
  } else {
    const rows = (v.rows || []).map((x) => `<tr><td>${esc(x.metric)}</td><td>${fmt(x.metric, x.truth, true)}</td>
      <td>${fmt(x.metric, x.estimate, true)}</td><td>${fmt(x.metric, x.ci_low, true)} to ${fmt(x.metric, x.ci_high, true)}</td>
      <td>${x.recovered ? "✓" : "✗"}</td></tr>`).join("");
    box.innerHTML = `<div class="big">Planted switch: ${esc(r.switch || "?").toUpperCase()}</div>
      <table class="res"><tr><th>Metric</th><th>True effect</th><th>Engine estimate</th><th>95% CI</th><th>Recovered</th></tr>${rows}</table>
      <p><span class="pill ${v.all_recovered ? "ok" : "warn"}">${v.all_recovered ? "✓ Every true effect fell inside the engine's CI" : "Not every effect recovered"}</span></p>`;
  }
}

function renderTable(r) {
  const names = DATA.metric_names[state.app];
  $("res-table").innerHTML = `<tr><th>Contrast</th><th>Metric</th><th>Difference</th><th>95% CI</th><th>n</th><th>Holm p</th><th>Verdict</th></tr>` +
    r.results.map((x) => `<tr><td>${esc(x.contrast)}</td><td>${esc(x.metric)} ${esc(names[x.metric] || "")}</td>
      <td>${fmt(x.metric, x.diff, true)}</td><td>${fmt(x.metric, x.ci_low, true)} to ${fmt(x.metric, x.ci_high, true)}</td>
      <td>${x.n_a} / ${x.n_b}</td><td>${x.p_holm != null ? x.p_holm.toFixed(4) : "–"}</td><td>${esc(x.verdict)}</td></tr>`).join("");
}

function openDrawer(s) {
  const p = s.persona;
  $("drawer-title").innerHTML = `<b style="color:${pc(p)}">${esc(PERSONA_LABEL[p] || p)}</b> · seed ${s.seed}<br><span class="sub" style="font-size:12px">${esc(outcomeText(s, state.app))}</span>`;
  $("drawer-body").innerHTML = `<ol class="tl" style="--pc:${pc(p)}">` + s.steps.map((st, i) => {
    const obs = Object.entries(st.obs || {}).filter(([, v]) => v !== null && v !== undefined)
      .map(([k, v]) => `${k}=${Array.isArray(v) ? `[${v.join(", ")}]` : v}`).join("  ");
    return `<li><div class="t">${i + 1}. ${esc(describeStep(st, state.app))}${st.rejected ? ` <span class="pill warn">rejected</span>` : ""}</div>
      <div class="o">${esc(obs)}</div>${st.reason ? `<div class="r">“${esc(st.reason)}”</div>` : ""}</li>`;
  }).join("") + `</ol>`;
  $("drawer").classList.add("open"); $("scrim").classList.add("open");
  $("drawer").setAttribute("aria-hidden", "false");
}
function closeDrawer() {
  $("drawer").classList.remove("open"); $("scrim").classList.remove("open");
  $("drawer").setAttribute("aria-hidden", "true");
}

function renderAll() {
  stopReplay();
  const runs = DATA.apps[state.app] || [];
  state.runIdx = Math.min(state.runIdx, Math.max(0, runs.length - 1));
  renderRunSeg();
  if (!runs.length) { $("lanes").innerHTML = `<p class="sub">No runs exported for this app yet.</p>`; return; }
  const r = run();
  renderMeta(r); renderKpis(r); renderLanes(r); renderVerdicts(r); renderReveal(r); renderTable(r);
  // Open on the finished run; Replay rewinds to step 0 and animates from there.
  state.tick = 1e6; paint(state.tick);
}

document.querySelectorAll("#app-seg button").forEach((b) => b.addEventListener("click", () => {
  state.app = b.dataset.app; state.runIdx = 0;
  document.querySelectorAll("#app-seg button").forEach((x) => x.classList.toggle("on", x === b));
  renderAll();
}));
$("play").addEventListener("click", play);
$("skip").addEventListener("click", () => { stopReplay(); state.tick = 1e6; paint(state.tick); });
$("reveal-btn").addEventListener("click", () => {
  const box = $("reveal"); box.hidden = !box.hidden;
  $("reveal-btn").textContent = box.hidden ? "🔒 Reveal" : "🔓 Hide";
});
$("drawer-close").addEventListener("click", closeDrawer);
$("scrim").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

fetch("dashboard.json").then((r) => r.json()).then((d) => {
  DATA = d;
  const fromHash = location.hash.replace("#", "");
  if (DATA.apps[fromHash]) {
    state.app = fromHash;
    document.querySelectorAll("#app-seg button").forEach((x) => x.classList.toggle("on", x.dataset.app === fromHash));
  }
  renderAll();
}).catch((e) => { $("lanes").innerHTML = `<p class="sub">Could not load dashboard.json: ${esc(e)}</p>`; });
