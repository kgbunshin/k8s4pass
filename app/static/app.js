(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const VIEWS = ["start", "wait", "exam", "result"];
  const store = {
    get() { try { return localStorage.getItem("ckad-sid"); } catch { return null; } },
    set(v) { try { v ? localStorage.setItem("ckad-sid", v) : localStorage.removeItem("ckad-sid"); } catch { /* no storage available */ } },
  };

  let sid = store.get();
  let session = null;
  let clockOffset = 0;
  let pollTimer = null;
  let tickTimer = null;
  let confirmTimer = null;
  let term = null;
  let fit = null;
  let sock = null;
  let examBuilt = false;
  let current = 0;          // question open in the panel
  let renderedKey = null;   // avoids rebuilding the panel (and losing scroll) on every poll
  let regrading = false;
  let expiryPolled = false;

  // ---- utilities ------------------------------------------------------------

  async function api(path, opts = {}) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    const body = res.status === 204 ? {} : await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(body.detail || res.statusText);
      err.status = res.status;
      throw err;
    }
    return body;
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  const inline = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

  // Minimal Markdown: paragraphs, lists, ``` blocks and `code` / **bold**. Everything is escaped first.
  function md(text) {
    const out = [];
    let list = null;
    let fence = null;
    const flush = () => { if (list) { out.push(`<${list.tag}>${list.items.join("")}</${list.tag}>`); list = null; } };
    for (const raw of text.split("\n")) {
      if (raw.startsWith("```")) {
        if (fence) { out.push(`<pre><code>${esc(fence.join("\n"))}</code></pre>`); fence = null; }
        else { flush(); fence = []; }
        continue;
      }
      if (fence) { fence.push(raw); continue; }
      const line = raw.trimEnd();
      let m;
      if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
        if (!list || list.tag !== "ul") { flush(); list = { tag: "ul", items: [] }; }
        list.items.push(`<li>${inline(m[1])}</li>`);
      } else if ((m = line.match(/^\s*\d+\.\s+(.*)$/))) {
        if (!list || list.tag !== "ol") { flush(); list = { tag: "ol", items: [] }; }
        list.items.push(`<li>${inline(m[1])}</li>`);
      } else if (!line.trim()) {
        flush();
      } else {
        flush();
        out.push(`<p>${inline(line)}</p>`);
      }
    }
    if (fence) out.push(`<pre><code>${esc(fence.join("\n"))}</code></pre>`);
    flush();
    return out.join("");
  }

  function show(name) {
    VIEWS.forEach((v) => { $("view-" + v).hidden = v !== name; });
    $("finish").hidden = name !== "exam";
    $("timer").hidden = name !== "exam";
  }

  function banner(text) {
    $("banner").textContent = text || "";
    $("banner").hidden = !text;
  }

  function setMsg(text, isErr = false) {
    $("start-msg").textContent = text || "";
    $("start-msg").classList.toggle("err", isErr);
  }

  // ---- session lifecycle -------------------------------------------------------

  function forget() {
    clearTimeout(pollTimer);
    clearInterval(tickTimer);
    closeSocket();
    if (term) { term.dispose(); term = null; fit = null; }
    examBuilt = false;
    renderedKey = null;
    current = 0;
    session = null;
    sid = null;
    store.set(null);
  }

  async function poll() {
    clearTimeout(pollTimer);
    if (!sid) return;
    let s;
    try {
      s = await api("/api/sessions/" + sid);
    } catch (e) {
      if (e.status === 404) {
        forget();
        show("start");
        setMsg("The previous session no longer exists (the server was restarted or it expired).", true);
        return;
      }
      pollTimer = setTimeout(poll, 3000); // network error: try again
      return;
    }
    render(s);
    if (s.status === "provisioning") pollTimer = setTimeout(poll, 1500);
    else if (s.status === "ready" || s.status === "grading" || (s.status === "finished" && s.alive)) {
      pollTimer = setTimeout(poll, 5000);
    }
  }

  function render(s) {
    session = s;
    expiryPolled = false;
    clockOffset = s.server_time - Date.now() / 1000;
    switch (s.status) {
      case "provisioning":
        show("wait");
        $("wait-step").textContent = s.step;
        break;
      case "ready":
      case "grading":
        enterExam(s);
        break;
      case "finished":
        if (s.alive) enterExam(s); // review window: the environment and the terminal keep running
        else showResult(s);
        break;
      case "failed":
        forget();
        show("start");
        setMsg("Failed to create the environment: " + (s.error || "unknown error"), true);
        break;
    }
  }

  // ---- exam screen ---------------------------------------------------------

  function enterExam(s) {
    show("exam");
    const review = s.status === "finished";
    $("finish").hidden = review;
    if (!examBuilt) {
      examBuilt = true;
      $("qnav").onclick = (ev) => {
        const b = ev.target.closest(".qtab");
        if (b) { selectQuestion(Number(b.dataset.i)); $("qbody").scrollTop = 0; }
      };
      current = 0;
      initTerminal();
      connect();
      clearInterval(tickTimer);
      tickTimer = setInterval(tick, 1000);
    }
    const key = review ? "review:" + s.result.attempt : "exam";
    if (key !== renderedKey) {
      renderedKey = key;
      renderNav();
      renderReview();
      selectQuestion(current);
    }
    tick();
  }

  const resultOf = (qid) => (session.status === "finished" && session.result
    ? session.result.questions.find((x) => x.id === qid) : null);

  function renderNav() {
    $("qnav").innerHTML = session.questions.map((q, i) => {
      const r = resultOf(q.id);
      const cls = r ? (r.points === r.total ? "done" : "todo") : "";
      return `<button class="qtab ${cls}" data-i="${i}">${i + 1}<small>${r ? `${r.points}/${r.total}` : `${q.points} pts`}</small></button>`;
    }).join("");
  }

  // Review bar (current score, history and actions) above the tabs, beside the terminal.
  function renderReview() {
    const box = $("review");
    const r = session.status === "finished" ? session.result : null;
    box.hidden = !r;
    if (!r) return;
    const later = r.history.slice(1).map((h) => `${h.percent}%`).join(" → ");
    box.innerHTML =
      `<div class="rhead">
         <div class="big">${r.score}/${r.total}</div>
         <div><span class="badge ${r.passed ? "pass" : "fail"}">${r.passed ? "Passed" : "Failed"}</span>
         <span class="muted small">${r.percent}% · pass mark 66%</span></div>
         <div class="actions">
           <button class="btn primary" data-regrade="">Regrade all</button>
           <button class="btn ghost" data-action="again">New session</button>
         </div>
       </div>
       <p class="muted small">Exam score (1st grading): ${r.history[0].score}/${r.history[0].total} · ${r.history[0].percent}%${later ? ` · regrades: ${later}` : ""}.
       The environment is still running: pick a question, fix it in the terminal beside this panel and regrade.</p>`;
  }

  function checksHtml(r) {
    return `<ul class="checks">${r.checks.map((c) =>
      `<li class="${c.ok ? "ok" : "no"}">${esc(c.description)} <span class="muted">(${c.points})</span></li>`).join("")}</ul>`;
  }

  function solutionsHtml(r) {
    return Object.entries(r.solutions).map(([name, script]) =>
      `<details><summary>Solution: ${esc(name)}</summary><pre><code>${esc(script)}</code></pre></details>`).join("");
  }

  function selectQuestion(i) {
    current = i;
    const q = session.questions[i];
    const r = resultOf(q.id);
    document.querySelectorAll(".qtab").forEach((b) => b.classList.toggle("active", Number(b.dataset.i) === i));
    $("qbody").innerHTML =
      `<h2>Question ${i + 1} <span class="pts">${r ? `${r.points}/${r.total}` : q.points} points</span></h2>` +
      `<p class="tags">${esc(q.domain)} · ${esc(q.topic)} · difficulty ${esc(q.difficulty)}</p>` +
      (r ? `<div class="rbox ${r.points === r.total ? "pass" : "fail"}">${checksHtml(r)}
              <button class="btn" data-regrade="${esc(q.id)}"${regrading ? " disabled" : ""}>Regrade this question</button>
            </div><h3 class="sub">Statement</h3>` : "") +
      md(q.statement) +
      (r ? `<h3 class="sub">Solutions</h3>${solutionsHtml(r)}` : "");
  }

  async function regrade(qid, clicked) {
    if (regrading) return;
    regrading = true;
    lockRegrade(true, clicked);
    banner("");
    try {
      render(await api(`/api/sessions/${sid}/regrade`, { method: "POST", body: JSON.stringify({ question: qid || null }) }));
    } catch (e) {
      banner("Failed to regrade: " + e.message);
    } finally {
      regrading = false;
      lockRegrade(false);
    }
  }

  function lockRegrade(on, clicked) {
    document.querySelectorAll("[data-regrade]").forEach((b) => {
      b.disabled = on;
      if (on) { b.dataset.label = b.textContent; if (b === clicked) b.textContent = "Regrading…"; }
      else if (b.dataset.label) { b.textContent = b.dataset.label; delete b.dataset.label; }
    });
  }

  const clock = (left) => {
    const p = (n) => String(n).padStart(2, "0");
    return `${p(Math.floor(left / 3600))}:${p(Math.floor((left % 3600) / 60))}:${p(left % 60)}`;
  };

  function tick() {
    if (!session) return;
    if (session.status === "grading") { $("timer").textContent = "grading…"; return; }
    const review = session.status === "finished";
    const deadline = review ? session.review_expires_at : session.expires_at;
    if (deadline == null) return;
    const left = Math.max(0, Math.round(deadline - (Date.now() / 1000 + clockOffset)));
    $("timer").textContent = (review ? "review " : "") + clock(left);
    $("timer").classList.toggle("low", left < 300);
    if (left === 0 && !expiryPolled) { // only once: grading/teardown is done by the reaper on the server
      expiryPolled = true;
      clearTimeout(pollTimer);
      pollTimer = setTimeout(poll, 2000);
    }
  }

  // ---- terminal --------------------------------------------------------------

  function initTerminal() {
    term = new Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
      theme: { background: "#0d1117" },
    });
    fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open($("terminal"));
    fit.fit();
    const enc = new TextEncoder();
    term.onData((d) => { if (sock && sock.readyState === WebSocket.OPEN) sock.send(enc.encode(d)); });
    term.onResize(sendResize);
    new ResizeObserver(() => { try { fit && fit.fit(); } catch { /* terminal already disposed */ } }).observe($("terminal"));
  }

  function sendResize() {
    if (sock && sock.readyState === WebSocket.OPEN && term) {
      sock.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
    }
  }

  function overlay(text) {
    $("term-overlay").hidden = !text;
    $("term-overlay-msg").textContent = text || "";
  }

  function closeSocket() {
    if (sock) { sock.onclose = null; sock.close(); sock = null; }
  }

  function connect() {
    if (!sid || (sock && sock.readyState <= WebSocket.OPEN)) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    sock = new WebSocket(`${proto}://${location.host}/ws/terminal/${sid}?cols=${term.cols}&rows=${term.rows}`);
    sock.binaryType = "arraybuffer";
    sock.onopen = () => { overlay(null); sendResize(); term.focus(); };
    sock.onmessage = (ev) => term.write(new Uint8Array(ev.data));
    sock.onclose = () => overlay("Terminal disconnected.");
  }

  // ---- result -------------------------------------------------------------

  function showResult(s) {
    clearTimeout(pollTimer);
    clearInterval(tickTimer);
    closeSocket();
    if (term) { term.dispose(); term = null; fit = null; }
    examBuilt = false;
    renderedKey = null;
    const r = s.result;
    show("result");
    $("result").innerHTML =
      `<div class="summary">
         <div class="big">${r.score}/${r.total}</div>
         <div><span class="badge ${r.passed ? "pass" : "fail"}">${r.passed ? "Passed" : "Failed"}</span>
         <p class="muted">${r.percent}% · pass mark 66% · environment shut down</p></div>
         <span class="spacer"></span>
         <button data-action="again" class="btn primary">New session</button>
       </div>` +
      r.questions.map((q, i) =>
        `<div class="rq">
           <h3><span>Question ${i + 1} <span class="muted">· ${esc(q.topic)}</span></span><span>${q.points}/${q.total}</span></h3>
           ${checksHtml(q)}${solutionsHtml(q)}
         </div>`).join("");
  }

  async function newSession() {
    const old = sid;
    forget();
    if (old) { try { await api("/api/sessions/" + old, { method: "DELETE" }); } catch { /* already removed */ } }
    banner("");
    show("start");
  }

  // ---- actions -----------------------------------------------------------------

  $("start").addEventListener("click", async () => {
    setMsg("");
    $("start").disabled = true;
    try {
      const s = await api("/api/sessions", {
        method: "POST",
        body: JSON.stringify({ domain: $("domain").value || null, count: Number($("count").value) || 1 }),
      });
      sid = s.id;
      store.set(sid);
      render(s);
      poll();
    } catch (e) {
      setMsg(e.message, true);
    } finally {
      $("start").disabled = false;
    }
  });

  document.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-regrade], [data-action]");
    if (!b) return;
    if (b.dataset.action === "again") newSession();
    else if (b.dataset.regrade !== undefined) regrade(b.dataset.regrade, b);
  });

  $("cancel").addEventListener("click", newSession);
  $("reconnect").addEventListener("click", connect);

  // Two-step confirmation on the button itself (no window.confirm).
  $("finish").addEventListener("click", async () => {
    const btn = $("finish");
    if (!btn.classList.contains("confirm")) {
      btn.classList.add("confirm");
      btn.textContent = "Confirm grading?";
      confirmTimer = setTimeout(() => { btn.classList.remove("confirm"); btn.textContent = "Finish and grade"; }, 5000);
      return;
    }
    clearTimeout(confirmTimer);
    btn.disabled = true;
    btn.textContent = "Grading…";
    banner("");
    try {
      render(await api(`/api/sessions/${sid}/finish`, { method: "POST" }));
    } catch (e) {
      banner("Failed to grade: " + e.message);
    } finally {
      btn.disabled = false;
      btn.classList.remove("confirm");
      btn.textContent = "Finish and grade";
    }
  });

  // ---- startup ----------------------------------------------------------------

  async function init() {
    show("start");
    try {
      const cat = await api("/api/catalog");
      const total = cat.domains.reduce((n, d) => n + d.count, 0);
      $("domain").innerHTML =
        `<option value="all">All domains (${total} questions)</option>` +
        cat.domains.map((d) => `<option value="${esc(d.domain)}">${esc(d.domain)} (${d.count})</option>`).join("");
      $("count").max = String(Math.max(1, total));
    } catch (e) {
      setMsg("Could not load the catalog: " + e.message, true);
    }
    if (sid) poll();
  }

  init();
})();
