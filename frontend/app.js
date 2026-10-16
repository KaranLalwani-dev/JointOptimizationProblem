"use strict";
// Draws the plan, the hubs, the nodes and the cables from the JSON the backend sends (all numbers in metres, y up).
const SVGNS = "http://www.w3.org/2000/svg";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const S = { view: null, id: null, vb: null, home: null, busy: false };

function el(tag, attrs, parent, text) {
  const e = document.createElementNS(SVGNS, tag);
  for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
  if (text !== undefined && text !== null) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* keep status text */ }
    throw new Error(msg);
  }
  return r.json();
}

function banner(msg) {
  const b = $("#banner");
  b.hidden = !msg;
  b.textContent = msg || "";
}

function mix(hex, k) {            // colour mixed with white, k = share of white
  const n = parseInt(hex.slice(1), 16), c = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  return "rgb(" + c.map((v) => Math.round(v + (255 - v) * k)).join(",") + ")";
}

// ------------------------------------------------------------------ drawing
function drawScene(sc) {
  const box = $("#canvas");
  box.querySelectorAll("svg").forEach((n) => n.remove());
  $("#empty").style.display = "none";
  const r = buildSvg(sc, false);
  S.home = r.home;
  S.vb = { ...r.home };
  setVB(r.svg);
  box.appendChild(r.svg);
  wireZoom(r.svg);
}

