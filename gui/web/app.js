/* Claude Modbus - logika GUI. Semua akses Modbus lewat window.pywebview.api */
"use strict";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, txt) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt !== undefined) n.textContent = txt;
  return n;
};

let api = null;
const S = { device: null, devices: [], points: [], liveTimer: null, hist: null,
            polling: false, connected: false, booted: false };

function toast(msg, kind = "") {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast " + kind;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 2800);
}
const status = (m) => { $("status").textContent = m; };
const fmt = (v) => (typeof v === "number" ? (Number.isInteger(v) ? v : v.toFixed(3))
                    : v === true ? "ON" : v === false ? "OFF" : v ?? "-");

function bindSwitch(id, initial, onChange) {
  const b = $(id);
  b.classList.toggle("on", !!initial);
  b.onclick = () => {
    const on = !b.classList.contains("on");
    b.classList.toggle("on", on);
    if (onChange) onChange(on);
  };
  return { get: () => b.classList.contains("on"),
           set: (v) => b.classList.toggle("on", !!v) };
}

/* ══════════ boot ══════════ */
window.addEventListener("pywebviewready", init);

async function init() {
  api = window.pywebview.api;
  const b = await api.boot();
  S.booted = true;
  setTheme(b.prefs?.theme || "light");
  $("dbPath").textContent = "db: " + b.db_path.split(/[\\/]/).slice(-2).join("/");

  const dt = $("np_type");
  for (const t of ["uint16", "int16", "uint32", "int32", "uint64", "int64",
                   "float32", "float64", "string"]) {
    dt.appendChild(new Option(t, t));
  }

  renderDevices(b.devices);
  wire();
  setInterval(tick, 1000);
  refreshMcp();
}

function setTheme(mode) {
  document.documentElement.dataset.theme = mode;
  $("themeBtn").innerHTML = mode === "dark" ? "&#9681;" : "&#9680;";
  if (api) api.set_theme(mode);
}

function wire() {
  document.querySelectorAll(".nav-item").forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll(".nav-item").forEach(b => b.classList.remove("is-active"));
      document.querySelectorAll(".page").forEach(p => p.classList.remove("is-active"));
      btn.classList.add("is-active");
      $("page-" + btn.dataset.page).classList.add("is-active");
      if (btn.dataset.page === "history") loadHistPoints();
      if (btn.dataset.page === "mcp") refreshLog();
    };
  });

  $("themeBtn").onclick = () =>
    setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  document.addEventListener("keydown", e => {
    if (e.ctrlKey && e.key.toLowerCase() === "d") {
      e.preventDefault();
      setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
    }
  });

  $("deviceSel").onchange = () => selectDevice($("deviceSel").value);
  $("connectBtn").onclick = toggleConnect;
  $("pollBtn").onclick = togglePoll;
  S.allowWrite = bindSwitch("allowWrite", false, async (on) => {
    const r = await api.set_allow_write(S.device, on);
    if (!r.ok) { toast(r.error, "err"); S.allowWrite.set(!on); return; }
    toast(on ? "Perintah tulis diizinkan untuk perangkat ini"
             : "Perintah tulis dikunci", on ? "" : "ok");
    renderLive();
  });
  S.npWrite = bindSwitch("np_write", false);

  $("readNow").onclick = () => readAll(false);
  $("readStore").onclick = () => readAll(true);

  $("reloadDev").onclick = async () => renderDevices((await api.devices()).devices);
  $("addDev").onclick = addDevice;
  $("addPoint").onclick = addPoint;

  $("simBtn").onclick = async () => {
    const running = $("simBtn").dataset.on === "1";
    const r = await api.simulator(running ? "stop" : "start");
    if (!r.ok) return toast(r.error, "err");
    setSim(r.running, r.url);
    toast(r.running ? "Simulator jalan di " + r.url : "Simulator dimatikan");
  };

  $("histLoad").onclick = loadHistory;
  $("histPoint").onchange = loadHistory;
  $("histExport").onclick = async () => {
    const r = await api.export_csv(S.device, histPointName(), +$("histMin").value || 30);
    if (r.ok) toast(`${r.rows} baris diekspor`, "ok");
    else if (!r.cancel) toast(r.error, "err");
  };
  $("histClear").onclick = async () => {
    const r = await api.clear_history(S.device);
    if (r.ok) { toast(`${r.deleted} baris riwayat dihapus`); loadHistory(); }
  };

  $("ex_read").onclick = async () => {
    const r = await api.read_raw(S.device, $("ex_table").value,
                                 +$("ex_addr").value, +$("ex_count").value);
    $("ex_out").textContent = r.ok
      ? r.values.map((v, i) => `${(+$("ex_addr").value + i).toString().padStart(5)}  ` +
          `${String(v).padStart(7)}  ${r.hex[i] ?? ""}`).join("\n")
      : "GAGAL: " + r.error;
  };
  $("ex_write").onclick = async () => {
    const r = await api.write_raw(S.device, $("ex_table").value,
                                  +$("ex_addr").value, $("ex_values").value);
    if (!r.ok) return toast(r.error, "err");
    $("ex_out").textContent = `ditulis ${JSON.stringify(r.written)}\n` +
                              `dibaca balik ${JSON.stringify(r.readback)}`;
    toast("Nilai ditulis", "ok");
  };
  $("su_go").onclick = async () => {
    $("su_out").textContent = "memindai...";
    const r = await api.scan_units(S.device, +$("su_start").value, +$("su_end").value);
    $("su_out").textContent = r.ok
      ? `menjawab: ${r.responding.join(", ") || "tidak ada"}\n\n` +
        r.result.map(x => `${String(x.unit_id).padStart(3)}  ${x.responded ? "OK " : "-  "} ${x.detail.slice(0, 48)}`).join("\n")
      : "GAGAL: " + r.error;
  };
  $("sr_go").onclick = async () => {
    $("sr_out").textContent = "menyapu...";
    const r = await api.scan_registers(S.device, $("sr_table").value,
                                       +$("sr_start").value, +$("sr_end").value);
    $("sr_out").textContent = r.ok
      ? r.blocks.map(b => b.ok
          ? `${String(b.address).padStart(5)}..${b.address + b.count - 1}  ${JSON.stringify(b.values)}`
          : `${String(b.address).padStart(5)}..${b.address + b.count - 1}  GAGAL ${b.error.slice(0, 60)}`).join("\n")
      : "GAGAL: " + r.error;
  };

  $("copyCli").onclick = () => copy($("mcpCli").textContent);
  $("copyJson").onclick = () => copy($("mcpJson").textContent);
}

