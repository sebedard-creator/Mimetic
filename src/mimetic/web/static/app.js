"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

let S = null;              // dernier état serveur
let currentTrack = 0;      // piste écoutée et affichée (A1/A2 en mode deux canaux)
const waves = {};          // aperçus min/max par emplacement

// Vue de la piste courante : en mono il n'y en a qu'une et rien ne change pour l'utilisateur.
function trackList() { return (S && S.tracks) || []; }
function track() {
  const list = trackList();
  return list[Math.min(currentTrack, list.length - 1)]
    || { profile: null, render: null, label: "mono", eq: (S && S.eq) || { enabled: false, state: "disabled" } };
}
// L'export entrelace toutes les pistes d'un coup : elles doivent donc toutes être rendues.
function allRendered() {
  const list = trackList();
  return list.length > 0 && list.every(t => t.render && !t.render.stale);
}
function eqAllReady() { return trackList().every(t => t.eq.state === "ready"); }

// ---------------------------------------------------------------- API
async function api(method, url, body, raw = false) {
  const opts = { method, headers: {} };
  if (body instanceof Blob) { opts.body = body; opts.headers["Content-Type"] = "application/octet-stream"; }
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  const res = await fetch(url, opts);
  if (!res.ok) {
    let err = { code: "HTTP_" + res.status, message: res.statusText };
    try { err = (await res.json()).error || err; } catch (_) {}
    throw err;
  }
  return raw ? res : res.json();
}

const MESSAGES = {
  MODEL_UNAVAILABLE: "Moteur d'analyse non installé.",
  INSUFFICIENT_SPEECH: "La source ne contient pas assez de dialogue exploitable (au moins 2 s de signal actif).",
  DIRECT_PATH_UNRESOLVED: "Le son direct n'a pas pu être séparé de la reverb dans cette source. Essayez une autre prise.",
  INVALID_AUDIO: "Fichier audio invalide.",
  UNSUPPORTED_FORMAT: "Format non pris en charge : WAV 16/24 bits ou 32 bits flottants, 44,1 ou 48 kHz, mono ou stéréo.",
  LIMIT_EXCEEDED: "Fichier trop long (10 min max).",
};

function showError(err) {
  const bar = $("#errorBar");
  if (!err) { bar.classList.add("hidden"); return; }
  bar.textContent = (MESSAGES[err.code] || err.message || err.code) + (err.detail ? ` (${err.detail})` : "");
  bar.classList.remove("hidden");
}

async function refresh() { await applyState(await api("GET", "/api/state")); }
function send(p) { return p.then(applyState).catch(e => { showError(e); refresh().catch(() => {}); }); }

// ---------------------------------------------------------------- Fichiers
const uploading = {};
$$(".file-card").forEach(card => {
  const slot = card.dataset.slot;
  const drop = $(".drop", card), input = $("input[type=file]", card);
  drop.addEventListener("click", () => input.click());
  input.addEventListener("change", () => { if (input.files[0]) upload(slot, input.files[0]); input.value = ""; });
  drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", e => {
    e.preventDefault(); drop.classList.remove("over");
    if (e.dataTransfer.files[0]) upload(slot, e.dataTransfer.files[0]);
  });
  $("select", card).addEventListener("change", ev => send(api("POST", `/api/channel/${slot}`, { mode: ev.target.value })));
});

async function upload(slot, file) {
  showError(null);
  uploading[slot] = file.name;
  renderFiles();
  try {
    const st = await api("POST", `/api/upload/${slot}?name=${encodeURIComponent(file.name)}`, file);
    delete waves[slot];
    delete uploading[slot];
    await applyState(st);
  } catch (e) {
    delete uploading[slot];
    showError(e);
    await refresh();
  }
}

