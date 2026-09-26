"use strict";
/* BestTake tutor mode: draws the lesson's own visuals from the scene language, and runs the beat-by-beat player. */

const SVGNS = "http://www.w3.org/2000/svg";
const NW = 156, NH = 60, CW = 246, RH = 104, PAD = 30;
const STAGE_LABEL = { orient: "Orient", reduce: "Reduce", ask: "Your turn", explain: "Explain", derive: "Derive", connect: "Connect", prove: "Prove", compress: "Compress" };

function el(tag, attrs = {}, parent) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
}
const escT = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ---------- Diagram state: replay ops up to a beat ---------- */

function diagramState(beats, upto) {
  const st = { nodes: new Map(), edges: new Map(), note: "", flows: [], hl: new Map(), fresh: new Set(), freshEdges: new Set() };
  for (let i = 0; i <= upto; i++) {
    const v = beats[i] && beats[i].visual;
    if (!v || v.type !== "diagram") continue;
    const cur = i === upto;
    for (const o of v.ops) {
      const n = st.nodes.get(o.id);
      switch (o.op) {
        case "add":
          if (!n) { st.nodes.set(o.id, { id: o.id, label: o.label, kind: o.kind, copies: 1, failed: false, order: st.nodes.size }); if (cur) st.fresh.add(o.id); }
          else Object.assign(n, { label: o.label, kind: o.kind });
          break;
        case "link": st.edges.set(`${o.from}>${o.to}`, { from: o.from, to: o.to, label: o.label }); if (cur) st.freshEdges.add(`${o.from}>${o.to}`); break;
        case "unlink": st.edges.delete(`${o.from}>${o.to}`); break;
        case "remove": st.nodes.delete(o.id); for (const [k, e] of st.edges) if (e.from === o.id || e.to === o.id) st.edges.delete(k); break;
        case "rename": if (n) n.label = o.label; break;
        case "copies": if (n) n.copies = o.count; break;
        case "fail": if (n) n.failed = true; break;
        case "recover": if (n) n.failed = false; break;
        case "note": if (cur) st.note = o.text; break;
        case "flow": if (cur) st.flows.push(o.path); break;
        case "highlight": if (cur) st.hl.set(o.id, o.tone); break;
      }
    }
  }
  return st;
}

function layout(st) {
  const ids = [...st.nodes.keys()].sort((a, b) => st.nodes.get(a).order - st.nodes.get(b).order);
  const preds = new Map(ids.map((i) => [i, []]));
  for (const e of st.edges.values()) if (preds.has(e.to) && st.nodes.has(e.from)) preds.get(e.to).push(e.from);
  const rank = new Map(), visiting = new Set();
  const r = (id) => {
    if (rank.has(id)) return rank.get(id);
    if (visiting.has(id)) return 0;
    visiting.add(id);
    let v = 0;
    for (const p of preds.get(id)) v = Math.max(v, r(p) + 1);
    visiting.delete(id);
    rank.set(id, v);
    return v;
  };
  ids.forEach(r);
  const cols = new Map();
  ids.forEach((id) => { const k = rank.get(id); if (!cols.has(k)) cols.set(k, []); cols.get(k).push(id); });
  const nRanks = cols.size ? Math.max(...cols.keys()) + 1 : 1;
  const maxRows = Math.max(1, ...[...cols.values()].map((c) => c.length));
  const vertical = nRanks > 5 && maxRows <= 3;
  const slot = new Map();
  [...cols.keys()].sort((a, b) => a - b).forEach((k) => {
    const col = cols.get(k);
    if (k > 0) {
      const bary = (id) => { const ps = preds.get(id).filter((p) => slot.has(p)); return ps.length ? ps.reduce((s, p) => s + slot.get(p), 0) / ps.length : 1e3 + st.nodes.get(id).order; };
      col.sort((a, b) => bary(a) - bary(b));
    }
    const offset = (maxRows - col.length) / 2;
    col.forEach((id, i) => slot.set(id, offset + i));
  });
  const pos = new Map();
  for (const id of ids) {
    const k = rank.get(id), s = slot.get(id);
    pos.set(id, vertical ? { x: PAD + s * (NW + 40), y: PAD + k * (NH + 56) } : { x: PAD + k * CW, y: PAD + s * RH });
  }
  const w = vertical ? PAD * 2 + maxRows * (NW + 40) - 40 : PAD * 2 + (nRanks - 1) * CW + NW;
  const h = vertical ? PAD * 2 + nRanks * (NH + 56) - 56 : PAD * 2 + (maxRows - 1) * RH + NH;
  return { pos, w: Math.max(w, 560), h: Math.max(h, 150) + 44, vertical };
}