function buildSvg(sc, thumb) {
  const [x0, y0, x1, y1] = sc.bounds, pad = 0.6;
  const W = x1 - x0 + 2 * pad, H = y1 - y0 + 2 * pad, top = y1 + pad;
  const X = (x) => x, Y = (y) => top - y;
  const span = Math.max(W, H), fs = Math.max(0.17, 0.0125 * span), r = thumb ? sc.r_node * 1.8 : sc.r_node;
  const svg = el("svg", { xmlns: SVGNS, preserveAspectRatio: "xMidYMid meet" });
  const home = { x: x0 - pad, y: 0, w: W, h: H };
  svg.setAttribute("viewBox", [home.x, home.y, home.w, home.h].join(" "));
  el("rect", { x: x0 - pad, y: 0, width: W, height: H, fill: "#ffffff" }, svg);
  const path = (polys) => polys.map((poly) => poly.map((ring) => "M" + ring.map((p) => X(p[0]) + "," + Y(p[1])).join("L") + "Z").join("")).join("");
  const line = (w) => ({ "vector-effect": "non-scaling-stroke", "stroke-width": thumb ? w * 0.6 : w });

  sc.balconies.forEach((b) => {
    el("path", { d: path(b.polys), fill: "#f1f1f1", stroke: "#777", "stroke-dasharray": "5 4", "fill-rule": "evenodd", ...line(0.9) }, svg);
    if (!thumb) twoLines(svg, b.x, Y(b.y), "Balcony", "outside", fs * 0.9, "#777", "middle");
  });
  sc.excluded.forEach((b) => {
    el("path", { d: path(b.polys), fill: "#dcdfe3", stroke: "#555", "fill-rule": "evenodd", ...line(0.8) }, svg);
    if (!thumb) twoLines(svg, b.x, Y(b.y), b.label, "no node", fs * 0.95, "#555", "middle");
  });
  sc.rooms.forEach((rm) => {
    const col = rm.hub === null ? "#ffffff" : mix(sc.hubs[rm.hub].color, 0.8);
    const p = el("path", { d: path(rm.polys), fill: col, stroke: "#555", "fill-rule": "evenodd", ...line(0.8) }, svg);
    el("title", {}, p, rm.name + ", " + rm.area.toFixed(1) + " m²" + (rm.hub === null ? "" : ", hub H" + (rm.hub + 1)));
  });
  el("path", { d: path(sc.layers.wall), fill: "#4a4f57", "fill-rule": "evenodd" }, svg);
  sc.layers.door.length && el("path", { d: path(sc.layers.door), fill: "#fbf3dc", stroke: "#b08d57", "fill-rule": "evenodd", ...line(0.7) }, svg);
  sc.layers.front_door.length && el("path", { d: path(sc.layers.front_door), fill: "#a86f1d", "fill-rule": "evenodd" }, svg);
  sc.layers.window.length && el("path", { d: path(sc.layers.window), fill: "#79b6f2", stroke: "#2a6fb8", "fill-rule": "evenodd", ...line(0.7) }, svg);
  if (!thumb) sc.rooms.forEach((rm) => {
    const a = rm.label.ha === "left" ? "start" : rm.label.ha === "right" ? "end" : "middle";
    twoLines(svg, rm.label.x, Y(rm.label.y), rm.name, rm.area.toFixed(1) + " m²", fs, "#333", a, rm.label.va);
  });

  // cables
  sc.hubs.forEach((h) => h.legs.forEach((lg) => {
    el("polyline", { points: lg.display.map((p) => X(p[0]) + "," + Y(p[1])).join(" "), fill: "none", stroke: h.color, "stroke-linejoin": "round", "stroke-linecap": "round", ...line(2.4) }, svg);
  }));
  sc.hubs.forEach((h) => h.legs.forEach((lg) => {
    if (!lg.arrow) return;
    const a = lg.arrow, ux = a.dx, uy = a.dy, nx = -uy, ny = ux, L = 0.24, Wd = 0.095;
    const pts = [[a.x, a.y], [a.x - ux * L + nx * Wd, a.y - uy * L + ny * Wd], [a.x - ux * L - nx * Wd, a.y - uy * L - ny * Wd]];
    el("polygon", { points: pts.map((p) => X(p[0]) + "," + Y(p[1])).join(" "), fill: h.color }, svg);
  }));
  sc.hubs.forEach((h) => h.legs.forEach((lg) => {
    if (!lg.mid || thumb) return;
    const g = el("g", {}, svg);
    el("circle", { cx: lg.mid[0], cy: Y(lg.mid[1]), r: r * 0.8, fill: "#fff", stroke: h.color, ...line(1.8) }, g);
    el("text", { x: lg.mid[0], y: Y(lg.mid[1]) + r * 0.36, "text-anchor": "middle", "font-size": r * 1.05, fill: "#222", "font-weight": 600 }, g, lg.order);
    el("title", {}, g, lg.from + " → " + lg.to + ", " + lg.length.toFixed(2) + " m");
  }));
  // nodes and hubs
  sc.hubs.forEach((h) => {
    h.devices.forEach((d) => {
      const g = el("g", {}, svg);
      el("circle", { cx: d.x, cy: Y(d.y), r: r, fill: h.color, stroke: "#fff", ...line(1.3) }, g);
      if (!thumb) el("text", { x: d.x, y: Y(d.y) + r * 0.38, "text-anchor": "middle", "font-size": r * (d.name.length > 2 ? 0.95 : 1.15), fill: "#fff", "font-weight": 700 }, g, d.name);
      el("title", {}, g, d.name + " in " + d.room);
    });
    const g = el("g", {}, svg);
    el("rect", { x: h.x - r * 1.05, y: Y(h.y) - r * 1.05, width: r * 2.1, height: r * 2.1, fill: h.color, stroke: "#1b1b1b", ...line(1.3) }, g);
    if (!thumb) el("text", { x: h.x, y: Y(h.y) + r * 0.38, "text-anchor": "middle", "font-size": r * 1.15, fill: "#fff", "font-weight": 700 }, g, h.name);
    el("title", {}, g, h.name + " (" + h.room_names.join(", ") + ")");
  });
  sc.hubs.forEach((h) => {
    const c = h.callout;
    if (!c || thumb) return;
    const f = fs * 0.85, lines = [c.text].concat(c.sub.split(", "));
    const w = Math.max(...lines.map((s) => s.length)) * f * 0.55 + 0.25, hh = lines.length * f * 1.25 + 0.16;
    el("line", { x1: c.tx, y1: Y(c.ty), x2: c.x, y2: Y(c.y), stroke: h.color, ...line(1.3) }, svg);
    el("rect", { x: c.x - w / 2, y: Y(c.y) - hh / 2, width: w, height: hh, rx: 0.06, fill: "#fff", stroke: h.color, ...line(1.4) }, svg);
    lines.forEach((s, i) => el("text", { x: c.x, y: Y(c.y) - hh / 2 + 0.08 + f * 1.1 + i * f * 1.25, "text-anchor": "middle", "font-size": f, fill: "#222", "font-weight": i === 0 ? 700 : 400 }, svg, s));
  });
  svg.setAttribute("style", "width:100%;height:100%;display:block");
  return { svg, home };
}