const copy = (t) => navigator.clipboard.writeText(t).then(() => toast("Disalin"));

/* ══════════ perangkat ══════════ */
function renderDevices(devices) {
  S.devices = devices;
  const sel = $("deviceSel");
  const prev = S.device;
  sel.innerHTML = "";
  for (const d of devices) sel.appendChild(new Option(`${d.name}  (${d.id})`, d.id));
  $("devCount").textContent = devices.length;

  const body = $("devBody");
  body.innerHTML = "";
  for (const d of devices) {
    const tr = el("tr");
    const cells = [d.id, d.name, d.endpoint, d.unit_id, d.points];
    for (const c of cells) tr.appendChild(el("td", "", String(c)));
    const w = el("td", "c");
    w.appendChild(el("span", "chip " + (d.allow_write ? "warn" : ""),
                     d.allow_write ? "boleh" : "kunci"));
    tr.appendChild(w);
    const st = el("td", "c");
    st.appendChild(el("span", "chip " + (d.connected ? "ok" : ""),
                      d.connected ? "terhubung" : "mati"));
    tr.appendChild(st);
    const del = el("td", "c");
    const b = el("button", "btn tonal-danger sm", "Hapus");
    b.onclick = async (e) => {
      e.stopPropagation();
      const r = await api.remove_device(d.id);
      if (r.ok) { toast(`Perangkat ${d.id} dihapus`); renderDevices((await api.devices()).devices); }
      else toast(r.error, "err");
    };
    del.appendChild(b);
    tr.appendChild(del);
    tr.onclick = () => { sel.value = d.id; selectDevice(d.id); };
    body.appendChild(tr);
  }

  if (devices.length) {
    const pick = devices.some(d => d.id === prev) ? prev : devices[0].id;
    sel.value = pick;
    selectDevice(pick);
  }
}

async function selectDevice(id) {
  S.device = id;
  $("npTarget").textContent = id || "-";
  const r = await api.describe(id);
  if (!r.ok) return toast(r.error, "err");
  S.points = r.points;
  S.connected = r.connected;
  S.polling = r.polling;
  $("endpoint").textContent = r.endpoint;
  $("liveTitle").textContent = r.device.name;
  $("pointCount").textContent = r.points.length;
  S.allowWrite.set(r.device.allow_write);
  $("pollSec").value = r.device.poll_interval;
  setConn(r.connected);
  setPoll(r.polling);
  renderLive();
}

