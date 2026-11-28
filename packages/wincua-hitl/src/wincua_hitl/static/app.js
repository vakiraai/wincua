"use strict";

(function () {
  const params = new URLSearchParams(location.search);
  let token = params.get("token") || sessionStorage.getItem("wincua-token") || "";
  if (params.get("token")) {
    sessionStorage.setItem("wincua-token", token);
    history.replaceState(null, "", location.pathname);
  }

  const $ = (id) => document.getElementById(id);
  let current = null;
  let shotUrl = null;
  let busy = false;
  let lastSeq = -1;

  const KIND_LABEL = {
    question: "Question",
    confirm: "Confirm risky action",
    next_step: "Pick the next step",
    takeover: "You have control",
  };

  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") n.className = v;
      else if (k === "on") for (const [ev, fn] of Object.entries(v)) n.addEventListener(ev, fn);
      else n.setAttribute(k, v);
    }
    for (const kid of kids) n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    return n;
  }

  async function api(path, opts) {
    const res = await fetch(path, {
      ...opts,
      headers: { "X-Wincua-Token": token, ...(opts && opts.body ? { "Content-Type": "application/json" } : {}) },
      cache: "no-store",
    });
    return res;
  }

  function setConn(state, text) {
    $("conn").className = "dot " + state;
    $("conn-text").textContent = text;
  }

  async function poll() {
    try {
      const res = await api("/api/state");
      if (res.status === 401) { setConn("down", "invalid token — reopen the URL from the terminal"); return; }
      if (!res.ok) throw new Error(res.status);
      const st = await res.json();
      setConn("live", st.queued ? `connected · ${st.queued} waiting` : "connected");
      renderHistory(st.history || []);
      if (st.seq !== lastSeq) {
        lastSeq = st.seq;
        await render(st.pending);
      }
    } catch (e) {
      setConn("down", "agent not reachable");
    } finally {
      setTimeout(poll, 600);
    }
    tick();
  }

  function tick() {
    if (!current || !current.timeout_s) { $("req-timer").textContent = ""; return; }
    const left = Math.max(0, Math.round(current.created_at + current.timeout_s - Date.now() / 1000));
    $("req-timer").textContent = `fails closed in ${left}s`;
  }

  async function render(req) {
    const prevId = current && current.id;
    current = req;
    $("req-error").hidden = true;
    if (!req) {
      $("idle").hidden = false;
      $("req").hidden = true;
      return;
    }
    $("idle").hidden = true;
    $("req").hidden = false;
    if (req.id === prevId) return;

    const allowed = new Set(req.allowed);
    const kind = $("req-kind");
    kind.textContent = KIND_LABEL[req.kind] || req.kind;
    kind.className = "badge " + (req.risk === "credential" ? "credential" : req.kind);
    $("req-task").textContent = req.task ? "Task: " + req.task : "";
    $("req-question").textContent = req.question || "";
    $("req-reason").hidden = !req.reason;
    $("req-reason").textContent = req.reason ? "Why: " + req.reason : "";
    $("req-action").hidden = !req.action;
    $("req-action").textContent = req.action ? `${req.action.tool}  ${JSON.stringify(req.action.args, null, 2)}` : "";

    const opts = $("opts");
    opts.replaceChildren();
    if (req.kind === "question") {
      for (const o of req.options || []) {
        opts.append(el("button", { class: "btn option", on: { click: () => send({ kind: "answer", text: o }) } }, o));
      }
    }

    $("confirm-row").hidden = req.kind !== "confirm";

    const showInstr = req.kind === "question" || req.kind === "next_step";
    $("instr-form").hidden = !showInstr;
    const input = $("instr");
    input.value = "";
    input.type = req.risk === "credential" ? "password" : "text";
    $("instr-label").textContent = req.kind === "question" ? "Your answer" : "Instruction or command";
    input.placeholder = req.kind === "question"
      ? "Type your answer"
      : "e.g. One · invoke auto:num1Button · type 23 · key enter";
    $("instr-help").textContent = req.kind === "next_step"
      ? "Type a button name (One) or a command: invoke <id|name> · click <id|x y> · type [@id] <text> · key <keys> · wait <s> · launch <app> · focus <title>. Anything else is passed to the planner as an instruction."
      : "";

    $("suggest-wrap").hidden = req.kind !== "next_step" || !(req.suggestions || []).length;
    $("suggest-filter").value = "";
    renderSuggestions();

    $("btn-takeover").hidden = !allowed.has("takeover");
    $("btn-resume").hidden = !allowed.has("resume");
    $("btn-done").hidden = !allowed.has("done");
    $("btn-abort").hidden = !allowed.has("abort");

    renderObservation(req.observation);
    await loadShot(req);
    if (showInstr) input.focus();
  }

  function renderSuggestions() {
    const box = $("suggest");
    box.replaceChildren();
    if (!current) return;
    const q = $("suggest-filter").value.toLowerCase();
    (current.suggestions || []).forEach((s) => {
      if (q && !(s.label + " " + JSON.stringify(s.args)).toLowerCase().includes(q)) return;
      const b = el("button", { class: "btn sug" + (s.needs_text ? " type" : ""), title: `${s.tool} ${JSON.stringify(s.args)}` }, s.label);
      b.addEventListener("click", () => {
        if (s.needs_text) {
          const input = $("instr");
          input.value = `type @${s.args.element_id} `;
          input.focus();
          return;
        }
        send({ kind: "action", action: { tool: s.tool, args: s.args } });
      });
      box.append(b);
    });
  }

  function renderObservation(obs) {
    const app = $("obs-app");
    const errs = $("obs-errors");
    const body = $("obs-table").querySelector("tbody");
    app.replaceChildren();
    errs.replaceChildren();
    body.replaceChildren();
    $("capture").textContent = obs && obs.capture_quality ? "capture: " + obs.capture_quality : "";
    if (!obs) { $("obs-count").textContent = "(none)"; return; }
    for (const [k, v] of Object.entries(obs.app || {})) {
      app.append(el("span", { class: "k" }, k), el("span", {}, typeof v === "object" ? JSON.stringify(v) : String(v)));
    }
    for (const e of obs.errors || []) errs.append(el("div", { class: "errline" }, `⚠ ${e.code}: ${e.message}`));
    const rows = obs.uia || [];
    $("obs-count").textContent = `· ${rows.length}${obs.uia_total > rows.length ? " of " + obs.uia_total : ""} elements`;
    const q = $("obs-filter").value.toLowerCase();
    for (const r of rows) {
      if (q && !`${r.element_id} ${r.role} ${r.name}`.toLowerCase().includes(q)) continue;
      const cell = el("td");
      if (current && current.kind === "next_step") {
        cell.append(el("button", {
          class: "btn mini",
          on: { click: () => send({ kind: "action", action: { tool: "invoke", args: { element_id: r.element_id } } }) },
        }, "invoke"));
      }
      body.append(el("tr", {}, el("td", { class: "id" }, r.element_id), el("td", {}, r.role), el("td", {}, r.name), cell));
    }
  }

  async function loadShot(req) {
    const img = $("shot");
    if (shotUrl) { URL.revokeObjectURL(shotUrl); shotUrl = null; }
    if (!req.has_screenshot) { img.hidden = true; $("shot-empty").hidden = false; return; }
    try {
      const res = await api("/api/screenshot/" + encodeURIComponent(req.id));
      if (!res.ok) throw new Error(res.status);
      shotUrl = URL.createObjectURL(await res.blob());
      img.src = shotUrl;
      img.hidden = false;
      $("shot-empty").hidden = true;
    } catch (e) {
      img.hidden = true;
      $("shot-empty").hidden = false;
    }
  }

  function renderHistory(items) {
    const ol = $("history");
    ol.replaceChildren();
    for (const h of items.slice().reverse().slice(0, 12)) {
      const what = h.chosen ? `${h.chosen.tool} ${JSON.stringify(h.chosen.args)}` : (h.text || "");
      ol.append(el("li", {}, el("b", {}, h.response), " ", h.kind === "confirm" && h.action ? h.action.tool + " " : "", what));
    }
  }

  async function send(payload) {
    if (!current || busy) return;
    busy = true;
    document.querySelectorAll("#req button").forEach((b) => (b.disabled = true));
    try {
      const res = await api("/api/respond/" + encodeURIComponent(current.id), { method: "POST", body: JSON.stringify(payload) });
      const out = await res.json().catch(() => ({}));
      if (!res.ok) {
        $("req-error").textContent = out.error || "request failed";
        $("req-error").hidden = false;
      }
    } catch (e) {
      $("req-error").textContent = "agent not reachable";
      $("req-error").hidden = false;
    } finally {
      busy = false;
      document.querySelectorAll("#req button").forEach((b) => (b.disabled = false));
    }
  }

  $("instr-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const text = $("instr").value;
    if (!current || !text.trim()) return;
    send({ kind: current.kind === "question" ? "answer" : "instruction", text });
  });
  $("btn-approve").addEventListener("click", () => send({ kind: "approve" }));
  $("btn-reject").addEventListener("click", () => send({ kind: "reject" }));
  $("btn-takeover").addEventListener("click", () => send({ kind: "takeover" }));
  $("btn-resume").addEventListener("click", () => send({ kind: "resume" }));
  $("btn-done").addEventListener("click", () => send({ kind: "done" }));
  $("btn-abort").addEventListener("click", () => send({ kind: "abort" }));
  $("suggest-filter").addEventListener("input", renderSuggestions);
  $("obs-filter").addEventListener("input", () => current && renderObservation(current.observation));

  poll();
})();
