"use strict";

const state = { me: null, poll: null };
const app = document.getElementById("app");
const ACTIVE = ["queued", "planning", "building"];

const CRIT = [
  { k: "teaching", label: "Teaching", color: "var(--c-teach)" },
  { k: "correctness", label: "Correctness", color: "var(--c-correct)" },
  { k: "coverage", label: "Coverage", color: "var(--c-cover)" },
  { k: "visual", label: "Visuals", color: "var(--c-visual)" },
  { k: "comments", label: "Viewer comments", color: "var(--c-comments)" },
  { k: "outperformance", label: "Beats channel reach", color: "var(--c-aud1)" },
  { k: "like_rate", label: "Like rate", color: "var(--c-aud2)" },
  { k: "velocity", label: "Views per day", color: "var(--c-aud3)" },
  { k: "comment_rate", label: "Comment rate", color: "var(--c-aud3)" },
  { k: "freshness", label: "Freshness", color: "var(--c-aud3)" },
];

const ENGINE_TEXT = { none: "Basic, no AI", ollama: "Local model", anthropic: "Claude" };
const STATUS_TEXT = {
  queued: "Waiting to start", planning: "Planning lessons", building: "Finding the best videos",
  ready: "Ready", partial: "Ready, some lessons need a retry", failed: "Stopped", interrupted: "Paused",
};
const LESSON_TEXT = {
  pending: "Waiting", searching: "Searching", judging: "Judging", extracting: "Extracting visuals", writing: "Writing notes", ready: "Ready", failed: "Failed",
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (sec) => {
  sec = Math.max(0, Math.round(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
};
const num = (n) => (n == null ? "–" : n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + "k" : String(n));

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 3200);
}

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method, credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (res.status === 401 && !path.startsWith("/api/login")) {
    state.me = null;
    renderNav();
    if (!location.hash.startsWith("#/signin")) location.hash = "#/signin";
    throw new Error(data.detail || "Sign in to continue.");
  }
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Check the form and try again.");
  return data;
}

function md(src) {
  const lines = String(src || "").split("\n");
  const inline = (t) => esc(t)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  let html = "", list = null, code = null;
  for (const raw of lines) {
    if (raw.trim().startsWith("```")) {
      if (code === null) { if (list) { html += `</${list}>`; list = null; } code = []; }
      else { html += `<pre><code>${esc(code.join("\n"))}</code></pre>`; code = null; }
      continue;
    }
    if (code !== null) { code.push(raw); continue; }
    const l = raw.trim();
    const m = l.match(/^([-*]|\d+[.)])\s+(.*)/);
    if (m) {
      const tag = /^\d/.test(m[1]) ? "ol" : "ul";
      if (list !== tag) { if (list) html += `</${list}>`; html += `<${tag}>`; list = tag; }
      html += `<li>${inline(m[2])}</li>`;
      continue;
    }
    if (list) { html += `</${list}>`; list = null; }
    if (!l) continue;
    const h = l.match(/^#{1,4}\s+(.*)/);
    html += h ? `<h3>${inline(h[1])}</h3>` : `<p>${inline(l)}</p>`;
  }
  if (list) html += `</${list}>`;
  if (code !== null) html += `<pre><code>${esc(code.join("\n"))}</code></pre>`;
  return html;
}

function scoreBar(contrib) {
  const total = CRIT.reduce((s, c) => s + (contrib?.[c.k] || 0), 0);
  const parts = CRIT.map((c) => {
    const v = contrib?.[c.k] || 0;
    return v > 0 ? `<i style="width:${v}%;background:${c.color}" title="${esc(c.label)}: ${v.toFixed(1)} pts"></i>` : "";
  }).join("");
  return `<div class="scorebar" role="img" aria-label="Score ${total.toFixed(0)} out of 100">${parts}</div>`;
}
const legend = () => `<div class="legend">${CRIT.slice(0, 6).map((c) => `<span style="--sw:${c.color}">${esc(c.label)}</span>`).join("")}<span style="--sw:var(--c-aud3)">Other audience signals</span></div>`;

function renderNav() {
  const nav = document.getElementById("nav");
  if (!state.me) { nav.innerHTML = ""; return; }
  const u = state.me.user, usage = state.me.usage;
  const plan = u.plan === "free" ? `Free plan, ${usage.courses} of ${usage.limit} courses` : "Pro";
  nav.innerHTML = `<span class="plan">${esc(plan)}</span><a href="#/">My courses</a>${u.is_owner ? `<a href="#/settings">Settings</a>` : ""}<button class="link" id="signout">Sign out</button>`;
  document.getElementById("signout").onclick = async () => {
    await api("/api/logout", { method: "POST" }).catch(() => {});
    state.me = null; renderNav(); location.hash = "#/signin";
  };
}