function twoLines(svg, x, y, a, b, size, color, anchor, va) {
  // two text lines anchored at (x, y); va = top | bottom | center (the block hangs below, stands above, or is centred on y)
  const gap = size * 1.18, h = gap * 2;
  let y1;
  if (va === "top") y1 = y + size * 0.95;
  else if (va === "bottom") y1 = y - gap;
  else y1 = y - h / 2 + size * 0.95;
  const t = el("text", { "text-anchor": anchor, "font-size": size, fill: color, "pointer-events": "none" }, svg);
  el("tspan", { x: x, y: y1 }, t, a);
  el("tspan", { x: x, y: y1 + gap }, t, b);
}

function setVB(svg) {
  const v = S.vb;
  svg.setAttribute("viewBox", [v.x, v.y, v.w, v.h].join(" "));
}

function wireZoom(svg) {
  let drag = null;
  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    const rect = svg.getBoundingClientRect(), k = e.deltaY < 0 ? 0.85 : 1 / 0.85;
    const fit = Math.min(rect.width / S.vb.w, rect.height / S.vb.h);
    // the point of the drawing under the mouse stays under the mouse
    const px = (e.clientX - rect.left - (rect.width - S.vb.w * fit) / 2) / fit + S.vb.x, py = (e.clientY - rect.top - (rect.height - S.vb.h * fit) / 2) / fit + S.vb.y;
    const nw = Math.min(S.home.w * 4, Math.max(S.home.w / 40, S.vb.w * k)), nh = nw * (S.vb.h / S.vb.w);
    S.vb = { x: px - (px - S.vb.x) * (nw / S.vb.w), y: py - (py - S.vb.y) * (nh / S.vb.h), w: nw, h: nh };
    setVB(svg);
  }, { passive: false });
  svg.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, vb: { ...S.vb } }; svg.setPointerCapture(e.pointerId); });
  svg.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const rect = svg.getBoundingClientRect(), fit = Math.min(rect.width / drag.vb.w, rect.height / drag.vb.h);
    S.vb = { ...drag.vb, x: drag.vb.x - (e.clientX - drag.x) / fit, y: drag.vb.y - (e.clientY - drag.y) / fit };
    setVB(svg);
  });
  svg.addEventListener("pointerup", () => { drag = null; });
  svg.addEventListener("dblclick", () => { S.vb = { ...S.home }; setVB(svg); });
}

// ------------------------------------------------------------------ side panels and headline
function row(a, b) { return '<div class="row"><span>' + esc(a) + "</span><span>" + b + "</span></div>"; }