function edgePath(a, b, vertical) {
  if (vertical) {
    const x1 = a.x + NW / 2, y1 = a.y + NH, x2 = b.x + NW / 2, y2 = b.y;
    if (y2 > y1) { const m = (y1 + y2) / 2; return `M${x1},${y1} C${x1},${m} ${x2},${m} ${x2},${y2}`; }
    return `M${a.x + NW},${a.y + NH / 2} C${a.x + NW + 70},${a.y + NH / 2} ${b.x + NW + 70},${b.y + NH / 2} ${b.x + NW},${b.y + NH / 2}`;
  }
  const x1 = a.x + NW, y1 = a.y + NH / 2, x2 = b.x, y2 = b.y + NH / 2;
  if (x2 > x1) { const m = (x1 + x2) / 2; return `M${x1},${y1} C${m},${y1} ${m},${y2} ${x2},${y2}`; }
  const low = Math.max(a.y, b.y) + NH + 46;
  return `M${a.x + NW / 2},${a.y + NH} C${a.x + NW / 2},${low} ${b.x + NW / 2},${low} ${b.x + NW / 2},${b.y + NH}`;
}

function wrapLabel(text, max = 17) {
  const words = String(text || "").split(/\s+/), lines = [];
  let line = "";
  for (const w of words) {
    if ((line + " " + w).trim().length > max && line) { lines.push(line); line = w; }
    else line = (line + " " + w).trim();
  }
  if (line) lines.push(line);
  if (lines.length > 2) { lines.length = 2; lines[1] = lines[1].slice(0, max - 1) + "…"; }
  return lines;
}

function nodeShape(g, kind) {
  if (kind === "database" || kind === "storage") {
    const e = 9;
    el("path", { class: "shape", d: `M0,${e} C0,-3 ${NW},-3 ${NW},${e} L${NW},${NH - e} C${NW},${NH + 3} 0,${NH + 3} 0,${NH - e} Z` }, g);
    el("path", { class: "shape-line", d: `M0,${e} C0,${e * 2 + 3} ${NW},${e * 2 + 3} ${NW},${e}`, fill: "none" }, g);
    return;
  }
  const rx = kind === "concept" ? NH / 2 : kind === "client" ? 16 : 9;
  el("rect", { class: "shape", width: NW, height: NH, rx }, g);
  if (kind === "queue") for (let i = 0; i < 3; i++) el("line", { class: "shape-line", x1: NW - 14 - i * 9, x2: NW - 14 - i * 9, y1: 12, y2: NH - 12 }, g);
  if (kind === "process") el("rect", { class: "shape-line", x: 4, y: 4, width: NW - 8, height: NH - 8, rx: 6, fill: "none" }, g);
  if (kind === "client") el("circle", { class: "shape-line", cx: 18, cy: NH / 2 - 5, r: 6, fill: "none" }, g);
}