async function loadMe() {
  try { state.me = await api("/api/me"); } catch (_) { state.me = null; }
  renderNav();
}

/* ---------- Sign in ---------- */

function viewSignin(mode = "signin") {
  const signup = mode === "signup";
  app.innerHTML = `
  <div class="welcome">
    <div>
      <h1>Learn anything from the best explanation on YouTube.</h1>
      <p class="lede">Name a topic. BestTake plans a zero-to-hero path, puts every candidate video in front of a jury that scores teaching, correctness, visuals and what viewers say, then teaches you each lesson with the winner.</p>
      <div class="jury demo-card" aria-hidden="true">
        <h3>How a pick is made</h3>
        <p class="muted small">Each bar is one video. Wider colored segments mean it earned more points on that criterion.</p>
        <div class="cand pick"><div class="score">86</div><div><div class="title"><a>Consistent hashing, built up step by step</a></div>
          <div class="bar-wrap">${scoreBar({ teaching: 20, correctness: 11, coverage: 9, visual: 16, comments: 16, outperformance: 6, like_rate: 4, velocity: 2, comment_rate: 1, freshness: 1 })}</div></div></div>
        <div class="cand"><div class="score">61</div><div><div class="title"><a>Consistent hashing in 5 minutes</a></div>
          <div class="bar-wrap">${scoreBar({ teaching: 13, correctness: 8, coverage: 5, visual: 7, comments: 11, outperformance: 7, like_rate: 4, velocity: 3, comment_rate: 1, freshness: 2 })}</div></div></div>
        ${legend()}
      </div>
    </div>
    <form class="authbox" id="authform" novalidate>
      <h2>${signup ? "Create your account" : "Sign in"}</h2>
      ${signup ? `<div class="field"><label for="name">Name</label><input id="name" autocomplete="name"></div>` : ""}
      <div class="field"><label for="email">Email</label><input id="email" type="email" autocomplete="email" required></div>
      <div class="field"><label for="password">Password</label><input id="password" type="password" autocomplete="${signup ? "new-password" : "current-password"}" required minlength="8"></div>
      <p class="error" id="autherr"></p>
      <button type="submit">${signup ? "Create account" : "Sign in"}</button>
      <p class="switch muted">${signup ? `Have an account? <a href="#/signin">Sign in</a>` : `New here? <a href="#/signup">Create an account</a>`}</p>
    </form>
  </div>`;
  document.getElementById("authform").onsubmit = async (e) => {
    e.preventDefault();
    const err = document.getElementById("autherr");
    err.textContent = "";
    const body = { email: document.getElementById("email").value, password: document.getElementById("password").value };
    if (signup) body.name = document.getElementById("name").value;
    try {
      await api(signup ? "/api/signup" : "/api/login", { method: "POST", body });
      await loadMe();
      location.hash = "#/";
    } catch (ex) { err.textContent = ex.message; }
  };
}

/* ---------- Dashboard ---------- */