function esc(s) { return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

function durationVerdict(s) {
  // Fenêtre d'analyse : 6 s, jusqu'à 3 passages (voir engines/recrir/adapter.py).
  if (s < 2) return '<span class="warn-text">trop court pour l\'analyse</span>';
  if (s < 4) return '<span class="warn-text">très court</span>';
  if (s < 10) return "1 passage analysé";
  if (s <= 20) return '<span class="dur-ok">durée idéale</span>';
  return "3 passages analysés au maximum";
}

function fmtAsset(a, slot) {
  const peak = a.peak > 0 ? (20 * Math.log10(a.peak)).toFixed(1) + " dBFS" : "silence";
  const clip = a.clipping_suspected ? ' · <span class="warn-text">écrêtage suspecté</span>' : "";
  const dur = slot === "source" ? " · " + durationVerdict(a.duration_seconds) : "";
  return `<b>${esc(a.name)}</b><br>${(a.sample_rate_hz / 1000).toFixed(1)} kHz · ${a.channels === 1 ? "mono" : "stéréo"} · ` +
    `${a.duration_seconds.toFixed(2)} s · pic ${peak}${clip}${dur}`;
}

function renderFiles() {
  for (const card of $$(".file-card")) {
    const slot = card.dataset.slot, a = S && S[slot];
    card.classList.toggle("loaded", !!a);
    $(".drop-text", card).innerHTML = uploading[slot] ? `Envoi de ${esc(uploading[slot])}…`
      : a ? "Remplacer le fichier" : "Déposer un WAV ou <u>choisir</u>";
    $(".asset-info", card).innerHTML = a ? fmtAsset(a, slot) : "";
    $(".channel", card).classList.toggle("hidden", !a || a.channels === 1 || (S && S.multitrack));
    if (a && a.channels > 1) $("select", card).value = a.channel_mode;
    $(".wave", card).classList.toggle("hidden", !a);
    const listen = $(".listen", card);
    if (listen) listen.classList.toggle("hidden", !a);
  }
}

async function ensureWave(slot) {
  const a = S[slot];
  if (!a) { delete waves[slot]; return; }
  if (!waves[slot] || waves[slot].id !== a.id) waves[slot] = { id: a.id, ov: await api("GET", `/api/waveform/${slot}`) };
}

// ---------------------------------------------------------------- Dessin
function css(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }

function fitCanvas(c) {
  const dpr = window.devicePixelRatio || 1, w = c.clientWidth, h = Number(c.getAttribute("height"));
  if (c.width !== Math.round(w * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
  const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { g, w, h };
}

function drawOverview(slot) {
  const card = $(`.file-card[data-slot=${slot}]`), c = $(".wave", card), a = S[slot], wv = waves[slot];
  if (!a || !wv || c.offsetParent === null) return;
  const { g, w, h } = fitCanvas(c);
  g.clearRect(0, 0, w, h);
  const t = track();
  // En deux pistes, l'aperçu montre le canal de la piste sélectionnée, comme les cadres d'analyse.
  const mode = S.multitrack ? (slot === "source" ? t.source_channel : t.destination_channel) : a.channel_mode;
  const ch = wv.ov.channels;
  const mm = ch.length === 1 || mode === "left" ? ch[0] : mode === "right" ? ch[1]
    : { min: ch[0].min.map((v, i) => (v + ch[1].min[i]) / 2), max: ch[0].max.map((v, i) => (v + ch[1].max[i]) / 2) };
  const n = mm.min.length, mid = h / 2;
  g.fillStyle = css("--wave-dry");
  for (let x = 0; x < w; x++) {
    const i = Math.min(n - 1, Math.floor(x / w * n));
    const top = mid - Math.min(1, mm.max[i]) * mid, bot = mid - Math.max(-1, mm.min[i]) * mid;
    g.fillRect(x, top, 1, Math.max(1, bot - top));
  }
  // passages analysés
  const shown = t.profile;
  if (slot === "source" && shown && shown.valid) {
    const frames = a.frame_count;
    g.strokeStyle = css("--accent2"); g.lineWidth = 1.5;
    for (const win of shown.parameters.windows || []) {
      const x0 = win.window_frames[0] / frames * w, x1 = win.window_frames[1] / frames * w;
      g.strokeRect(x0 + 0.75, 1, Math.max(2, x1 - x0 - 1.5), h - 2);
    }
  }
}

function drawProfile() {
  const p = track().profile, c = $("#profileEnv");
  if (!p || c.offsetParent === null) return;
  const { g, w, h } = fitCanvas(c);
  g.clearRect(0, 0, w, h);
  const db = p.envelope_db.db, floor = -80;
  g.strokeStyle = css("--wave-wet"); g.beginPath();
  db.forEach((v, i) => {
    const x = i / Math.max(1, db.length - 1) * w, y = (Math.max(floor, Math.min(0, v)) / floor) * (h - 4) + 2;
    i ? g.lineTo(x, y) : g.moveTo(x, y);
  });
  g.stroke();
  g.fillStyle = css("--muted"); g.font = "10px system-ui";
  g.fillText(`IR wet estimée (16 kHz, avant extension) — enveloppe dB, ${p.envelope_db.seconds.toFixed(2)} s`, 6, 12);
}

// ---------------------------------------------------------------- Pistes (fichiers 2 canaux)
function renderTracks() {
  const box = $("#trackTabs"), list = trackList(), multi = !!(S && S.multitrack);
  $("#multitrackNotice").classList.toggle("hidden", !multi);
  $("#mismatchNotice").classList.toggle("hidden", !(S && S.channel_mismatch));
  box.classList.toggle("hidden", !multi);
  if (!multi) { currentTrack = 0; box.innerHTML = ""; return; }
  if (currentTrack >= list.length) currentTrack = 0;
  box.innerHTML = list.map((t, i) => `<label class="${i === currentTrack ? "on" : ""}">` +
    `<input type="radio" name="track" value="${i}"${i === currentTrack ? " checked" : ""}> ${esc(t.label)}</label>`).join("");
  $$("input[name=track]", box).forEach(r => r.addEventListener("change", () => {
    // Changer de piste ne relance aucun calcul : on recharge seulement ce qui est affiché et écouté.
    currentTrack = Number(r.value);
    if (isResultKind(player.kind)) stopPlayback();
    player.pos = 0; player.pcm = {}; player.bufs = {}; player.key = null;
    refresh().catch(showError);
  }));
}

// ---------------------------------------------------------------- Statut
function renderStatus() {
  const job = S.job, box = $("#status");
  const running = job.state === "running";
  let text = "", sub = "";
  if (!S.source || !S.destination) {
    box.classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");
  if (running) {
    text = job.step || "Calcul…";
    sub = job.kind === "analysis" ? "Environ 30 s par passage de 6 s analysé (jusqu'à 3 passages)." : "";
  } else if (job.state === "cancelled") text = "Calcul annulé.";
  else if (job.state === "failed") text = "Le calcul a échoué.";
  else if (allRendered()) text = "Reverb prête.";
  else text = "En attente…";
  box.classList.toggle("idle", !running);
  box.classList.toggle("done", !running);
  $("#statusText").textContent = text;
  $("#statusSub").textContent = sub;
  $("#cancel").classList.toggle("hidden", !running);
  $("#retry").classList.toggle("hidden", !(job.state === "failed" || job.state === "cancelled"));
  $("#newPair").classList.toggle("hidden", running);
}
$("#newPair").addEventListener("click", async () => {
  showError(null);
  stopPlayback();
  player.pcm = {}; player.bufs = {}; player.key = null;
  for (const k of Object.keys(fileBuffers)) delete fileBuffers[k];
  for (const k of Object.keys(waves)) delete waves[k];
  try { await applyState(await api("POST", "/api/session/new")); }
  catch (e) { showError(e); refresh().catch(() => {}); }
});
$("#cancel").addEventListener("click", () => send(api("POST", "/api/cancel")));
$("#retry").addEventListener("click", () => { showError(null); send(api("POST", "/api/retry")); });

// ---------------------------------------------------------------- Vider le cache
function fmtBytes(n) {
  if (!n) return "";
  return n >= 1024 * 1024 ? `(${(n / 1024 / 1024).toFixed(1)} Mo)` : `(${Math.round(n / 1024)} ko)`;
}

$("#clearCache").addEventListener("click", () => {
  $("#cacheSize").textContent = fmtBytes(S && S.cache_bytes);
  $("#cacheExportDir").textContent = (S && S.export_dir) || "";
  const e = (S && S.exports) || { count: 0, bytes: 0 };
  $("#exportCount").textContent = e.count === 0 ? "Aucun fichier exporté pour l'instant"
    : e.count === 1 ? `1 fichier exporté ${fmtBytes(e.bytes)}` : `${e.count} fichiers exportés ${fmtBytes(e.bytes)}`;
  $("#cacheDialog").showModal();
});
$("#cacheCancel").addEventListener("click", () => $("#cacheDialog").close());
$("#cacheConfirm").addEventListener("click", async () => {
  $("#cacheDialog").close();
  stopPlayback();
  player.pcm = {}; player.bufs = {}; player.key = null; player.srcKey = null; player.srcBuf = null;
  for (const k of Object.keys(waves)) delete waves[k];
  showError(null);
  try {
    const st = await api("POST", "/api/cache/clear");
    await applyState(st);
    const exp = st.deleted_exports ? `, dont ${st.deleted_exports} fichier(s) exporté(s)` : "";
    $("#statusText").textContent = `Cache vidé ${fmtBytes(st.freed_bytes)}${exp}`.trim() + ".";
    $("#status").classList.remove("hidden");
  } catch (e) { showError(e); refresh().catch(() => {}); }
});

let pollTimer = null;
function schedulePoll() {
  clearTimeout(pollTimer);
  // Une seule piste en retard suffit à garder la page en attente : les exports les prennent ensemble.
  const pending = S.source && S.destination &&
    (S.job.state === "running" || (S.job.state === "idle" && !allRendered()));
  if (pending) pollTimer = setTimeout(() => refresh().catch(showError), 800);
}

// ---------------------------------------------------------------- Résultat
const CHIPS = {
  HF_SYNTHESIZED: ["Aigus > 7,8 kHz synthétisés (non mesurés)", true],
  BANDWIDTH_LIMITED: ["Reverb limitée à 8 kHz", true],
  TAIL_TRUNCATED: ["Queue plus longue que ~1 s : tronquée", true],
  REFERENCE_INCONSISTENT: ["Passages analysés incohérents : source hétérogène ?", true],
  MIX_OVER_0DBFS: ["Pic > 0 dBFS (conservé, pas de limiteur)", true],
  GAIN_UNCALIBRATED: ["Niveau non calibré exactement", true],
  EQ_ROOM_CONTEXT_UNCERTAIN: ["Match EQ : aigus de la reverb synthétiques, raccord moins sûr au-dessus de 7,8 kHz", true],
  EQ_LOW_CONFIDENCE: ["Match EQ : peu de dialogue exploitable, correction prudente", true],
  EQ_CORRECTION_LIMITED: ["Match EQ : correction limitée par les bornes de sécurité", true],
};

function renderResult() {
  const t = track(), r = t.render, p = t.profile;
  // La section reste ouverte dès qu'une piste a un rendu : sinon le sélecteur disparaîtrait
  // avec elle et on ne pourrait plus revenir sur la piste qui a réussi.
  const any = trackList().some(x => x.render);
  $("#result").classList.toggle("hidden", !any);
  if (!any) return;
  $("#result").classList.toggle("track-empty", !r);
  $("#trackEmpty").classList.toggle("hidden", !!r);
  if (!r) {
    $("#roomSummary").textContent = "";
    $("#staleBadge").classList.add("hidden");
    for (const id of ["#export", "#exportMatched", "#exportIrProfile"]) $(id).disabled = true;
    $("#trackEmpty").textContent = `Piste ${t.label} : aucun rendu. `
      + (S.job.state === "running" ? "Calcul en cours." : "Le calcul a échoué ou a été annulé pour cette piste ; "
        + "les autres pistes restent consultables, et l'export attend que toutes soient prêtes.");
    return;
  }
  $("#staleBadge").classList.toggle("hidden", allRendered());
  // Les exports partent ensemble : on attend que chaque piste soit à jour.
  for (const id of ["#export", "#exportMatched", "#exportIrProfile"]) $(id).disabled = !allRendered();
  if (p) {
    const f = (v, d) => v == null ? "n/d" : Number(v).toFixed(d);
    $("#roomSummary").innerHTML = `Pièce estimée : RT60 <b>${f(p.rt60_s, 2)} s</b> · DRR <b>${f(p.drr_db, 1)} dB</b>` +
      ` <span class="muted">(moteur expérimental)</span>`;
  }
  const chips = [["Analyse expérimentale (Rec-RIR)", false], ...r.warnings.map(w => CHIPS[w] || [w, true])];
  $("#chips").innerHTML = chips.map(([t, w]) => `<span class="chip${w ? " w" : ""}">${esc(t)}</span>`).join("");
  if (S.last_export) {
    $("#exportInfo").innerHTML = `Téléchargé, et conservé dans <code>${esc(S.last_export.directory)}</code> : ` +
      Object.values(S.last_export.files).map(n => `<a href="/api/exports/${encodeURIComponent(n)}" download>${esc(n)}</a>`).join(" · ");
  } else $("#exportInfo").textContent = "";
}

let settingsTimer = null;
function onSetting() {
  $("#gainOut").textContent = Number($("#wetGain").value).toFixed(1) + " dB";
  $("#delayOut").textContent = $("#predelay").value + " ms";
  clearTimeout(settingsTimer);
  settingsTimer = setTimeout(() => send(api("POST", "/api/settings", {
    wet_gain_db: Number($("#wetGain").value), additional_predelay_ms: Number($("#predelay").value),
  })), 300);
}
$("#wetGain").addEventListener("input", onSetting);
$("#predelay").addEventListener("input", onSetting);
// Téléchargement du WAV dès l'export : le fichier reste aussi dans le dossier d'exports du service.
function downloadExported(st) {
  const files = st.last_export && st.last_export.files;
  if (!files) return;
  const wav = Object.entries(files).find(([k]) => k.endsWith("_wav"));
  if (!wav) return;
  const a = document.createElement("a");
  a.href = `/api/exports/${encodeURIComponent(wav[1])}`;
  a.download = wav[1];
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function runExport(mode) {
  showError(null);
  const st = await api("POST", "/api/export", { mode });
  await applyState(st);
  downloadExported(st);
  return st;
}

$("#export").addEventListener("click", () => runExport("wet").catch(e => { showError(e); refresh().catch(() => {}); }));
$("#exportIrProfile").addEventListener("click", () => runExport("ir_profile").catch(e => { showError(e); refresh().catch(() => {}); }));
// Le clip traité inclut toujours le raccord de timbre : au besoin on active Match EQ et on attend.
$("#exportMatched").addEventListener("click", async () => {
  showError(null);
  try {
    if (!S.eq.enabled) {
      $("#eqEnabled").checked = true;
      await applyState(await api("POST", "/api/eq/settings", { enabled: true, amount: Number($("#eqAmount").value) / 100 }));
    }
    for (let i = 0; i < 300 && (!eqAllReady() || !allRendered() || S.job.state === "running"); i++) {
      if (trackList().some(t => t.eq.state === "failed")) break;
      $("#statusText").textContent = "Préparation du clip traité…";
      await new Promise(r => setTimeout(r, 500));
      await refresh();
    }
    if (!eqAllReady()) { showError(eqBlockedError()); renderEq(); return; }
    await runExport("matched");
  } catch (e) { showError(e); refresh().catch(() => {}); }
});

// ---------------------------------------------------------------- Match EQ
// Le clip traité exige le Match EQ sur *toutes* les pistes : nommer celles qui bloquent.
function eqBlockedError() {
  const ko = trackList().filter(t => t.eq.state !== "ready");
  const why = t => t.eq.state === "failed"
    ? (EQ_ERRORS[t.eq.error.code] || t.eq.error.message) : "analyse du timbre incomplète";
  const detail = S.multitrack
    ? ko.map(t => `${t.label} (${why(t)})`).join(", ")
    : (ko.length ? why(ko[0]) : "");
  return { code: "EQ_BLOCKED", message: "Export EQ_IR_MIX bloqué : Match EQ impossible sur " + detail
    + ". Les deux pistes sortent dans le même fichier, il ne peut pas être écrit à moitié corrigé."
    + " L'export IR_ONLY, lui, reste disponible." };
}

const EQ_ERRORS = {
  EQ_INSUFFICIENT_SPEECH: "pas assez de dialogue exploitable des deux côtés",
  EQ_NO_RELIABLE_BANDS: "trop peu de bandes fiables pour une correction",
  EQ_FILTER_FIT_FAILED: "filtre irréalisable dans les tolérances",
  EQ_REFERENCE_CONTAMINATED: "source trop contaminée (musique, autre voix ?)",
};

function sendEq() {
  return send(api("POST", "/api/eq/settings", {
    enabled: $("#eqEnabled").checked, amount: Number($("#eqAmount").value) / 100,
  }));
}
$("#eqEnabled").addEventListener("change", sendEq);
let eqTimer = null;
$("#eqAmount").addEventListener("input", () => {
  $("#eqAmountOut").textContent = $("#eqAmount").value + " %";
  clearTimeout(eqTimer);
  eqTimer = setTimeout(sendEq, 350);
});

function renderEq() {
  const eq = track().eq, r = track().render;
  renderOtherTracksEq();
  $("#eqEnabled").checked = eq.enabled;
  $("#eqAmountWrap").classList.toggle("hidden", !eq.enabled || eq.state !== "ready");
  $("#origOpt").classList.toggle("hidden", !(r && r.eq_applied));
  const el = $("#eqState");
  el.className = "small";
  if (!eq.enabled) { el.textContent = "Désactivé : l'ADR n'est pas modifié, seule la reverb est calculée."; el.classList.add("muted"); return; }
  if (eq.state === "pending") { el.textContent = "Analyse du timbre…"; el.classList.add("eq-busy"); return; }
  if (eq.state === "failed") {
    el.textContent = "Match EQ indisponible : " + (EQ_ERRORS[eq.error.code] || eq.error.message)
      + (eq.error.detail ? ` — ${eq.error.detail}` : "") + ". La reverb reste calculée sans correction.";
    el.classList.add("eq-fail");
    return;
  }
  const c = eq.curve || {};
  const gain = eq.applied_common_gain_db;
  el.innerHTML = `<span class="eq-ready">Correction active</span> · écart de niveau retiré ${(c.level_offset_db || 0).toFixed(1)} dB` +
    (gain == null ? "" : ` · gain de compensation ${gain.toFixed(1)} dB`) +
    (c.saturated_fraction > 0.15 ? ' · <span class="eq-fail">correction limitée par les bornes</span>' : "");
}

// État du Match EQ des pistes qu'on ne regarde pas : sans cela, un échec sur A2 reste invisible
// depuis A1, où tout semble en ordre.
function renderOtherTracksEq() {
  const el = $("#eqOtherTracks"), others = trackList().filter(t => t.index !== track().index);
  const ko = others.filter(t => t.eq.state === "failed");
  el.className = "small";
  el.classList.toggle("hidden", !(S.multitrack && S.eq.enabled && ko.length));
  if (!ko.length) { el.textContent = ""; return; }
  el.classList.add("eq-fail");
  el.textContent = "⚠ " + ko.map(t => `${t.label} : Match EQ indisponible`).join(" · ")
    + " — l'export EQ_IR_MIX est bloqué tant que c'est le cas.";
}

// ---------------------------------------------------------------- Écoute
const player = { ctx: null, gain: null, pcm: {}, bufs: {}, key: null, src: null, kind: null, startedAt: 0, pos: 0 };

function audioCtx() {
  if (!player.ctx) {
    player.ctx = new (window.AudioContext || window.webkitAudioContext)();
    player.gain = player.ctx.createGain();
    player.gain.connect(player.ctx.destination);
    applyMonitor();
  }
  if (player.ctx.state === "suspended") player.ctx.resume();
  return player.ctx;
}

function applyMonitor() {
  const db = Number($("#monitor").value);
  $("#monOut").textContent = db.toFixed(1) + " dB";
  if (player.gain) player.gain.gain.value = Math.pow(10, db / 20);
}
$("#monitor").addEventListener("input", applyMonitor);

async function loadPcm(kind) {
  const res = await api("GET", `/api/pcm/${kind}?track=${currentTrack}`, undefined, true);
  return { fs: Number(res.headers.get("X-Sample-Rate")), data: new Float32Array(await res.arrayBuffer()) };
}

function toBuffer(p) {
  const b = audioCtx().createBuffer(1, Math.max(1, p.data.length), p.fs);
  b.copyToChannel(p.data, 0);
  return b;
}

function isResultKind(k) { return k === "dry" || k === "wet" || k === "mix" || k === "original"; }
function currentPos() { return player.src && player.ctx ? player.ctx.currentTime - player.startedAt : player.pos; }

function stopPlayback() {
  if (player.src) {
    const p = currentPos();
    player.src.onended = null;
    try { player.src.stop(); } catch (_) {}
    player.src = null;
    if (isResultKind(player.kind)) player.pos = p;
  }
  player.kind = null;
  $("#play").textContent = "▶";
  $$(".file-card .listen").forEach(b => { b.textContent = "▶ Écouter"; });
}

function startResult(kind) {
  const p = player.pcm[kind];
  if (!p) return;
  const ctx = audioCtx();
  if (!player.bufs[kind]) player.bufs[kind] = toBuffer(p);
  stopPlayback();
  const src = ctx.createBufferSource();
  src.buffer = player.bufs[kind];
  src.connect(player.gain);
  const dur = p.data.length / p.fs, offset = player.pos >= dur ? 0 : player.pos;
  src.start(0, offset);
  player.startedAt = ctx.currentTime - offset;
  player.src = src; player.kind = kind;
  src.onended = () => { if (player.src === src) { player.src = null; player.kind = null; player.pos = 0; $("#play").textContent = "▶"; } };
  $("#play").textContent = "■";
}

async function loadResultBuffers() {
  const r = track().render;
  if (!r || r.stale || player.key === `${currentTrack}:${r.dependency_key}`) return;
  const wasPlaying = player.src && isResultKind(player.kind);
  const pos = currentPos();
  const needOriginal = !!r.eq_applied;
  const [dry, wet, original] = await Promise.all([loadPcm("dry"), loadPcm("wet"),
    needOriginal ? loadPcm("original") : Promise.resolve(null)]);
  const mix = { fs: dry.fs, data: new Float32Array(dry.data.length) };
  for (let i = 0; i < mix.data.length; i++) mix.data[i] = dry.data[i] + wet.data[i];
  if (isResultKind(player.kind)) stopPlayback();  // l'arrivée d'un rendu ne coupe pas l'écoute d'un import
  player.pcm = original ? { dry, wet, mix, original } : { dry, wet, mix };
  player.bufs = {};
  player.key = `${currentTrack}:${r.dependency_key}`;
  player.pos = Math.min(pos, dry.data.length / dry.fs);
  drawResult();
  if (wasPlaying) startResult(selectedSource());
}

function selectedSource() { return $("input[name=source]:checked").value; }

$("#play").addEventListener("click", () => {
  if (player.src && isResultKind(player.kind)) stopPlayback(); else startResult(selectedSource());
});
$$("input[name=source]").forEach(r => r.addEventListener("change", () => {
  if (player.src && isResultKind(player.kind)) { player.pos = currentPos(); startResult(selectedSource()); }
}));
$("#resultWave").addEventListener("click", e => {
  const p = player.pcm.dry; if (!p) return;
  const rect = e.currentTarget.getBoundingClientRect();
  player.pos = (e.clientX - rect.left) / rect.width * p.data.length / p.fs;
  if (player.src && isResultKind(player.kind)) startResult(player.kind); else drawResult();
});

// Écoute des fichiers importés : un transport par fenêtre, indépendant de celui du résultat.
const fileBuffers = {};
$$(".file-card .listen").forEach(btn => {
  const slot = btn.closest(".file-card").dataset.slot;
  btn.addEventListener("click", async () => {
    if (player.kind === slot) { stopPlayback(); return; }
    try {
      const a = S[slot];
      if (!a) return;
      const key = a.id + a.channel_mode + ":" + currentTrack;
      const cached = fileBuffers[slot];
      if (!cached || cached.key !== key) fileBuffers[slot] = { key, pcm: await loadPcm(slot), buf: null };
      const ctx = audioCtx();
      const entry = fileBuffers[slot];
      if (!entry.buf) entry.buf = toBuffer(entry.pcm);
      stopPlayback();
      const src = ctx.createBufferSource();
      src.buffer = entry.buf;
      src.connect(player.gain);
      src.start();
      player.src = src; player.kind = slot;
      src.onended = () => { if (player.src === src) stopPlayback(); };
      btn.textContent = "■ Arrêter";
    } catch (e) { showError(e); }
  });
});

function drawResult() {
  const c = $("#resultWave"), { dry, wet } = player.pcm;
  if (!dry || c.offsetParent === null) return;
  const { g, w, h } = fitCanvas(c);
  g.clearRect(0, 0, w, h);
  const n = dry.data.length, mid = h / 2, step = n / w;
  for (const [sig, color] of [[wet.data, css("--wave-wet")], [dry.data, css("--wave-dry")]]) {
    g.fillStyle = color;
    for (let x = 0; x < w; x++) {
      let mn = 0, mx = 0;
      const a = Math.floor(x * step), b = Math.min(n, Math.floor((x + 1) * step) + 1);
      for (let i = a; i < b; i += Math.max(1, Math.floor((b - a) / 64))) { const v = sig[i]; if (v < mn) mn = v; if (v > mx) mx = v; }
      g.fillRect(x, mid - Math.min(1, mx) * mid, 1, Math.max(1, (Math.min(1, mx) - Math.max(-1, mn)) * mid));
    }
  }
  const pos = isResultKind(player.kind) ? currentPos() : player.pos;
  g.fillStyle = css("--accent2");
  g.fillRect(pos / (n / dry.fs) * w, 0, 1.5, h);
}

(function tick() {
  if (player.pcm.dry) {
    const pos = isResultKind(player.kind) ? currentPos() : player.pos;
    $("#pos").textContent = pos.toFixed(3) + " s";
    if (player.src && isResultKind(player.kind)) drawResult();
  }
  requestAnimationFrame(tick);
})();

// ---------------------------------------------------------------- Application de l'état
let firstState = true;
async function applyState(st) {
  S = st;
  if (firstState) {
    firstState = false;
    $("#wetGain").value = S.settings.wet_gain_db;
    $("#predelay").value = S.settings.additional_predelay_ms;
    $("#gainOut").textContent = Number(S.settings.wet_gain_db).toFixed(1) + " dB";
    $("#delayOut").textContent = Math.round(S.settings.additional_predelay_ms) + " ms";
    $("#eqAmount").value = Math.round((S.eq.amount ?? 1) * 100);
    $("#eqAmountOut").textContent = $("#eqAmount").value + " %";
  }
  $("#engineMissing").classList.toggle("hidden", !!S.engine.available);
  $("#engineDetail").textContent = S.engine.detail ? `(${S.engine.detail})` : "";

  renderTracks();
  $("#commonSettings").classList.toggle("hidden", !S.multitrack);
  renderFiles();
  try { await ensureWave("source"); await ensureWave("destination"); } catch (e) { showError(e); }
  renderStatus();
  renderResult();
  renderEq();
  if (S.last_error) showError(S.last_error);
  else if (S.job.state === "running") showError(null);

  if (track().render && !track().render.stale) { try { await loadResultBuffers(); } catch (e) { showError(e); } }
  if (!track().render) {
    // Ne jamais interrompre l'écoute d'un fichier importé : la page se rafraîchit toutes les 0,8 s
    // pendant l'analyse, seul le transport du résultat est concerné.
    if (isResultKind(player.kind)) stopPlayback();
    player.pcm = {}; player.key = null;
  }

  const dump = { ...S };
  if (dump.profile) dump.profile = { ...dump.profile, envelope_db: "…" };
  $("#techDump").textContent = JSON.stringify(dump, null, 2);
  redrawAll();
  schedulePoll();
}

function redrawAll() {
  if (!S) return;
  drawOverview("source"); drawOverview("destination"); drawResult(); drawProfile();
}
window.addEventListener("resize", redrawAll);
$(".tech").addEventListener("toggle", redrawAll);
$("#monOut").textContent = Number($("#monitor").value).toFixed(1) + " dB";
refresh().catch(showError);