function drawPanels(view) {
  const p = view.panel, sc = view.scene, side = $("#info");
  const h = $("#headline");
  if (p.solved) {
    const ok = p.valid && (!view.file_check || view.file_check.ok);
    h.innerHTML = "<h1>Plan " + esc(sc.plan_id) + ": " + p.hubs + " hub" + (p.hubs === 1 ? "" : "s") + ", J = " + p.J.toFixed(2) + "</h1>" +
      '<div class="sub">L ' + p.L.toFixed(2) + " m + 5 × X (" + p.X + ")" + (p.X_touch > p.X ? " (+" + (p.X_touch - p.X) + " touches)" : "") + " + O " + p.O.toFixed(2) + " m + 0.5 × B (" + p.B + ") &nbsp;|&nbsp; <b class=\"" + (ok ? "ok" : "bad") + '">' + (ok ? "VALID" : "INVALID") + "</b></div>";
  } else {
    h.innerHTML = "<h1>Plan " + esc(sc.plan_id) + "</h1><div class=\"sub\">" + (p.error ? '<b class="bad">' + esc(p.error) + "</b>" : "not solved yet — press Solve") + "</div>";
  }
  let html = '<div class="card"><h3>Plan</h3>' + row("Split", esc(view.split || "unknown")) + row("Eligible rooms", p.rooms) + row("Total area", p.area + " m²") +
    row("Hubs: lower bound", p.lower_bound) + row("Hubs: exact minimum (" + esc(p.link_set) + ")", p.exact_min === null ? "n/a" : p.exact_min);
  (p.flags || []).forEach((f) => { html += '<div class="warn">flag: ' + esc(f) + "</div>"; });
  html += "</div>";
  if (p.solved) {
    html += '<div class="card"><h3>Hubs</h3>';
    sc.hubs.forEach((hb) => {
      html += '<div class="hubrow"><div class="nm"><span class="sw" style="background:' + hb.color + '"></span>' + hb.name + " &nbsp;" + esc(hb.room_names.join(", ")) + "</div>" +
        '<div class="det">' + (hb.area === null ? "" : hb.area.toFixed(1) + " m² | ") + hb.devices.length + " node" + (hb.devices.length === 1 ? "" : "s") + (hb.length === null ? "" : " | " + hb.length.toFixed(1) + " m | " + hb.bends + " bends") + "</div></div>";
    });
    html += "</div>";
    html += '<div class="card"><h3>Totals and validation</h3>' + row("Total cable L", p.L.toFixed(2) + " m") + row("Crossings X", p.X + (p.X_touch > p.X ? " (+" + (p.X_touch - p.X) + " touches)" : "")) +
      row("Overlap O", p.O.toFixed(2) + " m") + row("Bends B", p.B) + row("Objective J", "<b>" + p.J.toFixed(2) + "</b>") +
      (p.area_gap !== null && sc.hubs.length > 1 ? row("Assigned-area gap", p.area_gap.toFixed(1) + " m²") : "") + row("Solve time", p.seconds + " s");
    p.checks.forEach((c) => { html += '<div class="chk ' + (c.ok ? "ok" : "bad") + '">' + esc(c.text) + "</div>"; });
    if (view.file_check) html += '<div class="chk ' + (view.file_check.ok ? "ok" : "bad") + '">JSON file re-checked from its own contents' + (view.file_check.ok ? "" : ": " + esc(view.file_check.problems.join("; "))) + "</div>";
    html += "</div>";
    if (p.warnings.length || p.errors.length || p.retry) {
      html += '<div class="card"><h3>Flags and warnings</h3>';
      p.warnings.forEach((w) => { html += '<div class="warn">warning: ' + esc(w) + "</div>"; });
      p.errors.forEach((w) => { html += '<div class="err">ERROR ' + esc(w) + "</div>"; });
      if (p.retry) html += '<div class="warn">solved after retry: ' + esc(p.retry) + "</div>";
      html += "</div>";
    }
  }
  html += '<div class="card legend"><h3>How to read</h3><div>■ Hub (one colour per zone)</div><div>● Node (one in each eligible room)</div><div>→ Cable; numbers give the visiting order</div><div>○ Last number is the return to the hub</div><div>Grey room: excluded, no node. Pale gaps in walls are doors</div></div>';
  side.innerHTML = html;
}

// ------------------------------------------------------------------ actions
function setBusy(b, label) {
  S.busy = b;
  const solve = $("#btnSolve");
  solve.disabled = b || S.id === null;
  solve.innerHTML = b && label ? '<span class="spinner"></span>' + label : "Solve";
  $("#btnLoad").disabled = b;
  $("#btnRandom").disabled = b;
}

function render(view) {
  S.view = view;
  B.views.set(String(view.scene.plan_id), view);
  drawScene(view.scene);
  drawPanels(view);
  const solved = !!view.design;
  $("#btnJson").disabled = !solved;
  $("#btnSvg").disabled = false;
  $("#btnPng").disabled = false;
}

async function loadPlan(id) {
  id = String(id).trim();
  if (!id) return;
  banner("");
  setBusy(true);
  try {
    const view = await api("/api/plans/" + encodeURIComponent(id) + "?link_set=" + $("#linkSet").value);
    S.id = view.scene.plan_id;
    $("#planId").value = S.id;
    render(view);
    markSelected();
  } catch (e) { banner("Could not load plan " + id + ": " + e.message); }
  setBusy(false);
}

async function solvePlan(force) {
  if (S.id === null) return;
  banner("");
  const secs = Math.max(1, Math.min(300, parseFloat($("#seconds").value) || 30));
  setBusy(true, "Solving…");
  try {
    const view = await api("/api/plans/" + encodeURIComponent(S.id) + "/solve", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ seconds: secs, link_set: $("#linkSet").value, force: !!force }),
    });
    render(view);
    await loadList();
  } catch (e) { banner("Solving plan " + S.id + " failed: " + e.message); }
  setBusy(false);
}

function download(name, blob) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}

function svgText() {
  const svg = $("#canvas svg");
  const copy = svg.cloneNode(true);
  const h = S.home;
  copy.setAttribute("viewBox", [h.x, h.y, h.w, h.h].join(" "));
  copy.setAttribute("width", Math.round(h.w * 80));
  copy.setAttribute("height", Math.round(h.h * 80));
  copy.setAttribute("font-family", "Arial, Helvetica, sans-serif");
  return new XMLSerializer().serializeToString(copy);
}

