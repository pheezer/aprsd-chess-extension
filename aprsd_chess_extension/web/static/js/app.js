// APRS Chess web client. The Python side owns all chess rules (python-chess);
// this file draws the board, sends the player's actions, and re-renders from
// the server-pushed state.
import { Chessground } from "../vendor/chessground/chessground.min.js";

const $ = (id) => document.getElementById(id);

let state = null;
let selected = null; // game code shown on the board
let ground = null;
let pendingPromotion = null;

function api(path, body) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  }).then(async (res) => {
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
    if (data.state) render(data.state);
    return data;
  });
}

function setStatus(el, text, kind) {
  el.textContent = text || "";
  el.className = "status-line" + (kind ? ` ${kind}` : "");
}

function currentGame() {
  return state && state.games.find((g) => g.code === selected);
}

function colorName(c) {
  return c === "W" ? "white" : "black";
}

function describe(game) {
  if (game.syncing && !game.color) return { text: "Loading from server…", kind: "pending" };
  if (game.result && game.result !== "*") {
    const why = game.termination ? ` (${game.termination})` : "";
    if (game.result === "-") return { text: `Game cancelled${why}`, kind: "over" };
    if (game.result === "?") return { text: `Game not found on server`, kind: "over" };
    return { text: `Game over ${game.result}${why}`, kind: "over" };
  }
  const p = game.pending;
  if (p) {
    if (p.failed) return { text: "No answer from the server. Retry?", kind: "pending" };
    const what = p.kind === "move" ? p.san : p.kind;
    return { text: `${what} sent${p.acked ? ", delivered" : ""}… waiting for server`, kind: "pending" };
  }
  if (game.error) return { text: game.error, kind: "over" };
  if (game.syncing) return { text: "Syncing…", kind: "pending" };
  if (game.draw_offer && game.draw_offer !== game.color) {
    return { text: `${game.opponent} offers a draw`, kind: "mine" };
  }
  if (game.turn === game.color) {
    return { text: game.check ? "Check! Your move" : "Your move", kind: "mine" };
  }
  if (game.color === "W" && !game.joined && game.ply <= 1) {
    return { text: `Waiting for ${game.opponent} to join…`, kind: "" };
  }
  return { text: `Waiting for ${game.opponent}…`, kind: "" };
}

function renderGames() {
  const list = $("games");
  list.innerHTML = "";
  const games = state.games.filter((g) => g.color || g.syncing);
  if (!games.length) {
    const li = document.createElement("li");
    li.className = "empty-note";
    li.textContent = "No games yet.";
    list.appendChild(li);
    return;
  }
  for (const game of games) {
    const li = document.createElement("li");
    if (game.code === selected) li.className = "active";
    const btn = document.createElement("button");
    btn.type = "button";
    const label = document.createElement("span");
    label.textContent = `${game.code} vs ${game.opponent || "…"}`;
    const turn = document.createElement("span");
    const mine = game.result === "*" && game.turn && game.turn === game.color && !game.pending;
    turn.className = "turn" + (mine ? " mine" : "");
    turn.textContent =
      game.result && game.result !== "*" ? game.result.replace("1/2-1/2", "½-½") : mine ? "your move" : game.pending ? "sending" : "";
    btn.append(label, turn);
    btn.addEventListener("click", () => select(game.code));
    li.appendChild(btn);
    list.appendChild(li);
  }
}

function destsMap(legal) {
  const map = new Map();
  for (const [from, tos] of Object.entries(legal || {})) map.set(from, tos);
  return map;
}

function renderBoard(game) {
  const config = {
    fen: game.fen ? game.fen.split(" ")[0] : "8/8/8/8/8/8/8/8",
    orientation: colorName(game.color || "W"),
    turnColor: colorName(game.turn || "W"),
    lastMove: game.last_move ? [game.last_move.slice(0, 2), game.last_move.slice(2, 4)] : undefined,
    check: game.check ? colorName(game.turn) : false,
    movable: {
      free: false,
      color: game.result === "*" && !game.pending && game.turn === game.color ? colorName(game.color) : undefined,
      dests: destsMap(game.legal),
      showDests: true,
      events: { after: onMove },
    },
    premovable: { enabled: false },
    draggable: { showGhost: true },
    animation: { enabled: true, duration: 200 },
    coordinates: true,
  };
  if (!ground) ground = Chessground($("board"), config);
  else ground.set(config);
}

function renderMoves(game) {
  const ol = $("moves");
  ol.innerHTML = "";
  const moves = game.san_moves || [];
  const basePly = game.base_ply || 0;
  if (basePly > 0) {
    const note = document.createElement("li");
    note.className = "note";
    note.textContent = `(restored at move ${Math.floor(basePly / 2) + 1})`;
    ol.appendChild(note);
  }
  for (let i = 0; i < moves.length; i++) {
    const ply = basePly + i + 1;
    const li = document.createElement("li");
    if (ply % 2 === 1 || i === 0) {
      const num = document.createElement("span");
      num.className = "num";
      num.textContent = ply % 2 === 1 ? `${(ply + 1) / 2}.` : `${ply / 2}…`;
      li.appendChild(num);
    }
    li.appendChild(document.createTextNode(moves[i]));
    ol.appendChild(li);
  }
}