function setConn(on) {
  S.connected = on;
  $("connChip").textContent = on ? "terhubung" : "terputus";
  $("connChip").className = "chip " + (on ? "ok" : "");
  $("connectBtn").textContent = on ? "Putuskan" : "Hubungkan";
  $("connectBtn").className = "btn block " + (on ? "btn-danger" : "btn-ok");
}

function setPoll(on) {
  S.polling = on;
  $("pollBtn").textContent = on ? "Hentikan Polling" : "Mulai Polling";
  $("pollBtn").className = "btn block " + (on ? "tonal-danger" : "tonal");
  if (on && !S.liveTimer) S.liveTimer = setInterval(pullLive, 900);
  if (!on && S.liveTimer) { clearInterval(S.liveTimer); S.liveTimer = null; }
}

function setSim(running, url) {
  $("simBtn").dataset.on = running ? "1" : "0";
  $("simBtn").textContent = running ? "Matikan" : "Nyalakan";
  $("simUrl").textContent = running ? url : "mati";
}

async function toggleConnect() {
  const r = S.connected ? await api.disconnect(S.device) : await api.connect(S.device);
  if (!r.ok) return toast(r.error, "err");
  setConn(r.connected);
  status(r.connected ? `Terhubung ke ${S.device}` : `Koneksi ${S.device} ditutup`);
  if (!r.connected) setPoll(false);
  renderDevices((await api.devices()).devices);
}

async function togglePoll() {
  const r = S.polling ? await api.stop_polling(S.device)
                      : await api.start_polling(S.device, +$("pollSec").value || 2);
  if (!r.ok) return toast(r.error, "err");
  setPoll(!S.polling);
  status(S.polling ? `Polling ${S.device} tiap ${r.interval}s` : "Polling berhenti");
}

/* ══════════ live ══════════ */
async function readAll(store) {
  const r = await api.read_all(S.device, store);
  if (!r.ok) return toast(r.error, "err");
  applyReadings(r.readings, r.time);
  if (store) toast("Pembacaan disimpan ke riwayat", "ok");
}

async function pullLive() {
  const r = await api.live(S.device);
  if (r.ok) applyReadings(r.readings, r.time);
}

function applyReadings(readings, t) {
  const byName = Object.fromEntries(readings.map(r => [r.point, r]));
  S.points = S.points.map(p => ({ ...p, ...(byName[p.name] || {}) }));
  $("liveTime").textContent = t || "";
  const aktif = S.points.filter(p => p.alarm === "high" || p.alarm === "low");
  $("alarmChip").textContent = aktif.length ? `${aktif.length} alarm` : "";
  $("alarmChip").className = "chip " + (aktif.length ? "err" : "");
  renderLive();
}

function renderLive() {
  const body = $("liveBody");
  body.innerHTML = "";
  for (const p of S.points) {
    const tr = el("tr");
    const nm = el("td");
    nm.appendChild(el("b", "", p.name));
    if (p.description) nm.appendChild(el("div", "muted", p.description));
    tr.appendChild(nm);
    tr.appendChild(el("td", "", p.table));
    tr.appendChild(el("td", "r", String(p.address)));
    tr.appendChild(el("td", "", p.datatype + (p.scale !== 1 ? ` ×${p.scale}` : "")));

    const v = el("td", "r " + (p.alarm ? "alarm-" + p.alarm : ""));
    const val = el("b", "value-big", fmt(p.value));
    v.appendChild(val);
    if (p.unit) v.appendChild(el("span", "unit", p.unit));
    tr.appendChild(v);

    const st = el("td", "c");
    if (p.quality === "error") st.appendChild(el("span", "chip err", "galat"));
    else if (p.alarm === "high") st.appendChild(el("span", "chip err", "tinggi"));
    else if (p.alarm === "low") st.appendChild(el("span", "chip err", "rendah"));
    else if (p.alarm === "ok") st.appendChild(el("span", "chip ok", "normal"));
    tr.appendChild(st);

    const w = el("td", "r");
    if (p.writable) {
      const box = el("div", "write-cell");
      if (p.table === "coil") {
        for (const [label, val2, cls] of [["ON", 1, "tonal-ok"], ["OFF", 0, "ghost"]]) {
          const b = el("button", `btn ${cls} sm`, label);
          b.onclick = () => doWrite(p.name, val2);
          box.appendChild(b);
        }
      } else {
        const inp = el("input", "mono");
        inp.value = typeof p.value === "number" ? p.value : "";
        inp.onkeydown = (e) => { if (e.key === "Enter") doWrite(p.name, inp.value); };
        const b = el("button", "btn tonal sm", "Tulis");
        b.onclick = () => doWrite(p.name, inp.value);
        box.appendChild(inp);
        box.appendChild(b);
      }
      w.appendChild(box);
    } else {
      w.appendChild(el("span", "muted", "-"));
    }
    tr.appendChild(w);
    body.appendChild(tr);
  }
}

