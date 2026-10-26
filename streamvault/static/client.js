"use strict";
/* StreamVault thin client.
 *
 * The server decides every price and screen. This file only sends one action
 * at a time to POST /api/act and renders the state that comes back.
 *
 * DOM contract for the audit harness:
 *   #app[data-state]  current screen
 *   #app[data-seq]    render counter; rises once per server response, AFTER the
 *                     screen's entrance animation has finished, so the walker
 *                     never reads or screenshots a half-drawn screen
 *   [data-testid]     every value and button; money in data-value as cents
 */

const app = document.getElementById("app");
/* Served at "/" or under a copy prefix such as "/a/": every API call is relative to it. */
const BASE = location.pathname.replace(/\/+$/, "");
const seedParam = new URLSearchParams(location.search).get("seed");
const SEED = Number.isFinite(Number(seedParam)) ? Number(seedParam) : 0;
const MAX_ANIMATION_MS = 700;

let seq = 0;
let busy = false;

const $ = (id) => document.querySelector(`[data-testid="${id}"]`);
const money = (cents) => "$" + (cents / 100).toFixed(2);

function setMoney(id, cents, suffix = "") {
  const el = $(id);
  if (!el) return;
  el.dataset.value = String(cents);
  el.textContent = money(cents) + suffix;
}

function bumpSeq() {
  seq += 1;
  app.dataset.seq = String(seq);
  const dbg = document.getElementById("debug-seq");
  if (dbg) dbg.textContent = String(seq);
}

/* Bump data-seq once the new screen has finished animating in. If the screen
 * did not change there is no entrance animation, so bump on the next frame. */
function settle(changedScreen) {
  if (!changedScreen) {
    requestAnimationFrame(() => requestAnimationFrame(bumpSeq));
    return;
  }
  const section = app.querySelector(`section[data-screen="${app.dataset.state}"]`);
  let done = false;
  const finish = () => { if (!done) { done = true; bumpSeq(); } };
  if (section) section.addEventListener("animationend", finish, { once: true });
  setTimeout(finish, MAX_ANIMATION_MS);
}

function render(s) {
  const changed = app.dataset.state !== s.screen;
  app.dataset.state = s.screen;
  const dbg = document.getElementById("debug-state");
  if (dbg) dbg.textContent = s.screen;

  setMoney("headline-price", s.headline_price);
  $("headline-price").textContent = "From " + money(s.headline_price) + "/month";
  setMoney("rent-price", s.rent_price);
  $("rental-status").textContent = s.rented ? "Rented" : "";
  $("rent-movie").disabled = s.rented;

  s.plan_prices.forEach((cents, i) => {
    const el = $(`plan-${i}-price`);
    el.dataset.value = String(cents);
    el.firstChild.textContent = money(cents);
  });

  for (const a of s.addons) {
    $(`addon-${a.id}`).checked = a.checked;
    setMoney(`addon-${a.id}-price`, a.price, "/mo");
    $(`addon-${a.id}-price`).textContent = "+" + money(a.price) + "/mo";
  }

  if (s.checkout) {
    setMoney("checkout-plan-price", s.checkout.plan);
    setMoney("checkout-addons-total", s.checkout.addons);
    setMoney("checkout-fees", s.checkout.fees);
    setMoney("checkout-total", s.checkout.total, "/mo");
  }

  const step = $("cancel-step");
  step.dataset.value = String(s.cancel_step);
  step.textContent = String(s.cancel_step);
  if (s.cancel_message) $("cancel-message").textContent = s.cancel_message;
  $("final-status").textContent = s.screen === "cancelled" ? "Cancelled" : "";

  settle(changed);
}

async function send(action, arg) {
  if (busy) return;
  busy = true;
  try {
    const res = await fetch(`${BASE}/api/act`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(arg === undefined ? { action } : { action, arg }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      render(data);
    } else {
      /* Re-render the server's truth (e.g. undo a checkbox the browser flipped)
       * and still bump data-seq, so the walker sees a settled page rather than
       * timing out on a request that was refused. */
      const st = await fetch(`${BASE}/api/state`, { credentials: "same-origin" });
      if (st.ok) render(await st.json()); else bumpSeq();
    }
  } finally {
    busy = false;
  }
}

function bind(testid, action, arg) {
  const el = $(testid);
  const evt = el.type === "checkbox" ? "change" : "click";
  el.addEventListener(evt, () => send(action, arg));
}

bind("rent-movie", "rent_movie");
bind("start-signup", "start_signup");
for (let i = 0; i < 3; i += 1) bind(`plan-${i}`, "pick_plan", i);
for (let i = 0; i < 4; i += 1) bind(`addon-${i}`, "toggle_addon", i);
bind("addons-continue", "addons_continue");
bind("confirm-purchase", "confirm_purchase");
bind("go-account", "go_account");
bind("cancel-membership", "cancel_membership");
bind("keep-membership", "keep_membership");
bind("continue-cancel", "continue_cancel");

(async () => {
  const res = await fetch(`${BASE}/api/session/start?seed=${encodeURIComponent(SEED)}`, {
    method: "POST", credentials: "same-origin",
  });
  render(await res.json());
  if (app.dataset.seq === "0") { /* first render bumps via settle() */ }
})();
