"use strict";
/* Door Run client (SPEC section 5).
 *
 * The walker reads this page only through the DOM contract: the data-testid
 * nodes below, #game[data-state] and #game[data-seq]. data-seq is the render
 * counter: it rises once per completed render, which is how the walker knows a
 * new level has loaded even though data-state stays "level" from one level to
 * the next.
 */

const game = document.getElementById("game");
const seedParam = new URLSearchParams(location.search).get("seed");
const SEED = Number.isFinite(Number(seedParam)) ? Number(seedParam) : 0;

let seq = 0;
let screen = "start";
let wins = 0;
let levelsPlayed = 0;

const $ = (id) => document.querySelector(`[data-testid="${id}"]`);

async function api(path, body) {
  let res;
  /* Back off and retry when the hosted copy rate-limits us (HTTP 429), so a
   * busy server never leaves "undefined" in the page for the walker to read. */
  for (let attempt = 0; attempt < 6; attempt += 1) {
    res = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
    });
    if (res.status !== 429) break;
    await new Promise((r) => setTimeout(r, 500 * (attempt + 1)));
  }
  const text = await res.text();
  let data = {};
  if (text) { try { data = JSON.parse(text); } catch { data = {}; } }
  return { ok: res.ok, status: res.status, data };
}

/* Render, then bump data-seq on the next frame so the counter never rises
 * before the DOM the walker is about to read has actually been painted. */
function render() {
  game.dataset.state = screen;
  for (const panel of document.querySelectorAll(".panel")) {
    panel.hidden = panel.dataset.screen !== screen;
  }
  requestAnimationFrame(() => {
    seq += 1;
    game.dataset.seq = String(seq);
  });
}

function setLevel(payload) {
  $("level-no").textContent = String(payload.level_no);
  $("last-result").textContent = payload.last_result;
  const doors = payload.doors || [];
  for (let i = 0; i < 3; i += 1) {
    const node = $(`door-${i}-chance`);
    const btn = $(`door-${i}`);
    const door = doors[i];
    node.textContent = door ? String(door.win_chance) : "0";
    btn.disabled = !door;
  }
}

async function refreshLevel() {
  const { data } = await api("api/level/current");
  setLevel(data);
}

async function start() {
  const { data } = await api(`api/session/start?seed=${encodeURIComponent(SEED)}`, {});
  $("gems").textContent = String(data.gems);
  $("levels-total").textContent = String(data.levels_total);
  $("levels-played").textContent = String(data.levels_total);
  screen = "start";
  render();
}

$("open-store").addEventListener("click", () => {
  $("store-note").textContent = "";
  screen = "store";
  render();
});

$("close-store").addEventListener("click", async () => {
  await refreshLevel();
  screen = "level";
  render();
});

$("buy-gold-frame").addEventListener("click", async () => {
  const { ok, status, data } = await api("api/store/buy", { item_id: "gold_frame" });
  if (ok) {
    $("gems").textContent = String(data.gems);
    $("store-note").textContent = "Bought.";
  } else {
    $("store-note").textContent = status === 409 ? "Already owned." : "Not available.";
  }
  render();
});

for (let i = 0; i < 3; i += 1) {
  $(`door-${i}`).addEventListener("click", async () => {
    const { data } = await api("api/level/pick", { door: i });
    levelsPlayed += 1;
    if (data.result === "WIN") { wins += 1; }
    $("wins").textContent = String(wins);
    $("last-result").textContent = data.result;
    $("gems").textContent = String(data.gems);

    if (data.offer) {
      $("offer-price").textContent = String(data.offer.price);
      screen = "offer";
    } else if (data.done) {
      screen = "done";
    } else {
      await refreshLevel();
      screen = "level";
    }
    render();
  });
}

$("offer-dismiss").addEventListener("click", async () => {
  const { data } = await api("api/offer/dismiss", {});
  if (data.done) {
    screen = "done";
  } else {
    await refreshLevel();
    screen = "level";
  }
  render();
});

start();