function exportSvg() { if (S.view) download("plan_" + S.id + ".svg", new Blob([svgText()], { type: "image/svg+xml" })); }

function exportPng() {
  if (!S.view) return;
  const img = new Image(), h = S.home, w = Math.round(h.w * 120), ht = Math.round(h.h * 120);
  img.onload = () => {
    const c = document.createElement("canvas");
    c.width = w; c.height = ht;
    const g = c.getContext("2d");
    g.fillStyle = "#fff"; g.fillRect(0, 0, w, ht); g.drawImage(img, 0, 0, w, ht);
    c.toBlob((b) => download("plan_" + S.id + ".png", b));
  };
  img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svgText().replace(/width="\d+" height="\d+"/, 'width="' + w + '" height="' + ht + '"'));
}

// ------------------------------------------------------------------ plan browser (pages of plans with a small picture each)
const B = { page: 0, size: 3, total: 0, q: "", views: new Map() };
const enc = encodeURIComponent;

async function loadList() {
  const url = "/api/plans?split=" + enc($("#split").value) + "&q=" + enc(B.q) + "&limit=" + B.size + "&offset=" + B.page * B.size + "&link_set=" + $("#linkSet").value;
  let r;
  try { r = await api(url); } catch (e) { banner(e.message); return; }
  B.total = r.total;
  const pages = Math.max(1, Math.ceil(B.total / B.size));
  if (B.page >= pages) { B.page = pages - 1; return loadList(); }
  drawBrowser(r.plans, pages);
}

function drawBrowser(items, pages) {
  const box = $("#browser");
  const sizes = [3, 6, 12, 24].map((n) => '<option value="' + n + '"' + (n === B.size ? " selected" : "") + ">" + n + "</option>").join("");
  box.innerHTML = "<h3>Plans (" + B.total + ")</h3>" +
    '<div class="pager"><button id="pgPrev" class="ghost"' + (B.page <= 0 ? " disabled" : "") + '>\u2039</button>page <input id="pgNum" size="3" value="' + (B.page + 1) + '"> of ' + pages +
    '<button id="pgNext" class="ghost"' + (B.page >= pages - 1 ? " disabled" : "") + '>\u203a</button><span class="grow"></span>per page <select id="pgSize">' + sizes + "</select></div>" +
    '<div class="pager"><input id="pgFind" placeholder="find id" size="9" value="' + esc(B.q) + '"><button id="pgFindBtn" class="ghost">Find</button></div><div class="cards" id="cards"></div>';
  $("#pgPrev").onclick = () => { B.page--; loadList(); };
  $("#pgNext").onclick = () => { B.page++; loadList(); };
  $("#pgNum").onchange = (e) => { B.page = Math.max(0, Math.min(pages - 1, (parseInt(e.target.value, 10) || 1) - 1)); loadList(); };
  $("#pgSize").onchange = (e) => { const first = B.page * B.size; B.size = parseInt(e.target.value, 10); B.page = Math.floor(first / B.size); loadList(); };
  const find = () => { B.q = $("#pgFind").value.trim(); B.page = 0; loadList(); };
  $("#pgFindBtn").onclick = find;
  $("#pgFind").addEventListener("keydown", (e) => { if (e.key === "Enter") find(); });
  const cards = $("#cards");
  if (!items.length) cards.innerHTML = '<div class="nolist">No plans here. Upload a plan list or a single plan, or change the split.</div>';
  items.forEach((it) => {
    const c = document.createElement("div");
    c.className = "pcard";
    c.dataset.id = String(it.id);
    const badge = it.solved ? '<span class="badge ' + (it.valid ? "ok" : "bad") + '">' + (it.valid ? "J " + it.J.toFixed(2) + " \u00b7 " + it.hubs + " hub" + (it.hubs === 1 ? "" : "s") : "invalid") + "</span>" : '<span class="badge">not solved</span>';
    c.innerHTML = '<div class="thumb">\u2026</div><div><div class="t1">Plan ' + esc(it.id) + '</div><div class="t2">' + esc(it.split) + "</div>" + badge + "</div>";
    c.onclick = () => { if (!S.busy) loadPlan(it.id); };
    cards.appendChild(c);
    thumbFor(it.id, c.querySelector(".thumb"));
  });
  markSelected();
}