function renderGame() {
  const game = currentGame();
  $("empty").hidden = !!game;
  $("game").hidden = !game;
  if (!game) return;
  $("game-code").textContent = game.code;
  $("game-title").textContent = game.color
    ? `You (${game.color === "W" ? "White" : "Black"}) vs ${game.opponent}`
    : "";
  const status = describe(game);
  const el = $("game-status");
  el.textContent = status.text;
  el.className = "game-status" + (status.kind ? ` ${status.kind}` : "");

  const over = game.result && game.result !== "*";
  const offerFromThem = game.draw_offer && game.draw_offer !== game.color;
  $("btn-draw").textContent = offerFromThem ? "Accept draw" : "Offer draw";
  $("btn-draw").disabled = over || !game.color || !!game.pending;
  $("btn-resign").disabled = over || !game.color || !!game.pending;
  $("btn-resign").textContent = game.color === "W" && !game.joined && game.ply === 0 ? "Cancel game" : "Resign";
  $("btn-retry").hidden = !(game.pending && game.pending.failed);
  $("btn-forget").hidden = !over;
  if (game.color && game.fen) renderBoard(game);
  renderMoves(game);
}

function renderLog() {
  const ul = $("log");
  ul.innerHTML = "";
  for (const entry of state.log.slice().reverse()) {
    const li = document.createElement("li");
    li.className = entry.dir;
    const dir = document.createElement("span");
    dir.className = "dir";
    dir.textContent = entry.dir === "tx" ? "TX" : entry.dir === "rx" ? "RX" : "··";
    li.append(dir, document.createTextNode(entry.text));
    ul.appendChild(li);
  }
}

function renderNewStatus() {
  const p = state.pending_new;
  const el = $("new-status");
  if (!p) return;
  if (p.error) setStatus(el, p.error, "error");
  else setStatus(el, `Challenge sent to ${p.opponent}… waiting for server`, "pending");
}

function render(next) {
  const prev = state;
  state = next;
  // Jump to a newly created or resumed game.
  if (prev) {
    const known = new Set(prev.games.map((g) => g.code));
    const fresh = next.games.find((g) => !known.has(g.code) && g.color);
    if (fresh) select(fresh.code, false);
    if (prev.pending_new && !next.pending_new) setStatus($("new-status"), "");
  }
  if (!selected && next.games.length) selected = next.games[0].code;
  if (selected && !next.games.find((g) => g.code === selected)) selected = next.games.length ? next.games[0].code : null;
  const resumeError = next.resume_error;
  if (resumeError) setStatus($("resume-status"), `${resumeError.code}: ${resumeError.error}`, "error");
  else if (currentGame() && currentGame().color) setStatus($("resume-status"), "");
  renderNewStatus();
  renderGames();
  renderGame();
  renderLog();
}

function select(code, rerender = true) {
  selected = code;
  if (rerender && state) {
    renderGames();
    renderGame();
  }
}

function onMove(orig, dest) {
  const game = currentGame();
  if (!game) return;
  const fen = game.fen.split(" ")[0];
  const rows = fen.split("/");
  const file = orig.charCodeAt(0) - 97;
  const rank = parseInt(orig[1], 10);
  const row = rows[8 - rank];
  let col = 0;
  let piece = null;
  for (const ch of row) {
    if (/\d/.test(ch)) col += parseInt(ch, 10);
    else {
      if (col === file) piece = ch;
      col += 1;
    }
  }
  const promotes = piece && piece.toLowerCase() === "p" && (dest[1] === "8" || dest[1] === "1");
  if (promotes) {
    pendingPromotion = { code: game.code, uci: orig + dest };
    $("promotion").hidden = false;
    return;
  }
  sendMove(game.code, orig + dest);
}

function sendMove(code, uci) {
  api("/api/move", { code, uci }).catch((err) => {
    $("game-status").textContent = err.message;
    $("game-status").className = "game-status over";
    renderGame();
  });
}

$("promotion").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button");
  if (!btn || !pendingPromotion) return;
  const piece = btn.dataset.piece;
  $("promotion").hidden = true;
  const pending = pendingPromotion;
  pendingPromotion = null;
  if (piece) sendMove(pending.code, pending.uci + piece);
  else renderGame(); // put the pawn back
});

$("new-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const mycall = $("mycall").value.trim();
  const theircall = $("theircall").value.trim();
  setStatus($("new-status"), "Sending…", "pending");
  api("/api/new", { mycall, theircall }).catch((err) => setStatus($("new-status"), err.message, "error"));
});

$("resume-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const code = $("code").value.trim();
  setStatus($("resume-status"), `Asking the server for ${code}…`, "pending");
  api("/api/resume", { code })
    .then(() => select(code))
    .catch((err) => setStatus($("resume-status"), err.message, "error"));
});

$("refresh-list").addEventListener("click", () => api("/api/list").catch(() => {}));

function gameAction(path, confirmText) {
  const game = currentGame();
  if (!game) return;
  if (confirmText && !window.confirm(confirmText)) return;
  api(path, { code: game.code }).catch((err) => {
    $("game-status").textContent = err.message;
    $("game-status").className = "game-status over";
  });
}

$("btn-resync").addEventListener("click", () => gameAction("/api/sync"));
$("btn-retry").addEventListener("click", () => gameAction("/api/retry"));
$("btn-draw").addEventListener("click", () => gameAction("/api/draw"));
$("btn-resign").addEventListener("click", () => {
  const game = currentGame();
  const cancelling = game && game.color === "W" && !game.joined && game.ply === 0;
  gameAction("/api/resign", cancelling ? "Cancel this game?" : "Resign this game?");
});
$("btn-forget").addEventListener("click", () => gameAction("/api/forget"));

function connect() {
  const conn = $("conn");
  const source = new EventSource("/events");
  source.onopen = () => (conn.className = "conn up");
  source.onmessage = (ev) => render(JSON.parse(ev.data));
  source.onerror = () => {
    conn.className = "conn down";
  };
}

fetch("/api/state")
  .then((res) => res.json())
  .then(render)
  .finally(connect);
