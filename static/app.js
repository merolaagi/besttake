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

const STATUS_TEXT = {
  queued: "Waiting to start", planning: "Planning lessons", building: "Finding the best videos",
  ready: "Ready", partial: "Ready, some lessons need a retry", failed: "Stopped", interrupted: "Paused",
};
const LESSON_TEXT = {
  pending: "Waiting", searching: "Searching", judging: "Judging", writing: "Writing notes", ready: "Ready", failed: "Failed",
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
  nav.innerHTML = `<span class="plan">${esc(plan)}</span><a href="#/">My courses</a><button class="link" id="signout">Sign out</button>`;
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
  const [{ courses }] = await Promise.all([api("/api/courses"), loadMe()]);
  const me = state.me;
  const profiles = Object.entries(me.profiles || { balanced: "Balanced" });
  app.innerHTML = `
  ${me.api_key_configured ? "" : `<div class="notice">The server has no Anthropic API key yet. Add <code>ANTHROPIC_API_KEY</code> to <code>.env</code> and restart BestTake to build courses.</div>`}
  <form class="builder" id="builder">
    <h1>What do you want to learn?</h1>
    <div class="field"><label for="topic" class="muted">Topic</label>
      <input id="topic" class="topic" placeholder="System design: low-level and high-level design" required maxlength="200"></div>
    <div class="row3">
      <div class="field"><label for="level">Starting point</label>
        <select id="level"><option value="beginner">New to this</option><option value="intermediate">Some background</option><option value="advanced">Experienced</option></select></div>
      <div class="field"><label for="depth">Length</label>
        <select id="depth"><option value="quick">Quick, 6 lessons</option><option value="standard" selected>Standard, 12 lessons</option><option value="deep">Deep, 20 lessons</option></select></div>
      <div class="field"><label for="profile">What makes a video best</label>
        <select id="profile">${profiles.map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("")}</select></div>
    </div>
    <div class="field"><label for="goal">Goal <span class="muted">(optional)</span></label>
      <input id="goal" placeholder="Pass system design interviews at senior level" maxlength="500"></div>
    <p class="error" id="builderr"></p>
    <div class="actions"><button type="submit" id="buildbtn">Build course</button>
      <span class="muted small">Building takes a few minutes. You can start lessons as soon as they're ready.</span></div>
  </form>
  <h2>Your courses</h2>
  ${courses.length ? `<ul class="courses">${courses.map(courseRow).join("")}</ul>` : `<p class="empty">No courses yet. Enter a topic above to build your first one.</p>`}`;
  document.getElementById("builder").onsubmit = async (e) => {
    e.preventDefault();
    const btn = document.getElementById("buildbtn"), err = document.getElementById("builderr");
    err.textContent = ""; btn.disabled = true;
    try {
      const r = await api("/api/courses", { method: "POST", body: {
        topic: document.getElementById("topic").value, level: document.getElementById("level").value,
        depth: document.getElementById("depth").value, profile: document.getElementById("profile").value,
        goal: document.getElementById("goal").value,
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
    <span><span class="ct">${esc(c.title || c.topic)}</span><br><span class="status ${live ? "live" : ""}">${esc(STATUS_TEXT[c.status] || c.status)}</span></span>
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
      <p class="status ${live ? "live" : ""}">${esc(STATUS_TEXT[course.status] || course.status)}${lessons.length ? `, ${lessons.filter((l) => l.status === "ready").length} of ${lessons.length} lessons built` : ""}</p>
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

  const v = c.video;
  const seg0 = c.watch[0];
  app.innerHTML = `<div class="split">${outlineHtml(course, outline, lesson.id)}
  <article>
    ${head}
    <div class="player"><iframe id="player" title="${esc(v.title)}" allow="accelerometer; autoplay; encrypted-media; gyroscope; picture-in-picture; fullscreen" allowfullscreen src="${embedUrl(v.id, seg0)}"></iframe></div>
    <p class="credit">${esc(v.title)} by ${esc(v.channel)}. <a href="https://www.youtube.com/watch?v=${esc(v.id)}" target="_blank" rel="noopener">Open on YouTube</a></p>
    <div class="segments" role="group" aria-label="Parts to watch">
      ${c.watch.map((w, i) => `<button data-i="${i}" aria-pressed="${i === 0}"><b>${fmt(w.start)}–${fmt(w.end)}</b>${esc(w.label)}</button>`).join("")}
    </div>
    <div class="prose">
      ${c.hook ? `<p class="hook">${esc(c.hook)}</p>` : ""}
      <section class="block"><h2>Key ideas</h2><div class="ideas">
        ${c.key_ideas.map((k) => `<div class="idea"><h3>${esc(k.title)}</h3>${md(k.body)}</div>`).join("")}
      </div></section>
      ${c.diagram ? `<section class="block"><h2>The picture</h2><div class="diagram" id="diagram"></div></section>` : ""}
      ${c.worked_example ? `<section class="block"><h2>Worked example</h2>${md(c.worked_example)}</section>` : ""}
      ${c.pitfalls.length ? `<section class="block"><h2>Common mistakes</h2><ul>${c.pitfalls.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></section>` : ""}
      ${c.quiz.length ? `<section class="block quiz" id="quiz"><h2>Check your understanding</h2>${c.quiz.map(quizQ).join("")}<p id="quizscore" class="muted"></p></section>` : ""}
      ${c.check_yourself ? `<section class="block"><h2>Explain it out loud</h2><p class="checkq">${esc(c.check_yourself)}</p></section>` : ""}
    </div>
    <section class="block jury">
      <h2>Why this video</h2>
      <p class="muted">${candidates.length} videos made the shortlist. Each was scored out of 100 using the “${esc((state.me?.profiles || {})[course.profile] || course.profile)}” weighting.</p>
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

  document.querySelectorAll(".segments button").forEach((b) => {
    b.onclick = () => {
      const w = c.watch[+b.dataset.i];
      document.getElementById("player").src = embedUrl(v.id, w, true);
      document.querySelectorAll(".segments button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    };
  });

  if (c.diagram) renderDiagram(c.diagram);
  wireQuiz(c.quiz, id);

  document.getElementById("done").onclick = async (e) => {
    const nowDone = !progress.completed;
    try {
      await api(`/api/lessons/${id}/progress`, { method: "POST", body: { completed: nowDone } });
      if (nowDone && next) { toast("Lesson done"); location.hash = `#/lesson/${next}`; }
      else route();
    } catch (ex) { toast(ex.message); }
  };
}

function embedUrl(vid, seg, autoplay = false) {
  const p = new URLSearchParams({ rel: "0", modestbranding: "1" });
  if (seg) { p.set("start", String(seg.start)); if (seg.end > seg.start) p.set("end", String(seg.end)); }
  if (autoplay) p.set("autoplay", "1");
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(vid)}?${p}`;
}

function candRow(cd, i) {
  const j = cd.judge || {}, s = cd.signals || {};
  const pts = [...(j.strengths || []).map((x) => `<li>${esc(x)}</li>`), ...(j.weaknesses || []).map((x) => `<li class="muted">${esc(x)}</li>`)];
  return `<div class="cand ${i === 0 ? "pick" : ""}">
    <div class="score">${Math.round(cd.score)}<small>${i === 0 ? "the pick" : `#${cd.rank}`}</small></div>
    <div>
      <div class="title"><a href="${esc(cd.url)}" target="_blank" rel="noopener">${esc(cd.title)}</a></div>
      <div class="stats"><span>${esc(cd.channel)}</span><span>${fmt(cd.duration)}</span><span>${num(s.views)} views</span>
        ${s.like_rate_pct != null ? `<span>${s.like_rate_pct}% liked</span>` : ""}<span>${num(s.views_per_sub)}× channel size</span></div>
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

/* ---------- Router ---------- */

async function route() {
  clearTimeout(state.poll);
  const h = location.hash || "#/";
  try {
    if (h.startsWith("#/signin")) return viewSignin("signin");
    if (h.startsWith("#/signup")) return viewSignin("signup");
    if (!state.me) await loadMe();
    if (!state.me) { location.hash = "#/signin"; return; }
    let m;
    if ((m = h.match(/^#\/course\/(\d+)/))) return await viewCourse(+m[1]);
    if ((m = h.match(/^#\/lesson\/(\d+)/))) return await viewLesson(+m[1]);
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