async function viewHome() {
  const [{ courses }, { templates }] = await Promise.all([api("/api/courses"), api("/api/templates"), loadMe()]);
  const me = state.me;
  const profiles = Object.entries(me.profiles || { balanced: "Balanced" });
  const engines = me.engines || [];
  app.innerHTML = `
  <form class="builder" id="builder">
    <h1>What do you want to learn?</h1>
    <div class="field"><label for="topic" class="muted">Topic</label>
      <input id="topic" class="topic" placeholder="System design: low-level and high-level design" required maxlength="200"></div>
    <div class="row3">
      <div class="field"><label for="engine">Ranking engine</label>
        <select id="engine">${engines.map((e) => `<option value="${esc(e.id)}" ${e.available ? "" : "disabled"} ${e.id === me.default_engine ? "selected" : ""}>${esc(e.label)}${e.available ? "" : " (not set up)"}</option>`).join("")}</select>
        <p class="hint" id="enginenote"></p></div>
      <div class="field"><label for="level">Starting point</label>
        <select id="level"><option value="beginner">New to this</option><option value="intermediate">Some background</option><option value="advanced">Experienced</option></select></div>
      <div class="field"><label for="profile">What makes a video best</label>
        <select id="profile">${profiles.map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("")}</select></div>
    </div>
    <div class="field">
      <div class="labelrow"><label for="lessons">Lessons <span class="muted" id="lessonsreq"></span></label>
        <select id="template" aria-label="Start from a template"><option value="">Start from a template</option>${templates.map((t) => `<option value="${esc(t.id)}">${esc(t.label)}</option>`).join("")}</select></div>
      <textarea id="lessons" rows="7" spellcheck="false" placeholder="# Core building blocks&#10;Load balancing: round robin, health checks, layer 7&#10;Caching: eviction, LRU, CDN&#10;Consistent hashing"></textarea>
      <p class="hint">One lesson per line. Start a line with # to begin a module. Add concepts after a colon so coverage can be checked.</p>
    </div>
    <div class="row3 ai-only">
      <div class="field"><label for="depth">Length when the AI plans</label>
        <select id="depth"><option value="quick">Quick, 6 lessons</option><option value="standard" selected>Standard, 12 lessons</option><option value="deep">Deep, 20 lessons</option></select></div>
      <div class="field" style="grid-column: span 2"><label for="goal">Goal <span class="muted">(optional)</span></label>
        <input id="goal" placeholder="Pass system design interviews at senior level" maxlength="500"></div>
    </div>
    <p class="error" id="builderr"></p>
    <div class="actions"><button type="submit" id="buildbtn">Build course</button>
      <span class="muted small" id="buildhint"></span></div>
  </form>
  <h2>Your courses</h2>
  ${courses.length ? `<ul class="courses">${courses.map(courseRow).join("")}</ul>` : `<p class="empty">No courses yet. Enter a topic above to build your first one.</p>`}`;

  const engineSel = document.getElementById("engine");
  const syncEngine = () => {
    const e = engines.find((x) => x.id === engineSel.value) || engines[0];
    const basic = e.id === "none";
    document.getElementById("enginenote").textContent = e.note || "";
    document.getElementById("lessonsreq").textContent = basic ? "(required in Basic mode)" : "(optional, leave empty and the AI plans them)";
    document.querySelectorAll(".ai-only").forEach((el) => { el.hidden = basic; });
    document.getElementById("buildhint").textContent = basic
      ? "Basic mode takes about a minute per lesson, mostly reading YouTube."
      : e.id === "ollama" ? "A local model takes a few minutes per lesson. Lessons appear as they finish."
      : "Building takes a few minutes. You can start lessons as soon as they're ready.";
  };
  engineSel.onchange = syncEngine;
  syncEngine();

  document.getElementById("template").onchange = (e) => {
    const t = templates.find((x) => x.id === e.target.value);
    if (!t) return;
    const ta = document.getElementById("lessons");
    if (ta.value.trim() && !confirm("Replace your lesson list with this template?")) { e.target.value = ""; return; }
    ta.value = t.text;
    const topic = document.getElementById("topic");
    if (!topic.value.trim()) topic.value = t.topic;
    e.target.value = "";
  };

  document.getElementById("builder").onsubmit = async (e) => {
    e.preventDefault();
    const btn = document.getElementById("buildbtn"), err = document.getElementById("builderr");
    err.textContent = ""; btn.disabled = true;
    try {
      const r = await api("/api/courses", { method: "POST", body: {
        topic: document.getElementById("topic").value, level: document.getElementById("level").value,
        depth: document.getElementById("depth").value, profile: document.getElementById("profile").value,
        goal: document.getElementById("goal").value, engine: engineSel.value,
        lessons: document.getElementById("lessons").value,
      } });
      location.hash = `#/course/${r.id}`;
    } catch (ex) { err.textContent = ex.message; btn.disabled = false; }
  };
  if (courses.some((c) => ACTIVE.includes(c.status))) state.poll = setTimeout(route, 5000);
}

function courseRow(c) {
  const pctReady = c.total ? Math.round((c.ready / c.total) * 100) : 0;
  const live = ACTIVE.includes(c.status);
  const progress = c.total ? `${c.done} of ${c.total} lessons done` : "Planning";
  return `<li><a class="course" href="#/course/${c.id}">
    <span><span class="ct">${esc(c.title || c.topic)}</span><br><span class="status ${live ? "live" : ""}">${esc(STATUS_TEXT[c.status] || c.status)}, ${esc(ENGINE_TEXT[c.provider] || "")}</span></span>
    <span><span class="meter" title="${pctReady}% of lessons built"><i style="width:${pctReady}%"></i></span></span>
    <span class="small muted">${esc(progress)}</span></a></li>`;
}

/* ---------- Outline ---------- */

