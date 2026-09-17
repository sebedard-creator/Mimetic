"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

let S = null;              // dernier état serveur
const waves = {};          // aperçus min/max par emplacement

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

function fmtAsset(a) {
  const peak = a.peak > 0 ? (20 * Math.log10(a.peak)).toFixed(1) + " dBFS" : "silence";
  const clip = a.clipping_suspected ? ' · <span class="warn-text">écrêtage suspecté</span>' : "";
  return `<b>${esc(a.name)}</b><br>${(a.sample_rate_hz / 1000).toFixed(1)} kHz · ${a.channels === 1 ? "mono" : "stéréo"} · ` +
    `${a.duration_seconds.toFixed(2)} s · pic ${peak}${clip}`;
}

function renderFiles() {
  for (const card of $$(".file-card")) {
    const slot = card.dataset.slot, a = S && S[slot];
    card.classList.toggle("loaded", !!a);
    $(".drop-text", card).innerHTML = uploading[slot] ? `Envoi de ${esc(uploading[slot])}…`
      : a ? "Remplacer le fichier" : "Déposer un WAV ou <u>choisir</u>";
    $(".asset-info", card).innerHTML = a ? fmtAsset(a) : "";
    $(".channel", card).classList.toggle("hidden", !a || a.channels === 1);
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
  const ch = wv.ov.channels, mode = a.channel_mode;
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
  if (slot === "source" && S.profile && S.profile.valid) {
    const frames = a.frame_count;
    g.strokeStyle = css("--accent2"); g.lineWidth = 1.5;
    for (const win of S.profile.parameters.windows || []) {
      const x0 = win.window_frames[0] / frames * w, x1 = win.window_frames[1] / frames * w;
      g.strokeRect(x0 + 0.75, 1, Math.max(2, x1 - x0 - 1.5), h - 2);
    }
  }
}

function drawProfile() {
  const p = S.profile, c = $("#profileEnv");
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
  else if (S.render && !S.render.stale) text = "Reverb prête.";
  else text = "En attente…";
  box.classList.toggle("idle", !running);
  box.classList.toggle("done", !running);
  $("#statusText").textContent = text;
  $("#statusSub").textContent = sub;
  $("#cancel").classList.toggle("hidden", !running);
  $("#retry").classList.toggle("hidden", !(job.state === "failed" || job.state === "cancelled"));
}
$("#cancel").addEventListener("click", () => send(api("POST", "/api/cancel")));
$("#retry").addEventListener("click", () => { showError(null); send(api("POST", "/api/retry")); });

let pollTimer = null;
function schedulePoll() {
  clearTimeout(pollTimer);
  const pending = S.source && S.destination && (S.job.state === "running" || (S.render && S.render.stale) ||
    (!S.render && S.job.state === "idle"));
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
};

function renderResult() {
  const r = S.render, p = S.profile;
  $("#result").classList.toggle("hidden", !r);
  if (!r) return;
  $("#staleBadge").classList.toggle("hidden", !r.stale);
  $("#export").disabled = r.stale;
  if (p) {
    const f = (v, d) => v == null ? "n/d" : Number(v).toFixed(d);
    $("#roomSummary").innerHTML = `Pièce estimée : RT60 <b>${f(p.rt60_s, 2)} s</b> · DRR <b>${f(p.drr_db, 1)} dB</b>` +
      ` <span class="muted">(moteur expérimental)</span>`;
  }
  const chips = [["Analyse expérimentale (Rec-RIR)", false], ...r.warnings.map(w => CHIPS[w] || [w, true])];
  $("#chips").innerHTML = chips.map(([t, w]) => `<span class="chip${w ? " w" : ""}">${esc(t)}</span>`).join("");
  if (S.last_export) {
    $("#exportInfo").innerHTML = `Exporté dans <code>${esc(S.last_export.directory)}</code> — télécharger : ` +
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
$("#export").addEventListener("click", () => send(api("POST", "/api/export", { export_ir: $("#exportIr").checked })));

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
  const res = await api("GET", `/api/pcm/${kind}`, undefined, true);
  return { fs: Number(res.headers.get("X-Sample-Rate")), data: new Float32Array(await res.arrayBuffer()) };
}

function toBuffer(p) {
  const b = audioCtx().createBuffer(1, Math.max(1, p.data.length), p.fs);
  b.copyToChannel(p.data, 0);
  return b;
}

function isResultKind(k) { return k === "dry" || k === "wet" || k === "mix"; }
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
  const l = $(".file-card[data-slot=source] .listen"); if (l) l.textContent = "▶ Écouter";
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
  if (!S.render || S.render.stale || player.key === S.render.dependency_key) return;
  const wasPlaying = player.src && isResultKind(player.kind);
  const pos = currentPos();
  const [dry, wet] = await Promise.all([loadPcm("dry"), loadPcm("wet")]);
  const mix = { fs: dry.fs, data: new Float32Array(dry.data.length) };
  for (let i = 0; i < mix.data.length; i++) mix.data[i] = dry.data[i] + wet.data[i];
  stopPlayback();
  player.pcm = { dry, wet, mix };
  player.bufs = {};
  player.key = S.render.dependency_key;
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

const sourceListen = $(".file-card[data-slot=source] .listen");
sourceListen.addEventListener("click", async () => {
  if (player.kind === "source") { stopPlayback(); return; }
  try {
    const key = S.source.id + S.source.channel_mode;
    if (player.srcKey !== key) { player.srcPcm = await loadPcm("source"); player.srcKey = key; player.srcBuf = null; }
    const ctx = audioCtx();
    if (!player.srcBuf) player.srcBuf = toBuffer(player.srcPcm);
    stopPlayback();
    const src = ctx.createBufferSource();
    src.buffer = player.srcBuf; src.connect(player.gain); src.start();
    player.src = src; player.kind = "source";
    src.onended = () => { if (player.src === src) stopPlayback(); };
    sourceListen.textContent = "■ Arrêter";
  } catch (e) { showError(e); }
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
  }
  $("#engineMissing").classList.toggle("hidden", !!S.engine.available);
  $("#engineDetail").textContent = S.engine.detail ? `(${S.engine.detail})` : "";

  renderFiles();
  try { await ensureWave("source"); await ensureWave("destination"); } catch (e) { showError(e); }
  renderStatus();
  renderResult();
  if (S.last_error) showError(S.last_error);
  else if (S.job.state === "running") showError(null);

  if (S.render && !S.render.stale) { try { await loadResultBuffers(); } catch (e) { showError(e); } }
  if (!S.render) { stopPlayback(); player.pcm = {}; player.key = null; }

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