class DiagramView {
  constructor(host) {
    this.svg = el("svg", { class: "diagram-svg", role: "img" });
    const defs = el("defs", {}, this.svg);
    const m = el("marker", { id: "arrow", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" }, defs);
    el("path", { d: "M0,0 L10,5 L0,10 z", class: "arrowhead" }, m);
    this.gEdges = el("g", {}, this.svg);
    this.gNodes = el("g", {}, this.svg);
    this.gFlows = el("g", {}, this.svg);
    this.noteEl = el("text", { class: "diagram-note", "text-anchor": "middle" }, this.svg);
    this.nodes = new Map();
    this.edges = new Map();
    host.appendChild(this.svg);
  }

  render(st) {
    const L = layout(st);
    this.svg.setAttribute("viewBox", `0 0 ${L.w} ${L.h}`);
    this.svg.setAttribute("aria-label", [...st.nodes.values()].map((n) => n.label).join(", "));
    for (const [id, g] of this.nodes) if (!st.nodes.has(id)) { g.classList.add("leave"); setTimeout(() => g.remove(), 400); this.nodes.delete(id); }
    for (const n of st.nodes.values()) {
      let g = this.nodes.get(n.id);
      const p = L.pos.get(n.id);
      if (!g || g.dataset.kind !== n.kind || g.dataset.label !== n.label || +g.dataset.copies !== n.copies) {
        const fresh = !g;
        if (g) g.remove();
        g = el("g", { class: `node kind-${n.kind}` }, this.gNodes);
        g.dataset.kind = n.kind; g.dataset.label = n.label; g.dataset.copies = n.copies;
        for (let c = Math.min(n.copies, 4) - 1; c > 0; c--) {
          const sg = el("g", { class: "copy", transform: `translate(${c * 7},${-c * 7})` }, g);
          nodeShape(sg, n.kind);
        }
        nodeShape(g, n.kind);
        const lines = wrapLabel(n.label);
        const t = el("text", { class: "node-label", x: NW / 2 + (n.kind === "client" ? 8 : 0), y: NH / 2 - (lines.length - 1) * 8 + 5, "text-anchor": "middle" }, g);
        lines.forEach((ln, i) => { const s = el("tspan", { x: NW / 2 + (n.kind === "client" ? 8 : 0), dy: i ? 16 : 0 }, t); s.textContent = ln; });
        if (n.copies > 1) { const b = el("text", { class: "copies-badge", x: NW - 8, y: -6, "text-anchor": "end" }, g); b.textContent = `×${n.copies}`; }
        el("path", { class: "fail-x", d: `M${NW / 2 - 12},${NH / 2 - 12} l24,24 M${NW / 2 + 12},${NH / 2 - 12} l-24,24` }, g);
        g.style.transform = `translate(${p.x}px, ${p.y}px)`;
        if (fresh || st.fresh.has(n.id)) { g.classList.add("enter"); requestAnimationFrame(() => requestAnimationFrame(() => g.classList.remove("enter"))); }
        this.nodes.set(n.id, g);
      }
      g.style.transform = `translate(${p.x}px, ${p.y}px)`;
      g.classList.toggle("failed", n.failed);
      g.classList.remove("hl-warn", "hl-good", "hl-focus");
      if (st.hl.has(n.id)) g.classList.add("hl-" + st.hl.get(n.id));
    }
    for (const [k, g] of this.edges) if (!st.edges.has(k) || !st.nodes.has(st.edges.get(k).from)) { g.remove(); this.edges.delete(k); }
    for (const [k, e] of st.edges) {
      const a = L.pos.get(e.from), b = L.pos.get(e.to);
      if (!a || !b) continue;
      let g = this.edges.get(k);
      const d = edgePath(a, b, L.vertical);
      if (!g) {
        g = el("g", { class: "edge" }, this.gEdges);
        el("path", { class: "edge-line", "marker-end": "url(#arrow)", pathLength: "1" }, g);
        el("rect", { class: "edge-label-bg", rx: 4 }, g);
        el("text", { class: "edge-label", "text-anchor": "middle" }, g);
        if (st.freshEdges.has(k)) { g.classList.add("draw"); setTimeout(() => g.classList.remove("draw"), 900); }
        this.edges.set(k, g);
      }
      const path = g.querySelector(".edge-line");
      path.setAttribute("d", d);
      const lbl = g.querySelector(".edge-label"), bg = g.querySelector(".edge-label-bg");
      lbl.textContent = e.label || "";
      if (e.label) {
        const len = path.getTotalLength ? path.getTotalLength() : 0;
        const mid = len ? path.getPointAtLength(len / 2) : { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
        lbl.setAttribute("x", mid.x); lbl.setAttribute("y", mid.y - 6);
        const wpx = e.label.length * 6.6 + 10;
        Object.entries({ x: mid.x - wpx / 2, y: mid.y - 19, width: wpx, height: 18 }).forEach(([kk, vv]) => bg.setAttribute(kk, vv));
        bg.style.display = "";
      } else bg.style.display = "none";
    }
    this.gFlows.innerHTML = "";
    st.flows.forEach((pathIds) => {
      for (let i = 0; i < pathIds.length - 1; i++) {
        const a = L.pos.get(pathIds[i]), b = L.pos.get(pathIds[i + 1]);
        if (!a || !b) continue;
        const fwd = st.edges.has(`${pathIds[i]}>${pathIds[i + 1]}`), back = st.edges.has(`${pathIds[i + 1]}>${pathIds[i]}`);
        const d = fwd || !back ? edgePath(a, b, L.vertical) : edgePath(b, a, L.vertical);
        const pid = `fp${Math.random().toString(36).slice(2, 8)}`;
        el("path", { id: pid, d, fill: "none", stroke: "none" }, this.gFlows);
        for (let j = 0; j < 3; j++) {
          const c = el("circle", { r: 5, class: "flow-dot", opacity: 0 }, this.gFlows);
          el("set", { attributeName: "opacity", to: "1", begin: `${i * 0.5 + j * 0.53}s` }, c);
          const am = el("animateMotion", { dur: "1.6s", repeatCount: "indefinite", begin: `${i * 0.5 + j * 0.53}s`, keyPoints: fwd || !back ? "0;1" : "1;0", keyTimes: "0;1", calcMode: "linear" }, c);
          el("mpath", { href: `#${pid}` }, am);
        }
      }
    });
    this.noteEl.setAttribute("x", L.w / 2);
    this.noteEl.setAttribute("y", L.h - 12);
    this.noteEl.textContent = st.note || "";
  }
}

/* ---------- Chart, table, code ---------- */

function niceTicks(lo, hi, n = 5) {
  if (lo === hi) { lo -= 1; hi += 1; }
  const span = hi - lo, step0 = span / n, mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || mag * 10;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}
const fmtNum = (v) => (Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(1) + "M" : Math.abs(v) >= 1e3 ? (v / 1e3).toFixed(Math.abs(v) >= 1e4 ? 0 : 1) + "k" : String(+v.toFixed(2)));

function renderChart(host, v) {
  const W = 760, H = 400, L = 64, R = 24, T = v.title ? 44 : 20, B = 58;
  const svg = el("svg", { class: "chart-svg", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": v.title || "Chart" }, host);
  if (v.title) { const t = el("text", { class: "chart-title", x: L, y: 26 }, svg); t.textContent = v.title; }
  const pts = v.series.flatMap((s) => s.points);
  const bar = v.kind === "bar";
  const xs = bar ? v.series[0].points.map((p) => p[0]) : pts.map((p) => p[0]);
  const x0 = bar ? 0 : Math.min(...xs), x1 = bar ? xs.length : Math.max(...xs);
  const y0 = Math.min(0, ...pts.map((p) => p[1])), y1 = Math.max(...pts.map((p) => p[1]));
  const yt = niceTicks(y0, y1), ymin = Math.min(y0, yt[0]), ymax = Math.max(y1, yt[yt.length - 1]);
  const sx = (x) => L + ((x - x0) / ((x1 - x0) || 1)) * (W - L - R), sy = (y) => H - B - ((y - ymin) / ((ymax - ymin) || 1)) * (H - T - B);
  yt.forEach((t) => {
    el("line", { class: "grid", x1: L, x2: W - R, y1: sy(t), y2: sy(t) }, svg);
    const lb = el("text", { class: "tick", x: L - 8, y: sy(t) + 4, "text-anchor": "end" }, svg); lb.textContent = fmtNum(t);
  });
  el("line", { class: "axis", x1: L, x2: W - R, y1: sy(ymin), y2: sy(ymin) }, svg);
  if (bar) {
    const bw = (W - L - R) / xs.length;
    v.series[0].points.forEach((p, i) => {
      const r = el("rect", { class: "bar", x: L + i * bw + bw * 0.18, width: bw * 0.64, y: sy(p[1]), height: Math.max(0, sy(ymin) - sy(p[1])) }, svg);
      r.style.animationDelay = `${i * 60}ms`;
      const lb = el("text", { class: "tick", x: L + i * bw + bw / 2, y: H - B + 18, "text-anchor": "middle" }, svg); lb.textContent = fmtNum(p[0]);
    });
  } else {
    niceTicks(x0, x1, 6).filter((t) => t >= x0 && t <= x1).forEach((t) => {
      const lb = el("text", { class: "tick", x: sx(t), y: H - B + 18, "text-anchor": "middle" }, svg); lb.textContent = fmtNum(t);
    });
    v.series.forEach((s, i) => {
      const d = s.points.map((p, j) => `${j ? "L" : "M"}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join(" ");
      const path = el("path", { class: `series s${i}`, d, pathLength: "1" }, svg);
      path.style.animationDelay = `${i * 300}ms`;
    });
    if (v.series.length > 1) v.series.forEach((s, i) => {
      el("line", { class: `series s${i}`, x1: L + i * 150, x2: L + i * 150 + 22, y1: H - 14, y2: H - 14, style: "animation:none;stroke-dasharray:none" }, svg);
      const t = el("text", { class: "tick", x: L + i * 150 + 28, y: H - 10 }, svg); t.textContent = s.name;
    });
  }
  if (v.mark && v.mark.x != null && !bar) {
    el("line", { class: "mark", x1: sx(v.mark.x), x2: sx(v.mark.x), y1: T, y2: sy(ymin) }, svg);
    const t = el("text", { class: "mark-label", x: sx(v.mark.x) + 6, y: T + 14 }, svg); t.textContent = v.mark.label || "";
  }
  if (v.x_label) { const t = el("text", { class: "axis-label", x: (L + W - R) / 2, y: H - B + 40, "text-anchor": "middle" }, svg); t.textContent = v.x_label; }
  if (v.y_label) { const t = el("text", { class: "axis-label", transform: `translate(16 ${(T + H - B) / 2}) rotate(-90)`, "text-anchor": "middle" }, svg); t.textContent = v.y_label; }
}

function renderTable(host, v) {
  host.insertAdjacentHTML("beforeend", `<div class="stage-scroll"><table class="stage-table"><thead><tr>${v.columns.map((c) => `<th>${escT(c)}</th>`).join("")}</tr></thead>
    <tbody>${v.rows.map((r, i) => `<tr class="${i === v.highlight ? "hl" : ""}" style="animation-delay:${i * 90}ms">${r.map((c) => `<td>${escT(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
}

function renderCode(host, v) {
  const hl = new Set(v.highlight || []);
  host.insertAdjacentHTML("beforeend", `<div class="stage-scroll"><pre class="stage-code" aria-label="${escT(v.lang || "code")}">${v.code.split("\n").map((ln, i) => `<span class="ln ${hl.has(i + 1) ? "hl" : ""}" style="animation-delay:${i * 40}ms"><i>${i + 1}</i>${escT(ln) || " "}</span>`).join("")}</pre></div>`);
}

/* ---------- Stage: decide what to show at a beat ---------- */

class Stage {
  constructor(host, beats) {
    this.host = host; this.beats = beats; this.diagram = null; this.mode = null; this.key = null;
  }
  show(k) {
    let j = k;
    while (j >= 0 && !this.beats[j].visual) j--;
    const v = j >= 0 ? this.beats[j].visual : null;
    if (!v) {
      const anyDiagram = this.beats.slice(0, k + 1).some((b) => b.visual && b.visual.type === "diagram");
      if (!anyDiagram) { this.host.innerHTML = `<div class="stage-empty">The picture builds up as we go.</div>`; this.mode = "empty"; this.diagram = null; return; }
    }
    if (!v || v.type === "diagram") {
      if (this.mode !== "diagram") { this.host.innerHTML = ""; this.diagram = new DiagramView(this.host); this.mode = "diagram"; }
      this.diagram.render(diagramState(this.beats, k));
      return;
    }
    const key = `${j}`;
    if (this.mode === v.type && this.key === key) return;
    this.host.innerHTML = ""; this.diagram = null; this.mode = v.type; this.key = key;
    if (v.type === "chart") renderChart(this.host, v);
    else if (v.type === "table") renderTable(this.host, v);
    else if (v.type === "code") renderCode(this.host, v);
  }
}

/* ---------- Tutor lesson player ---------- */

function tutorLessonHtml(data, h) {
  const { lesson } = data, c = lesson.content, ch = c.chain || {};
  const chain = ch.mechanism || ch.pressure ? `<ol class="chain" aria-label="Requirement to mechanism">
      ${[["Requirement", ch.pressure], ["Problem", ch.problem], ["Property", ch.property], ["Mechanism", ch.mechanism]]
        .filter(([, v]) => v).map(([k, v]) => `<li><b>${k}</b><span>${h.esc(v)}</span></li>`).join("")}</ol>` : "";
  return `
    <div class="lesson-head"><div class="where"><span class="stage-chip st-${h.esc(ch.stage || "derive")}">${h.esc(STAGE_LABEL[ch.stage] || "Derive")}</span> ${h.esc(lesson.module_title)}, lesson ${data.position} of ${data.total}</div>
      <h1>${h.esc(lesson.title)}</h1>${c.goal ? `<p class="hook">${h.esc(c.goal)}</p>` : ""}${chain}</div>
    <section class="tplayer" aria-label="Lesson">
      <div class="stage2" id="stage2"></div>
      <div class="beats-bar" role="tablist" aria-label="Beats">${c.beats.map((b, i) => `<button role="tab" class="k-${b.kind}" data-i="${i}" title="${h.esc(STAGE_LABEL[b.kind] || b.kind)}"></button>`).join("")}</div>
      <div class="beat" id="beat" aria-live="polite"></div>
      <div class="stepnav">
        <button class="ghost" id="bprev">Back</button>
        <span class="counter" id="bcount"></span>
        <label class="speak"><input type="checkbox" id="speak"> Read aloud</label>
        <button id="bnext">Next</button>
      </div>
    </section>
    <section class="after" id="after" hidden>
      ${c.rules.length ? `<div class="rulecard"><h2>Compressed into rules</h2><ol>${c.rules.map((r) => `<li>${h.esc(r)}</li>`).join("")}</ol></div>` : ""}
      ${c.challenge ? `<div class="block"><h2>Prove it</h2><div id="challenge"></div></div>` : ""}
      <div class="block"><h2>Did the method work here?</h2>
        <p class="muted small">Where did the explanation break, or what felt memorized instead of derived? Notes feed the next version of your tutor protocol.</p>
        <textarea id="pnote" rows="3" placeholder="The load balancer appeared before I felt the need for it…"></textarea>
        <button class="ghost" id="pnotebtn" style="margin-top:.5rem">Save note</button></div>
    </section>
    ${c.sources.length ? `<p class="credit">Built from the best explanations found: ${c.sources.map((s) => `<a href="${h.esc(s.url)}" target="_blank" rel="noopener">${h.esc(s.title)}</a> (${h.esc(s.channel)})`).join(", ")}.</p>` : ""}`;
}

function answerBox(item, key, h, canCheck) {
  return `<div class="askbox" data-key="${key}">
    <p class="question">${h.esc(item.question)}</p>
    <textarea rows="3" placeholder="Think it through in your own words…"></textarea>
    <div class="askbtns">
      ${canCheck ? `<button class="check">Check my reasoning</button>` : ""}
      ${item.hint ? `<button class="ghost hintbtn">Hint</button>` : ""}
      <button class="ghost reveal">Show the idea</button>
    </div>
    <div class="hint-out" hidden><b>Hint:</b> ${h.esc(item.hint || "")}</div>
    <div class="feedback" hidden></div>
    <div class="idea-out" hidden><b>The idea:</b> ${h.md(item.answer || "")}
      <div class="selfcheck"><span class="muted small">Did you reach it?</span> <button class="ghost small yes">Yes</button> <button class="ghost small no">Not yet</button></div></div>
    <div class="primitive-out" hidden></div>
  </div>`;
}

function wireAnswerBox(box, item, opts, h) {
  const ta = box.querySelector("textarea");
  const show = (sel) => { box.querySelector(sel).hidden = false; };
  const prim = (name, explain) => {
    const p = box.querySelector(".primitive-out");
    if (!name) return;
    p.innerHTML = `<p><b>Missing piece: ${h.esc(name)}.</b> ${h.esc(explain || "")}</p>
      ${opts.courseMode === "tutor" ? `<button class="ghost small addgap">Add a lesson on “${h.esc(name)}”</button>` : ""}`;
    p.hidden = false;
    const b = p.querySelector(".addgap");
    if (b) b.onclick = async () => {
      try { await h.api(`/api/courses/${opts.courseId}/gap`, { method: "POST", body: { primitive: name, after_lesson_id: opts.lessonId } }); h.toast("Building a lesson for that missing piece"); b.disabled = true; }
      catch (ex) { h.toast(ex.message); }
    };
  };
  const hb = box.querySelector(".hintbtn"); if (hb) hb.onclick = () => show(".hint-out");
  box.querySelector(".reveal").onclick = () => show(".idea-out");
  const self = async (ok) => {
    try { await h.api(`/api/lessons/${opts.lessonId}/self`, { method: "POST", body: { ...opts.target, got_it: ok } }); } catch (_) { /* best effort */ }
    box.querySelector(".selfcheck").innerHTML = ok ? `<span class="ok small">Good. On to the next step.</span>` : `<span class="small">Let's find the piece underneath.</span>`;
    if (!ok) prim(item.missing_primitive?.name, item.missing_primitive?.explain);
  };
  box.querySelector(".yes").onclick = () => self(true);
  box.querySelector(".no").onclick = () => self(false);
  const cb = box.querySelector(".check");
  if (cb) cb.onclick = async () => {
    if (ta.value.trim().length < 2) { ta.focus(); return; }
    cb.disabled = true; cb.textContent = "Checking…";
    const fb = box.querySelector(".feedback");
    try {
      const r = await h.api(`/api/lessons/${opts.lessonId}/check`, { method: "POST", body: { ...opts.target, answer: ta.value } });
      fb.className = `feedback v-${r.verdict}`;
      fb.innerHTML = `<b>${r.verdict === "got_it" ? "You derived it." : r.verdict === "partly" ? "Partly there." : "Not yet."}</b> ${h.esc(r.feedback)}`;
      fb.hidden = false;
      if (r.verdict !== "got_it") prim(r.missing_primitive || item.missing_primitive?.name, r.missing_primitive ? "" : item.missing_primitive?.explain);
    } catch (ex) { fb.className = "feedback"; fb.textContent = ex.message; fb.hidden = false; }
    cb.disabled = false; cb.textContent = "Check again";
  };
}

function mountTutorLesson(data, h) {
  const { lesson, course } = data, c = lesson.content, beats = c.beats;
  const stage = new Stage(document.getElementById("stage2"), beats);
  const canCheck = course.provider !== "none" && course.engine_available;
  const opts = { lessonId: lesson.id, courseId: course.id, courseMode: course.mode };
  let cur = 0;
  const speakBox = document.getElementById("speak");
  const say = (text) => {
    if (!("speechSynthesis" in window)) return;
    speechSynthesis.cancel();
    if (speakBox.checked && text) speechSynthesis.speak(new SpeechSynthesisUtterance(text.replace(/[*`#]/g, "")));
  };
  speakBox.onchange = () => { if (!speakBox.checked && "speechSynthesis" in window) speechSynthesis.cancel(); else show(cur); };
  if (!("speechSynthesis" in window)) speakBox.closest("label").hidden = true;
  const show = (i) => {
    cur = Math.max(0, Math.min(beats.length - 1, i));
    const b = beats[cur];
    stage.show(cur);
    const src = b.source && c.sources[b.source.idx];
    document.getElementById("beat").innerHTML = `
      <span class="stage-chip st-${h.esc(b.kind)}">${h.esc(STAGE_LABEL[b.kind] || b.kind)}</span>
      <div class="beat-text">${h.md(b.text)}</div>
      ${b.question ? answerBox(b, cur, h, canCheck) : ""}
      ${src ? `<p class="small muted">See it explained in <a href="${h.esc(src.url)}&t=${b.source.t}s" target="_blank" rel="noopener">${h.esc(src.title)}</a> at ${h.fmt(b.source.t)}.</p>` : ""}`;
    const box = document.querySelector("#beat .askbox");
    if (box) wireAnswerBox(box, b, { ...opts, target: { beat: cur } }, h);
    document.getElementById("bcount").textContent = `${cur + 1} of ${beats.length}`;
    document.getElementById("bprev").disabled = cur === 0;
    document.getElementById("bnext").textContent = cur === beats.length - 1 ? "Finish" : "Next";
    document.querySelectorAll(".beats-bar button").forEach((x, j) => { x.setAttribute("aria-selected", String(j === cur)); x.classList.toggle("seen", j <= cur); });
    say([b.text, b.question].filter(Boolean).join(" "));
  };
  document.getElementById("bprev").onclick = () => show(cur - 1);
  document.getElementById("bnext").onclick = () => {
    if (cur === beats.length - 1) {
      const a = document.getElementById("after"); a.hidden = false; a.scrollIntoView({ behavior: "smooth", block: "start" });
    } else show(cur + 1);
  };
  document.querySelectorAll(".beats-bar button").forEach((b) => { b.onclick = () => show(+b.dataset.i); });
  h.setKeys((e) => {
    if (/input|textarea|select/i.test(document.activeElement?.tagName || "")) return;
    if (e.key === "ArrowRight") document.getElementById("bnext").click();
    if (e.key === "ArrowLeft") show(cur - 1);
  });
  if (c.challenge) {
    const host = document.getElementById("challenge");
    host.innerHTML = answerBox(c.challenge, "challenge", h, canCheck);
    wireAnswerBox(host.querySelector(".askbox"), c.challenge, { ...opts, target: { challenge: true } }, h);
  }
  document.getElementById("pnotebtn").onclick = async () => {
    const t = document.getElementById("pnote");
    try { await h.api("/api/protocol/notes", { method: "POST", body: { text: t.value, lesson_id: lesson.id } }); t.value = ""; h.toast("Saved to your protocol notes"); }
    catch (ex) { h.toast(ex.message); }
  };
  show(0);
}

window.BTTutor = { tutorLessonHtml, mountTutorLesson, STAGE_LABEL };