function outlineHtml(course, lessons, currentId) {
  let html = `<nav class="outline collapsed" id="outline" aria-label="Lessons">
    <button class="ghost outline-toggle" id="otoggle" aria-expanded="false">Show all lessons</button>
    <a class="course-name" href="#/course/${course.id}">${esc(course.title || course.topic)}</a>`;
  let mod = null, n = 0;
  for (const l of lessons) {
    if (l.module_idx !== mod) {
      if (mod !== null) html += "</ol>";
      html += `<h4>${esc(l.module_title)}</h4><ol>`;
      mod = l.module_idx;
    }
    n += 1;
    const ready = l.status === "ready";
    const mark = l.completed ? `<span class="mark done" aria-label="Done">✓</span>`
      : l.status === "failed" ? `<span class="mark fail" aria-label="Failed">!</span>`
      : `<span class="mark">${n}</span>`;
    const cur = l.id === currentId ? ` aria-current="page"` : "";
    html += `<li><a href="#/lesson/${l.id}" class="${ready ? "" : "off"}"${cur} title="${esc(LESSON_TEXT[l.status] || "")}">${mark}<span>${esc(l.title)}</span></a></li>`;
  }
  if (mod !== null) html += "</ol>";
  return html + "</nav>";
}

function wireOutline() {
  const t = document.getElementById("otoggle"), o = document.getElementById("outline");
  if (!t) return;
  t.onclick = () => {
    const open = o.classList.toggle("collapsed") === false;
    t.setAttribute("aria-expanded", String(open));
    t.textContent = open ? "Hide lessons" : "Show all lessons";
  };
}

/* ---------- Course ---------- */