async function thumbFor(id, target) {
  try {
    let view = B.views.get(String(id));
    if (!view) {
      view = await api("/api/plans/" + enc(id) + "?link_set=" + $("#linkSet").value);
      B.views.set(String(id), view);
    }
    target.textContent = "";
    target.appendChild(buildSvg(view.scene, true).svg);
  } catch (e) { target.textContent = "no picture"; target.title = e.message; }
}

function markSelected() {
  document.querySelectorAll(".pcard").forEach((c) => c.classList.toggle("sel", S.id !== null && c.dataset.id === String(S.id)));
}

async function refreshStatus() {
  const st = await api("/api/status");
  const sel = $("#split"), keep = sel.value;
  sel.innerHTML = '<option value="">all</option>';
  Object.keys(st.splits || {}).sort().forEach((k) => { const o = document.createElement("option"); o.value = k; o.textContent = k + " (" + st.splits[k] + ")"; sel.appendChild(o); });
  sel.value = keep;
  return st;
}

async function uploadFiles(fileList) {
  const files = Array.from(fileList || []);          // copy first: the input's own list is emptied right after the click handler returns
  if (!files.length) return;
  banner("");
  const plans = [];
  for (const f of files) {
    let j;
    try { j = JSON.parse(await f.text()); } catch (e) { banner(f.name + " is not a JSON file"); return; }
    plans.push(...(Array.isArray(j) ? j : j.plans ? j.plans : [j]));
  }
  setBusy(true);
  try {
    const r = await api("/api/upload", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(plans) });
    await refreshStatus();
    if (r.loaded.length) { $("#split").value = "uploaded"; B.page = 0; B.q = ""; for (const id of r.loaded) B.views.delete(String(id)); await loadList(); }
    if (r.errors.length) banner("Not loaded: " + r.errors.map((e) => "plan " + e.id + ": " + e.error).join(" | "));
    if (r.loaded.length) { setBusy(false); await loadPlan(r.loaded[0]); if (r.errors.length) banner("Loaded " + r.loaded.length + ". Not loaded: " + r.errors.map((e) => "plan " + e.id + ": " + e.error).join(" | ")); return; }
  } catch (e) { banner("Upload failed: " + e.message); }
  setBusy(false);
}

async function init() {
  $("#btnLoad").onclick = () => loadPlan($("#planId").value);
  $("#planId").addEventListener("keydown", (e) => { if (e.key === "Enter") loadPlan($("#planId").value); });
  $("#btnRandom").onclick = async () => {
    try { const r = await api("/api/random?split=" + encodeURIComponent($("#split").value)); loadPlan(r.id); } catch (e) { banner(e.message); }
  };
  $("#btnUploadList").onclick = () => $("#fileInput").click();
  $("#btnUploadOne").onclick = () => $("#fileInput").click();
  $("#split").onchange = () => { B.page = 0; loadList(); };
  $("#fileInput").onchange = (e) => { uploadFiles(e.target.files); e.target.value = ""; };
  const cv = $("#canvas");
  cv.addEventListener("dragover", (e) => { e.preventDefault(); });
  cv.addEventListener("drop", (e) => { e.preventDefault(); uploadFiles(e.dataTransfer.files); });
  $("#btnSolve").onclick = (e) => solvePlan(e.shiftKey);
  $("#linkSet").onchange = () => { B.views.clear(); if (S.id !== null) loadPlan(S.id); loadList(); };
  $("#btnJson").onclick = () => { if (S.id !== null) window.location = "/api/plans/" + encodeURIComponent(S.id) + "/result.json?link_set=" + $("#linkSet").value; };
  $("#btnSvg").onclick = exportSvg;
  $("#btnPng").onclick = exportPng;
  try {
    const st = await refreshStatus();
    if (!st.ready) banner(st.solver_error || "the solver is not ready");
    else if (st.data_error) { $("#empty").textContent = "No plan file loaded (" + st.data_error + ") \u2014 use Upload plan, or drop a plan file here."; }
    else $("#empty").textContent = st.plans + " plans loaded. Type an id, press Random or Upload plan (or drop a plan file here), then press Solve.";
    loadList();
    const q = new URLSearchParams(location.search).get("plan");
    if (q) loadPlan(q);
  } catch (e) { banner("The backend does not answer: " + e.message); }
}
init();