async function doWrite(point, value) {
  const r = await api.write_point(S.device, point, value);
  if (!r.ok) return toast(r.error, "err");
  toast(`${point} = ${fmt(r.readback.value)}`, "ok");
  applyReadings([r.readback]);
}

/* ══════════ tambah perangkat / point ══════════ */
async function addDevice() {
  const data = {
    id: $("nd_id").value.trim(), name: $("nd_name").value.trim(),
    transport: $("nd_transport").value, host: $("nd_host").value.trim(),
    port: $("nd_port").value, serial_port: $("nd_serial").value.trim(),
    baudrate: $("nd_baud").value, parity: $("nd_parity").value,
    unit_id: $("nd_unit").value, allow_write: false,
  };
  if (!data.id) return toast("ID perangkat wajib diisi", "err");
  const r = await api.add_device(data);
  if (!r.ok) return toast(r.error, "err");
  toast(`Perangkat ${r.device} dibuat`, "ok");
  $("nd_id").value = $("nd_name").value = "";
  renderDevices((await api.devices()).devices);
}

async function addPoint() {
  if (!S.device) return toast("Pilih perangkat dulu", "err");
  const data = {
    name: $("np_name").value.trim(), table: $("np_table").value,
    address: $("np_addr").value, datatype: $("np_type").value,
    scale: $("np_scale").value, offset: $("np_offset").value,
    unit: $("np_unit").value.trim(), writable: S.npWrite.get(),
    alarm_low: $("np_low").value, alarm_high: $("np_high").value,
  };
  if (!data.name) return toast("Nama point wajib diisi", "err");
  const r = await api.add_point(S.device, data);
  if (!r.ok) return toast(r.error, "err");
  toast(`Point ${r.point} ditambahkan`, "ok");
  $("np_name").value = "";
  selectDevice(S.device);
  renderDevices((await api.devices()).devices);
}

/* ══════════ riwayat ══════════ */
function histPointName() {
  return $("histPoint").value || "";
}

async function loadHistPoints() {
  const sel = $("histPoint");
  const prev = sel.value;
  sel.innerHTML = "";
  for (const p of S.points) sel.appendChild(new Option(p.name, p.name));
  if (prev && S.points.some(p => p.name === prev)) sel.value = prev;
  loadHistory();
}

async function loadHistory() {
  if (!S.device || !histPointName()) return;
  const r = await api.history(S.device, histPointName(), +$("histMin").value || 30);
  if (!r.ok) return toast(r.error, "err");
  S.hist = r;
  const pt = S.points.find(p => p.name === histPointName()) || {};
  $("histTitle").textContent = `${histPointName()} ${pt.unit ? "(" + pt.unit + ")" : ""}`;
  $("histChip").textContent = `${r.rows.length} sampel`;
  drawChart(r.rows, pt.unit || "");
  drawStats(r.stats, pt.unit || "");
}

function drawStats(s, unit) {
  const tiles = $("statTiles");
  tiles.innerHTML = "";
  if (!s || !s.numeric_samples) {
    tiles.appendChild(el("p", "muted",
      "Belum ada data. Nyalakan polling di panel kiri, tunggu beberapa siklus, lalu muat lagi."));
    $("statMore").innerHTML = "";
    return;
  }
  const items = [["Sampel", s.numeric_samples, ""], ["Minimum", s.min, unit],
                 ["Maksimum", s.max, unit], ["Rata-rata", s.mean, unit],
                 ["Terakhir", s.last, unit]];
  for (const [label, val, u] of items) {
    const t = el("div", "tile");
    t.appendChild(el("b", "", fmt(val) + (u ? " " + u : "")));
    t.appendChild(el("span", "", label));
    tiles.appendChild(t);
  }
  const more = $("statMore");
  more.innerHTML = "";
  const rows = [["Median", fmt(s.median) + " " + unit],
                ["Simpangan baku", fmt(s.stdev) + " " + unit],
                ["Tren", `${s.direction} (${fmt(s.trend_per_minute)} ${unit}/menit)`],
                ["Rentang waktu", `${fmt(s.span_seconds)} detik`],
                ["Sampel bermasalah", s.bad_samples]];
  for (const [k, v] of rows) {
    const d = el("div", "kv");
    d.appendChild(el("span", "", k));
    d.appendChild(el("b", "", String(v)));
    more.appendChild(d);
  }
}