async function viewCourse(id) {
  const { course, lessons, events } = await api(`/api/courses/${id}`);
  const live = ACTIVE.includes(course.status) || course.running;
  const firstReady = lessons.find((l) => l.status === "ready" && !l.completed) || lessons.find((l) => l.status === "ready");
  const failed = lessons.filter((l) => l.status === "failed").length;
  const canResume = !live && ["failed", "interrupted", "partial"].includes(course.status);
  app.innerHTML = `<div class="split">
    ${outlineHtml(course, lessons, null)}
    <div>
      <h1>${esc(course.title || course.topic)}</h1>
      ${course.summary ? `<p class="hook">${esc(course.summary)}</p>` : ""}
      <p class="status ${live ? "live" : ""}">Ranked with ${esc(ENGINE_TEXT[course.provider] || "")}. ${esc(STATUS_TEXT[course.status] || course.status)}${lessons.length ? `, ${lessons.filter((l) => l.status === "ready").length} of ${lessons.length} lessons built` : ""}</p>
      ${course.error ? `<div class="notice">${esc(course.error)}</div>` : ""}
      <div class="actions" style="display:flex;gap:.8rem;flex-wrap:wrap;margin:1.2rem 0">
        ${firstReady ? `<a class="btn" href="#/lesson/${firstReady.id}">${firstReady.completed ? "Review" : "Start"} “${esc(firstReady.title)}”</a>` : ""}
        ${canResume ? `<button class="ghost" id="resume">${failed ? `Retry ${failed} failed lesson${failed > 1 ? "s" : ""}` : "Resume building"}</button>` : ""}
        ${!live ? `<button class="ghost" id="del">Delete course</button>` : ""}
      </div>
      <h3>Build log</h3>
      <div class="log" id="log">${events.map((e) => `<div>${esc(new Date(e.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}  ${esc(e.msg)}</div>`).join("") || "<div>Nothing yet.</div>"}</div>
    </div></div>`;
  wireOutline();
  const log = document.getElementById("log"); log.scrollTop = log.scrollHeight;
  const r = document.getElementById("resume");
  if (r) r.onclick = async () => { try { await api(`/api/courses/${id}/resume`, { method: "POST" }); route(); } catch (ex) { toast(ex.message); } };
  const d = document.getElementById("del");
  if (d) d.onclick = async () => {
    if (!confirm("Delete this course and its progress?")) return;
    try { await api(`/api/courses/${id}`, { method: "DELETE" }); location.hash = "#/"; toast("Course deleted"); } catch (ex) { toast(ex.message); }
  };
  if (live) state.poll = setTimeout(route, 3000);
}

/* ---------- Lesson ---------- */

async function viewLesson(id) {
  const data = await api(`/api/lessons/${id}`);
  const { lesson, course, outline, candidates, prev, next, position, total, progress } = data;
  const c = lesson.content;
  const head = `<div class="lesson-head"><div class="where">${esc(lesson.module_title)}, lesson ${position} of ${total}</div><h1>${esc(lesson.title)}</h1></div>`;

  if (!c) {
    const live = ACTIVE.includes(course.status);
    app.innerHTML = `<div class="split">${outlineHtml(course, outline, lesson.id)}<div>${head}
      <p class="status ${live ? "live" : ""}">${esc(LESSON_TEXT[lesson.status] || lesson.status)}</p>
      ${lesson.error ? `<div class="notice">${esc(lesson.error)}</div>` : ""}
      <p class="muted">This lesson isn't built yet. ${live ? "It will appear here when its video is picked." : ""}</p>
      ${!live ? `<button id="rebuild">Build this lesson</button>` : ""}</div></div>`;
    wireOutline();
    const b = document.getElementById("rebuild");
    if (b) b.onclick = async () => { try { await api(`/api/lessons/${id}/rebuild`, { method: "POST" }); toast("Building lesson"); location.hash = `#/course/${course.id}`; } catch (ex) { toast(ex.message); } };
    if (live) state.poll = setTimeout(route, 4000);
    return;
  }

  const src = c.source || {};
  const steps = c.steps || [];
  const ytAt = (t) => `${src.url || ""}${src.url && src.url.includes("?") ? "&" : "?"}t=${Math.max(0, Math.round(t || 0))}s`;
  app.innerHTML = `<div class="split">${outlineHtml(course, outline, lesson.id)}
  <article>
    ${head}
    ${c.intro ? `<p class="hook">${esc(c.intro)}</p>` : ""}
    ${steps.length ? `
    <section class="buildup" aria-label="Step-by-step build-up">
      <div class="stage" id="stage"></div>
      <div class="caption" id="caption" aria-live="polite"></div>
      <div class="stepnav">
        <button class="ghost" id="prevstep">Previous step</button>
        <span class="counter" id="counter"></span>
        <button id="nextstep">Next step</button>
      </div>
      <div class="filmstrip" role="tablist" aria-label="All steps">
        ${steps.map((st, i) => `<button role="tab" data-i="${i}" title="${esc(st.title || `Step ${i + 1}`)}"><img src="${esc(st.image)}" alt="" loading="lazy">${st.clip ? `<span class="motion" aria-label="animated">▶</span>` : ""}</button>`).join("")}
      </div>
      <p class="credit">Visuals and narration from <a href="${esc(src.url)}" target="_blank" rel="noopener">${esc(src.title)}</a> by ${esc(src.channel)}${src.rank > 1 ? ` (ranked #${src.rank}; the top pick had fewer visuals)` : ""}.</p>
    </section>` : `
    <div class="notice"><p><strong>No visuals were extracted for this lesson.</strong> ${esc(c.extract_error || "")}</p>
      ${src.url ? `<p style="margin:0">Best source: <a href="${esc(src.url)}" target="_blank" rel="noopener">${esc(src.title)}</a> by ${esc(src.channel)}.</p>` : ""}
      <button class="ghost" id="rebuild2" style="margin-top:.8rem">Try this lesson again</button></div>`}
    <div class="prose">
      ${(c.concept_marks || []).length ? `<section class="block"><h2>Concepts in this lesson</h2><ul class="marks">
        ${c.concept_marks.map((m) => `<li>${m.start != null ? `<button class="link jump" data-t="${m.start}">${fmt(m.start)}</button>` : `<span class="muted small">${m.covered ? "mentioned" : "not found"}</span>`}<span>${esc(m.concept)}</span></li>`).join("")}
      </ul></section>` : ""}
      ${(c.key_ideas || []).length ? `<section class="block"><h2>Key ideas</h2><div class="ideas">
        ${c.key_ideas.map((k) => `<div class="idea"><h3>${esc(k.title)}</h3>${md(k.body)}</div>`).join("")}
      </div></section>` : ""}
      ${c.diagram ? `<section class="block"><h2>The whole picture</h2><div class="diagram" id="diagram"></div></section>` : ""}
      ${c.worked_example ? `<section class="block"><h2>Worked example</h2>${md(c.worked_example)}</section>` : ""}
      ${(c.pitfalls || []).length ? `<section class="block"><h2>Common mistakes</h2><ul>${c.pitfalls.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></section>` : ""}
      ${(c.quiz || []).length ? `<section class="block quiz" id="quiz"><h2>Check your understanding</h2>${c.quiz.map(quizQ).join("")}<p id="quizscore" class="muted"></p></section>` : ""}
      ${c.check_yourself ? `<section class="block"><h2>Explain it out loud</h2><p class="checkq">${esc(c.check_yourself)}</p></section>` : ""}
      ${c.mode === "basic" ? `<p class="notice small">Built without AI: step titles come from the lesson's concepts and the text is the narrator's own words. With a local model or Claude, each step gets a plain-language explanation, plus key ideas, a diagram and a quiz.</p>` : ""}
    </div>
    <section class="block jury">
      <h2>Why this source</h2>
      <p class="muted">${candidates.length} videos made the shortlist. Each was scored out of 100 using the “${esc((state.me?.profiles || {})[course.profile] || course.profile)}” weighting, then scaled by how on-topic it is, so a popular video from another field can't win.${course.provider === "none" ? " Scored without AI: coverage comes from matching the lesson's concepts in the transcript, visuals from analyzing sampled frames, and viewer sentiment from comment phrases. Correctness can't be checked in this mode." : ""}</p>
      ${candidates.map(candRow).join("")}
      ${legend()}
    </section>
    <div class="lesson-nav">
      ${prev ? `<a class="btn ghost" href="#/lesson/${prev}">Previous lesson</a>` : "<span></span>"}
      <button id="done" class="${progress.completed ? "ghost" : ""}">${progress.completed ? "Marked as done" : "Mark as done"}</button>
      ${next ? `<a class="btn ghost" href="#/lesson/${next}">Next lesson</a>` : "<span></span>"}
    </div>
  </article></div>`;
  wireOutline();

  let cur = 0;
  const show = (i) => {
    if (!steps.length) return;
    cur = Math.max(0, Math.min(steps.length - 1, i));
    const st = steps[cur];
    document.getElementById("stage").innerHTML = st.clip
      ? `<video src="${esc(st.clip)}" poster="${esc(st.image)}" autoplay muted loop playsinline aria-label="${esc(st.title || "Animation")}"></video>`
      : `<img src="${esc(st.image)}" alt="${esc(st.title || `Step ${cur + 1}`)}">`;
    const body = st.explain
      ? `<p>${esc(st.explain)}</p>${st.text ? `<details><summary>What the narrator says</summary><p class="narr">${esc(st.text)}</p></details>` : ""}`
      : st.text ? `<p class="narr">${esc(st.text)}</p>` : "";
    document.getElementById("caption").innerHTML = `<h3>${esc(st.title || `Step ${cur + 1}`)}</h3>${body}
      <p class="small muted">At ${fmt(st.t)} in the source. <a href="${esc(ytAt(st.start))}" target="_blank" rel="noopener">Open this moment on YouTube</a></p>`;
    document.getElementById("counter").textContent = `Step ${cur + 1} of ${steps.length}`;
    document.getElementById("prevstep").disabled = cur === 0;
    document.getElementById("nextstep").disabled = cur === steps.length - 1;
    document.querySelectorAll(".filmstrip button").forEach((b, j) => {
      b.setAttribute("aria-selected", String(j === cur));
      if (j === cur) b.scrollIntoView({ block: "nearest", inline: "nearest" });
    });
  };
  if (steps.length) {
    document.getElementById("prevstep").onclick = () => show(cur - 1);
    document.getElementById("nextstep").onclick = () => show(cur + 1);
    document.querySelectorAll(".filmstrip button").forEach((b) => { b.onclick = () => show(+b.dataset.i); });
    state.keys = (e) => {
      if (/input|textarea|select/i.test(document.activeElement?.tagName || "")) return;
      if (e.key === "ArrowRight") show(cur + 1);
      if (e.key === "ArrowLeft") show(cur - 1);
    };
    window.addEventListener("keydown", state.keys);
    show(0);
  }
  document.querySelectorAll(".jump").forEach((b) => {
    b.onclick = () => {
      const t = +b.dataset.t;
      let idx = -1;
      steps.forEach((st, i) => { if (st.start <= t + 5) idx = i; });
      if (idx >= 0) { show(idx); document.getElementById("stage").scrollIntoView({ behavior: "smooth", block: "center" }); }
      else if (src.url) window.open(ytAt(t), "_blank", "noopener");
    };
  });
  const rb = document.getElementById("rebuild2");
  if (rb) rb.onclick = async () => { try { await api(`/api/lessons/${id}/rebuild`, { method: "POST" }); toast("Rebuilding lesson"); location.hash = `#/course/${course.id}`; } catch (ex) { toast(ex.message); } };
  if (c.diagram) renderDiagram(c.diagram);
  wireQuiz(c.quiz || [], id);

  document.getElementById("done").onclick = async () => {
    const nowDone = !progress.completed;
    try {
      await api(`/api/lessons/${id}/progress`, { method: "POST", body: { completed: nowDone } });
      if (nowDone && next) { toast("Lesson done"); location.hash = `#/lesson/${next}`; }
      else route();
    } catch (ex) { toast(ex.message); }
  };
}

function candRow(cd, i) {
  const j = cd.judge || {}, s = cd.signals || {};
  const pts = [...(j.strengths || []).map((x) => `<li>${esc(x)}</li>`), ...(j.weaknesses || []).map((x) => `<li class="muted">${esc(x)}</li>`)];
  return `<div class="cand ${i === 0 ? "pick" : ""}">
    <div class="score">${Math.round(cd.score)}<small>${i === 0 ? "the pick" : `#${cd.rank}`}</small></div>
    <div>
      <div class="title"><a href="${esc(cd.url)}" target="_blank" rel="noopener">${esc(cd.title)}</a></div>
      <div class="stats"><span>${esc(cd.channel)}</span><span>${fmt(cd.duration)}</span><span>${num(s.views)} views</span>
        ${s.like_rate_pct != null ? `<span>${s.like_rate_pct}% liked</span>` : ""}<span>${num(s.views_per_sub)}× channel size</span>
        ${s.relevance != null ? `<span class="${s.relevance < 0.5 ? "off" : ""}">${Math.round(s.relevance * 100)}% on-topic</span>` : ""}</div>
      <div class="bar-wrap" style="margin-top:.5rem">${scoreBar(cd.contributions)}</div>
      ${j.verdict ? `<p style="margin:.5rem 0 0">${esc(j.verdict)}</p>` : ""}
      ${i === 0 && pts.length ? `<ul>${pts.join("")}</ul>` : ""}
      ${i === 0 && (j.visual_notes || j.comment_notes) ? `<p class="small muted" style="margin:.4rem 0 0">${esc([j.visual_notes, j.comment_notes].filter(Boolean).join(" "))}</p>` : ""}
    </div></div>`;
}

function quizQ(q, qi) {
  return `<div class="q" data-q="${qi}"><p><strong>${qi + 1}. ${esc(q.q)}</strong></p>
    <div class="opts">${q.options.map((o, oi) => `<button data-o="${oi}">${esc(o)}</button>`).join("")}</div>
    <p class="why" hidden></p></div>`;
}

function wireQuiz(quiz, lessonId) {
  if (!quiz.length) return;
  let answered = 0, right = 0;
  document.querySelectorAll("#quiz .q").forEach((qe) => {
    const q = quiz[+qe.dataset.q];
    qe.querySelectorAll(".opts button").forEach((b) => {
      b.onclick = async () => {
        const pick = +b.dataset.o, ok = pick === q.answer;
        qe.querySelectorAll(".opts button").forEach((x) => {
          x.disabled = true;
          if (+x.dataset.o === q.answer) x.classList.add("right");
        });
        if (!ok) b.classList.add("wrong");
        const why = qe.querySelector(".why");
        why.hidden = false;
        why.textContent = (ok ? "Correct. " : "Not quite. ") + (q.why || "");
        answered += 1; if (ok) right += 1;
        if (answered === quiz.length) {
          document.getElementById("quizscore").textContent = `You got ${right} of ${quiz.length}.`;
          api(`/api/lessons/${lessonId}/progress`, { method: "POST", body: { quiz_score: right / quiz.length } }).catch(() => {});
        }
      };
    });
  });
}

async function renderDiagram(src) {
  const el = document.getElementById("diagram");
  if (!el) return;
  if (!window.mermaid) { el.innerHTML = `<pre>${esc(src)}</pre>`; return; }
  try {
    const dark = matchMedia("(prefers-color-scheme: dark)").matches;
    mermaid.initialize({ startOnLoad: false, theme: dark ? "dark" : "neutral", securityLevel: "strict", fontFamily: "Atkinson Hyperlegible, sans-serif" });
    const { svg } = await mermaid.render("mmd" + Date.now(), src);
    el.innerHTML = svg;
  } catch (_) {
    el.innerHTML = `<pre>${esc(src)}</pre>`;
    document.querySelectorAll("body > [id^='dmmd']").forEach((n) => n.remove());
  }
}


/* ---------- Settings ---------- */

async function viewSettings() {
  if (!state.me?.user?.is_owner) { app.innerHTML = `<p class="notice">Only the owner account can change settings. <a href="#/">Back to your courses</a></p>`; return; }
  const { settings: st, ffmpeg } = await api("/api/settings");
  const secret = (k, label, help) => `
    <div class="field"><label for="${k}">${label}</label>
      <input id="${k}" type="password" autocomplete="off" placeholder="${st[k].set ? `Saved (${esc(st[k].hint)}). Type a new key to replace it.` : "Paste a key"}">
      <p class="hint">${help}${st[k].set ? ` Saved ${st[k].source === "env" ? "in .env" : "in the app"}.` : ""}
      ${st[k].set && st[k].source === "app" ? ` <button type="button" class="link" data-clear="${k}">Remove key</button>` : ""}</p></div>`;
  const text = (k, label, help) => `
    <div class="field"><label for="${k}">${label}</label><input id="${k}" value="${esc(st[k].value)}"><p class="hint">${help}</p></div>`;
  app.innerHTML = `<div class="settings">
    <h1>Settings</h1>
    <p class="muted">Keys are stored on this server, in its own database, and are never shown in full again. Everything works without them in Basic mode.</p>
    ${ffmpeg ? "" : `<p class="notice">ffmpeg isn't installed, so visuals can't be extracted. Rerun the one-liner to install it.</p>`}
    <form id="setform">
      <section class="block"><h2>Default ranking engine</h2>
        <div class="field"><select id="AI_PROVIDER">
          ${[["none", "Basic, no AI"], ["ollama", "Local model (Ollama)"], ["anthropic", "Claude"]].map(([v, l]) => `<option value="${v}" ${st.AI_PROVIDER.value === v ? "selected" : ""}>${l}</option>`).join("")}
        </select><p class="hint">Preselected when you build a course. Choosing Local model and rerunning the one-liner installs Ollama and downloads the models.</p></div>
      </section>
      <section class="block"><h2>Claude</h2>
        ${secret("ANTHROPIC_API_KEY", "Anthropic API key", "From console.anthropic.com. Enables the Claude engine.")}
        ${text("BESTTAKE_MODEL", "Model", "The model used for planning, judging and writing.")}
        <button type="button" class="ghost" data-test="anthropic">Test Claude</button> <span class="hint" id="t-anthropic"></span>
      </section>
      <section class="block"><h2>YouTube</h2>
        ${secret("YOUTUBE_API_KEY", "YouTube Data API key (optional)", "From Google Cloud console. Makes search faster and more reliable; without it BestTake searches through yt-dlp.")}
        <button type="button" class="ghost" data-test="youtube">Test YouTube</button> <span class="hint" id="t-youtube"></span>
      </section>
      <section class="block"><h2>Local model</h2>
        ${text("OLLAMA_URL", "Ollama address", "Where Ollama runs. The default is this Mac.")}
        ${text("OLLAMA_MODEL", "Text model", "Plans, judges and writes. qwen2.5:14b fits comfortably on an M4 Pro.")}
        ${text("OLLAMA_VISION_MODEL", "Vision model", "Rates how visual each candidate is from its frames.")}
        <button type="button" class="ghost" data-test="ollama">Test local model</button> <span class="hint" id="t-ollama"></span>
      </section>
      <p class="error" id="seterr"></p>
      <button type="submit">Save settings</button>
    </form></div>`;
  const keys = ["AI_PROVIDER", "ANTHROPIC_API_KEY", "BESTTAKE_MODEL", "YOUTUBE_API_KEY", "OLLAMA_URL", "OLLAMA_MODEL", "OLLAMA_VISION_MODEL"];
  const clear = new Set();
  const collect = () => Object.fromEntries(keys.map((k) => [k, document.getElementById(k).value]));
  document.querySelectorAll("[data-clear]").forEach((b) => {
    b.onclick = () => { clear.add(b.dataset.clear); b.closest(".hint").innerHTML = "The key will be removed when you save."; };
  });
  document.getElementById("setform").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/api/settings", { method: "POST", body: { values: collect(), clear: [...clear] } });
      await loadMe();
      toast("Settings saved");
      route();
    } catch (ex) { document.getElementById("seterr").textContent = ex.message; }
  };
  document.querySelectorAll("[data-test]").forEach((b) => {
    b.onclick = async () => {
      const out = document.getElementById("t-" + b.dataset.test);
      out.textContent = "Saving and testing…";
      try {
        await api("/api/settings", { method: "POST", body: { values: collect(), clear: [] } });
        const r = await api("/api/settings/test", { method: "POST", body: { target: b.dataset.test } });
        out.textContent = r.message;
        out.className = "hint " + (r.ok ? "ok" : "bad");
      } catch (ex) { out.textContent = ex.message; out.className = "hint bad"; }
    };
  });
}

/* ---------- Router ---------- */

async function route() {
  clearTimeout(state.poll);
  if (state.keys) { window.removeEventListener("keydown", state.keys); state.keys = null; }
  const h = location.hash || "#/";
  try {
    if (h.startsWith("#/signin")) return viewSignin("signin");
    if (h.startsWith("#/signup")) return viewSignin("signup");
    if (!state.me) await loadMe();
    if (!state.me) { location.hash = "#/signin"; return; }
    let m;
    if ((m = h.match(/^#\/course\/(\d+)/))) return await viewCourse(+m[1]);
    if ((m = h.match(/^#\/lesson\/(\d+)/))) return await viewLesson(+m[1]);
    if (h.startsWith("#/settings")) return await viewSettings();
    return await viewHome();
  } catch (ex) {
    if (state.me) app.innerHTML = `<p class="notice">${esc(ex.message)} <a href="#/">Back to your courses</a></p>`;
  }
}

let lastHash = location.hash;
window.addEventListener("hashchange", () => {
  const changed = location.hash !== lastHash;
  lastHash = location.hash;
  route().then(() => { if (changed) { window.scrollTo(0, 0); app.focus({ preventScroll: true }); } });
});
route();