function drawChart(rows, unit) {
  const cv = $("chart");
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = 260;
  cv.width = w * dpr; cv.height = h * dpr;
  const g = cv.getContext("2d");
  g.scale(dpr, dpr);
  const css = getComputedStyle(document.documentElement);
  const col = (n) => css.getPropertyValue(n).trim();
  g.clearRect(0, 0, w, h);

  const pts = rows.filter(r => typeof r.value === "number");
  if (pts.length < 2) {
    g.fillStyle = col("--faint");
    g.font = "13px sans-serif";
    g.fillText("Belum cukup data untuk digambar.", 14, h / 2);
    return;
  }
  const xs = pts.map(p => p.ts), ys = pts.map(p => p.value);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  if (y1 - y0 < 1e-9) { y0 -= 1; y1 += 1; }
  const pad = (y1 - y0) * 0.12;
  y0 -= pad; y1 += pad;
  const L = 56, R = 12, T = 12, B = 26;
  const px = (t) => L + (t - x0) / Math.max(1e-9, x1 - x0) * (w - L - R);
  const py = (v) => T + (1 - (v - y0) / (y1 - y0)) * (h - T - B);

  g.strokeStyle = col("--border");
  g.fillStyle = col("--faint");
  g.font = "10px ui-monospace, monospace";
  g.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const v = y0 + (y1 - y0) * i / 4, y = py(v);
    g.beginPath(); g.moveTo(L, y); g.lineTo(w - R, y); g.stroke();
    g.fillText(v.toFixed(2), 6, y + 3);
  }
  const tl = (t) => new Date(t * 1000).toLocaleTimeString("id-ID");
  g.fillText(tl(x0), L, h - 8);
  g.fillText(tl(x1), w - R - 52, h - 8);

  const grad = g.createLinearGradient(0, T, 0, h - B);
  grad.addColorStop(0, col("--accent") + "44");
  grad.addColorStop(1, col("--accent") + "05");
  g.beginPath();
  g.moveTo(px(xs[0]), py(ys[0]));
  pts.forEach(p => g.lineTo(px(p.ts), py(p.value)));
  g.lineTo(px(xs[xs.length - 1]), h - B);
  g.lineTo(px(xs[0]), h - B);
  g.closePath();
  g.fillStyle = grad;
  g.fill();

  g.beginPath();
  pts.forEach((p, i) => i ? g.lineTo(px(p.ts), py(p.value)) : g.moveTo(px(p.ts), py(p.value)));
  g.strokeStyle = col("--accent");
  g.lineWidth = 2;
  g.lineJoin = "round";
  g.stroke();

  const last = pts[pts.length - 1];
  g.beginPath();
  g.arc(px(last.ts), py(last.value), 4, 0, Math.PI * 2);
  g.fillStyle = col("--accent");
  g.fill();
  g.fillStyle = col("--text");
  g.font = "600 12px ui-monospace, monospace";
  g.fillText(`${fmt(last.value)} ${unit}`, Math.min(px(last.ts) + 8, w - 90),
             py(last.value) - 8);
}

/* ══════════ mcp & log ══════════ */
async function refreshMcp() {
  const r = await api.mcp_config();
  $("mcpCli").textContent = r.cli;
  $("mcpJson").textContent = r.json;
}

async function refreshLog() {
  const r = await api.log();
  const box = $("engLog");
  box.innerHTML = "";
  for (const l of r.lines) {
    const t = new Date(l.ts * 1000).toLocaleTimeString("id-ID");
    box.appendChild(el("div", "", `${t}  ${l.text}`));
  }
}

function tick() {
  $("clock").textContent = new Date().toLocaleTimeString("id-ID");
  if ($("page-mcp").classList.contains("is-active")) refreshLog();
}
