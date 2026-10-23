/* Jarvis main window. Renders real pipeline events (via the Qt WebChannel bridge
   `jarvis`) and calls real backend slots. No sample data: everything shown comes
   from the running assistant, its database or its settings. */
(function () {
  "use strict";
  const { JarvisOrb, LABEL, COLOR, BUSY, connectBridge } = window.JarvisUI;
  const $ = id => document.getElementById(id);
  let J = null;

  // ───────────────────────────── state
  const S = {
    phase: "stopped", running: false, voiceOk: false, muted: false, info: null, dryRun: false,
    listenMode: "tap", level: 0, errorUntil: 0, errorDetail: "", task: null, approval: null,
    orbStyle: "hud", view: "assistant", ptt: false, liveRuns: 0, executingTool: "",
  };

  // ───────────────────────────── helpers
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "style") el.style.cssText = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "text") el.textContent = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    return el;
  }
  const corners = () => ["tl", "tr", "bl", "br"].map(c => h("i", { class: "corner " + c }));
  const svgNS = "http://www.w3.org/2000/svg";
  function icon(paths, stroke) {
    const s = document.createElementNS(svgNS, "svg");
    s.setAttribute("width", "16"); s.setAttribute("height", "16"); s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("fill", "none"); s.setAttribute("stroke", stroke || "currentColor"); s.setAttribute("stroke-width", "1.5");
    s.setAttribute("stroke-linecap", "round"); s.setAttribute("stroke-linejoin", "round"); s.style.cssText = "flex:none; margin-top:1px";
    paths.forEach(d => { const p = document.createElementNS(svgNS, "path"); p.setAttribute("d", d); s.appendChild(p); });
    return s;
  }
  const ICON_OK = ["M20 6L9 17l-5-5"], ICON_NO = ["M18 6L6 18", "M6 6l12 12"];
  const ICON_WARN = ["M21.73 18l-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3", "M12 9v4", "M12 17h.01"];
  const tstr = ms => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const dstr = ms => new Date(ms).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const fmt = n => Math.round(n).toLocaleString("en-US");
  const paramStr = p => Object.entries(p || {}).filter(([, v]) => v !== "" && v != null).map(([k, v]) => k + " = " + (typeof v === "object" ? JSON.stringify(v) : v)).join(" · ");
  const call = (fn, ...args) => new Promise(res => { if (!J) return res(null); J[fn](...args, r => res(r)); });
  function toast(text) {
    const t = h("div", { class: "toast", role: "status", text });
    document.body.appendChild(t); setTimeout(() => t.remove(), 3200);
  }
  const busy = () => BUSY.includes(S.phase) || !!S.approval;

  // ───────────────────────────── display state
  function displayPhase() {
    if (S.approval) return "approval";
    if (S.errorUntil > Date.now() && ["idle", "stopped"].includes(S.phase)) return "error";
    if (S.phase === "idle" && !S.voiceOk) return "text";
    if (S.phase === "idle" && S.muted) return "paused";
    return S.phase;
  }

  // ───────────────────────────── render: chrome
  function renderState() {
    const ph = displayPhase();
    const color = COLOR[ph] || COLOR.stopped;
    document.documentElement.style.setProperty("--state-color", color);
    $("state-label").textContent = LABEL[ph] || ph;
    $("status-label").textContent = LABEL[ph] || ph;
    $("power-label").textContent = S.running ? "Stop" : "Start";
    const b = busy();
    $("btn-stop").hidden = !b; $("btn-send").hidden = b;
    $("btn-ptt").disabled = !S.running || !S.voiceOk;
    $("btn-ptt").classList.toggle("active", S.phase === "listening");
    $("btn-mute").disabled = !S.running || !S.voiceOk;
    $("btn-mute").setAttribute("aria-pressed", S.muted ? "true" : "false");
    const mt = S.muted ? "Resume microphone (Ctrl+M)" : "Mute microphone (Ctrl+M)";
    $("btn-mute").title = mt; $("btn-mute").setAttribute("aria-label", mt);
    $("ico-mic").style.display = S.muted ? "none" : ""; $("ico-mic-off").style.display = S.muted ? "" : "none";
    const input = $("input");
    input.placeholder = !S.running ? "Start the assistant to send commands" : S.approval ? "Type yes or no to answer"
      : b ? "Type a command — it runs next" : S.voiceOk ? "Type a command, or click the orb to talk" : "Type a command (no microphone)";
    const orbAria = !S.running ? "Start Jarvis" : (ph === "idle" || ph === "paused") && S.voiceOk ? "Talk to Jarvis" : (LABEL[ph] || ph);
    $("orb-button").title = orbAria; $("orb-button").setAttribute("aria-label", orbAria);
  }


  // ───────────────────────────── render: live captions
  // Only the current exchange is shown under the orb: what the user said, then Jarvis's answer.
  // The full conversation lives in Memory › History and every tool run in Activity.
  const CAPTION_FADE_MS = 12000;
  let captionTimer = null;
  function caption(you, jarvis, kind) {
    clearTimeout(captionTimer);
    $("captions").classList.remove("faded");
    if (you !== undefined) $("cap-you").textContent = you;
    if (jarvis !== undefined) {
      const el = $("cap-jarvis");
      el.textContent = jarvis; el.className = "cap-jarvis" + (kind ? " " + kind : "");
    }
    if (S.phase === "idle") fadeCaptionsLater();     // the answer can arrive after the pipeline went idle
  }
  function clearCaptions() { clearTimeout(captionTimer); caption("", ""); }
  function fadeCaptionsLater() {
    clearTimeout(captionTimer);
    if (!$("cap-you").textContent && !$("cap-jarvis").textContent) return;
    captionTimer = setTimeout(() => $("captions").classList.add("faded"), CAPTION_FADE_MS);
  }
  const sys = text => { caption(undefined, text, "note"); if (!busy()) fadeCaptionsLater(); };

  // Tool runs are tracked for Activity › Running now; the Assistant view shows no task strip (user choice, 2026-10-18).
  function finishTaskLater() { const t = S.task; setTimeout(() => { if (S.task === t && t.status !== "running" && t.status !== "awaiting") { S.task = null; } }, 2400); }

  // Shared with agent.js (loaded after this file).
  const A = window.JarvisApp = { h, corners, call: (...a) => call(...a), dstr, tstr, toast, sys: t => sys(t), showView: n => showView(n) };

  // ───────────────────────────── approval dialog
  function renderApproval() {
    const root = $("dialog-root"); root.textContent = "";
    const a = S.approval; if (!a) return;
    const left = Math.max(0, a.deadline * 1000 - Date.now()), frac = left / (a.timeout_s * 1000);
    const ring = document.createElementNS(svgNS, "svg");
    ring.setAttribute("width", "22"); ring.setAttribute("height", "22"); ring.setAttribute("viewBox", "0 0 28 28"); ring.style.transform = "rotate(-90deg)";
    ring.innerHTML = '<circle cx="14" cy="14" r="10" fill="none" stroke="color-mix(in srgb, var(--color-neutral-100) 16%, transparent)" stroke-width="1.5"></circle><circle id="cd-ring" cx="14" cy="14" r="10" fill="none" stroke="var(--color-accent-200)" stroke-width="1.5" stroke-dasharray="62.83"></circle>';
    const dlg = h("div", { class: "dialog blueprint", role: "dialog", "aria-modal": "true", "aria-labelledby": "ap-title", "data-screen-label": "Permission dialog" }, corners(),
      h("div", { class: "dialog-kicker" },
        icon(["M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z", "M12 8v4", "M12 16h.01"], "var(--color-accent-300)"),
        h("span", { class: "k", text: "Permission required · " + a.kind_label }),
        h("span", { class: "countdown" }, ring, h("span", { id: "cd-text", text: (left / 1000).toFixed(1) + " s" }))),
      h("div", { class: "dialog-title", id: "ap-title", text: a.title }),
      h("div", { class: "kv" }, (a.rows || []).map(r => h("div", { class: "kv-row" }, h("span", { class: "k", text: r.k }), h("span", { class: "v" }, String(r.v), " ", h("span", { class: "sub", text: r.sub || "" }))))),
      h("div", { class: "dialog-body", text: (a.source === "phone" ? "Asked in the phone app, where you can answer too. You can also press Y or N or choose below." : `Jarvis asked aloud: “${a.spoken}”. Answer by voice (yes / no), press Y or N, type it, or choose below.`) + ` Nothing runs until you allow it; the request is denied automatically when the timer runs out.` }),
      h("div", { class: "dialog-actions" },
        h("button", { type: "button", class: "btn btn-secondary blueprint", id: "ap-deny", onclick: () => answer(false) }, corners(), "Deny · N"),
        h("button", { type: "button", class: "btn btn-primary blueprint", onclick: () => answer(true) }, corners(), "Allow · Y")));
    root.appendChild(h("div", { class: "dialog-backdrop" }, dlg));
    setRing(frac, left);
    setTimeout(() => { const d = $("ap-deny"); if (d) d.focus(); }, 30);
  }
  function setRing(frac, left) {
    const r = $("cd-ring"), t = $("cd-text");
    if (r) r.setAttribute("stroke-dashoffset", (62.83 * (1 - frac)).toFixed(2));
    if (t) t.textContent = (left / 1000).toFixed(1) + " s";
  }
  function tickApproval() {
    const a = S.approval; if (!a) return;
    const left = Math.max(0, a.deadline * 1000 - Date.now());
    setRing(left / (a.timeout_s * 1000), left);
  }
  function answer(allowed) { if (S.approval) call("approve", S.approval.id, allowed); }

  // ───────────────────────────── events from the backend
  function onEvent(json) {
    let ev; try { ev = JSON.parse(json); } catch (e) { return; }
    const at = ev.ts ? ev.ts * 1000 : Date.now();
    switch (ev.type) {
      case "phase":
        S.phase = ev.phase;
        if (ev.phase === "listening") { S.listenMode = ev.mode || "tap"; S.level = 0; }
        if (ev.phase === "executing" && ev.tool) S.executingTool = ev.purpose || ev.tool;
        if (ev.phase === "loading") { S.running = true; }
        if (ev.phase === "stopped") { S.running = false; S.task = null; S.approval = null; renderApproval(); }
        if (ev.phase === "listening") clearCaptions();
        if (ev.phase === "idle") fadeCaptionsLater(); else clearTimeout(captionTimer);
        break;
      case "ready":
        S.info = ev; S.voiceOk = !!ev.voice; S.running = true; S.dryRun = !!ev.dry_run;
        if (document.activeElement === document.body) $("input").focus();
        break;
      case "level": S.level = ev.v; return;
      case "transcript": caption((ev.source === "phone" ? "📱 " : "") + ev.text, ""); break;
      case "tool_call":
        S.task = { tool: ev.tool, callId: ev.call_id, title: ev.tool + " · " + (ev.purpose || ""), detail: paramStr(ev.args), status: "running", startedAt: Date.now() };
        S.liveRuns++; bumpActivity(); break;
      case "tool_result":
        if (S.task && S.task.callId === ev.call_id) {
          S.task.status = ev.dry_run ? "dry_run" : ev.ok ? "done" : "failed"; S.task.endedAt = Date.now();
          if (!ev.ok) S.task.detail = ev.content; finishTaskLater();
        }
        bumpActivity(); break;
      case "approval_request":
        S.approval = ev;
        if (S.task) S.task.status = "awaiting";
        renderApproval(); bumpActivity(); break;
      case "approval_resolved":
        S.approval = null; renderApproval();
        if (S.task && S.task.status === "awaiting") { S.task.status = ev.decision === "allow" ? "running" : "failed"; if (ev.decision !== "allow") { S.task.endedAt = Date.now(); S.task.detail = "Not approved — nothing was executed"; finishTaskLater(); } }
        bumpActivity(); break;
      case "reply": ev.greeting ? caption("", ev.text) : caption(undefined, ev.text); break;
      case "error":
        caption(undefined, ev.detail, "error");
        S.errorUntil = Date.now() + 3400; S.errorDetail = ev.detail;
        if (S.task && S.task.status === "running") { S.task.status = "failed"; S.task.endedAt = Date.now(); finishTaskLater(); }
        setTimeout(renderState, 3500); break;
      case "cancelled":
        caption(undefined, ev.detail || "Cancelled.", "note");
        if (S.task && (S.task.status === "running" || S.task.status === "awaiting")) { S.task.status = "failed"; S.task.detail = "Cancelled"; S.task.endedAt = Date.now(); finishTaskLater(); }
        break;
      case "mute": S.muted = !!ev.muted; break;
      case "stopped": S.info = null; break;
      case "settings_test": Settings.onTest(ev); return;
      case "email_test": { const l = $("email-line"); if (l) l.textContent = (ev.ok ? "✓ " : "✗ ") + ev.detail; return; }
      case "phone_status": Settings.renderPhone(); return;
      case "task_update": {
        const t = ev.task;
        const prev = S.activeTask && S.activeTask.id === t.id ? S.activeTask.status : null;
        if (["PLANNING", "RUNNING", "WAITING_FOR_APPROVAL", "VERIFYING"].includes(t.status) || (S.activeTask && S.activeTask.id === t.id)) S.activeTask = t;
        if (prev !== t.status && ["FAILED", "BLOCKED", "PAUSED", "CANCELLED"].includes(t.status))
          caption(undefined, `Task #${t.id} ${t.status.toLowerCase()}` + (t.blocked_reason && t.status !== "COMPLETED" ? `: ${t.blocked_reason}` : ""), "error");
        if (A.agent) A.agent.onEvent(ev);
        return;
      }
      case "task_event": {
        const e = ev.event;
        const shown = { accepted: "Objective accepted", plan: "Plan", verifying: "Verification", check_fail: "Check failed",
                        completed: "Completed", capability_gap: "Missing capability", resumed: "Resumed", inspected: "Inspected" };
        if (A.agent) A.agent.onEvent(ev);
        return;
      }
      case "task_progress": return;
      case "window":
        if (ev.state === "expanded" && ev.reason === "hotkey") {
          // Opened by the shortcut while the user may be typing elsewhere: catch stray
          // keys in the text box and ignore Space push-to-talk briefly.
          S.pttBlockedUntil = Date.now() + 2000;
          if (S.view !== "assistant") showView("assistant");
          setTimeout(() => $("input").focus(), 50);
        }
        return;
      case "settings_changed": if (ev.config && ev.config.orb_style) S.orbStyle = ev.config.orb_style; return;
      default: return;
    }
    renderState();
  }

  // ───────────────────────────── actions
  function send() {
    const input = $("input"); const text = input.value.trim(); if (!text) return;
    input.value = "";
    call("send", text).then(r => { if (r === "rejected" && !S.running) input.value = text; });
  }
  function cancel() { call("cancel"); }
  function pttDown(fromKey) {
    if (S.ptt || !S.running || !S.voiceOk) return;
    if (fromKey && S.pttBlockedUntil && Date.now() < S.pttBlockedUntil) return;
    S.ptt = true;
    call("pttDown").then(ok => { if (!ok) { S.ptt = false; if (busy()) sys("Busy — wait for the current command, or press Esc to stop it."); } });
  }
  function pttUp() { if (!S.ptt) return; S.ptt = false; call("pttUp"); }
  function orbClick() {
    if (!S.running) { call("start"); return; }
    if (!S.voiceOk) { sys("No microphone available — type a command instead."); return; }
    if (busy()) return;
    call("listen").then(ok => { if (!ok) sys("Can't listen right now."); });
  }
  function toggleMute() { if (S.running && S.voiceOk) call("toggleMute").then(m => { S.muted = !!m; renderState(); }); }

  // ───────────────────────────── views
  function showView(name) {
    S.view = name;
    document.querySelectorAll(".tab").forEach(t => t.setAttribute("aria-selected", t.dataset.view === name ? "true" : "false"));
    document.querySelectorAll(".view").forEach(v => v.classList.toggle("active", v.id === "view-" + name));
    if (name === "activity") { S.liveRuns = 0; bumpActivity(); Activity.load(); }
    if (name === "memory") Memory.load();
    if (name === "settings") Settings.load();
    if (A.agent) { if (name === "agent") A.agent.show(); else A.agent.hide(); }
    if (name === "assistant") setTimeout(() => $("input").focus(), 30);
  }
  let actTimer = null;
  function bumpActivity() {
    const c = $("activity-count");
    c.hidden = S.view === "activity" || S.liveRuns === 0; c.textContent = S.liveRuns;
    if (S.view === "activity") { clearTimeout(actTimer); actTimer = setTimeout(Activity.load, 250); }
  }

  // ───────────────────────────── Activity view
  const Activity = {
    async load() {
      const now = $("act-now"); now.textContent = "";
      if (S.approval) {
        const a = S.approval;
        now.appendChild(h("div", { class: "card blueprint approval-card" }, corners(),
          h("div", { class: "section-title", text: "Waiting for your approval" }),
          h("div", { class: "dialog-title", style: "font-size:18px", text: a.title }),
          h("div", { class: "kv" }, (a.rows || []).map(r => h("div", { class: "kv-row" }, h("span", { class: "k", text: r.k }), h("span", { class: "v", text: String(r.v) + (r.sub ? "  " + r.sub : "") })))),
          h("div", { class: "dialog-actions", style: "justify-content:flex-start" },
            h("button", { class: "btn btn-primary", onclick: () => answer(true), text: "Allow" }), h("button", { class: "btn btn-secondary", onclick: () => answer(false), text: "Deny" }))));
      } else if (S.task && (S.task.status === "running" || S.task.status === "awaiting")) {
        now.appendChild(h("div", { class: "card blueprint approval-card" }, corners(),
          h("div", { class: "section-title", text: "Running now" }), h("div", { class: "task-title", text: S.task.title }),
          h("div", { class: "task-detail", text: S.task.detail }),
          h("div", {}, h("button", { class: "btn btn-secondary", onclick: cancel, text: "Cancel" }))));
      }
      const raw = await call("activityList"); if (raw == null) return;
      const rows = JSON.parse(raw);
      const box = $("act-table"); box.textContent = "";
      if (!rows.length) { box.appendChild(h("div", { class: "empty", text: "No tool has run yet. Ask Jarvis to open an app, control media, or remember something." })); return; }
      const statusTag = s => h("span", { class: "tag " + ({ done: "tag-accent", dry_run: "tag-neutral", running: "tag-accent" }[s] || "tag-outline"), style: "text-transform:uppercase; font-size:10px; letter-spacing:.08em", text: s.replace("_", " ") });
      box.appendChild(h("table", { class: "table" },
        h("thead", {}, h("tr", {}, h("th", { text: "When" }), h("th", { text: "Tool" }), h("th", { text: "Arguments" }), h("th", { text: "Status" }), h("th", { class: "num", text: "Time" }), h("th", { text: "Result" }))),
        h("tbody", {}, rows.map(r => h("tr", {},
          h("td", { class: "muted", style: "white-space:nowrap", text: dstr(r.started_at * 1000) }),
          h("td", {}, h("span", { class: "tag tag-outline", style: "font-family:var(--font-heading); font-weight:600", text: r.tool })),
          h("td", { class: "wrap muted", text: typeof r.args === "object" ? paramStr(r.args) : String(r.args || "") }),
          h("td", {}, statusTag(r.status), r.decision && r.decision !== "auto" ? h("div", { class: "muted", style: "font-size:11px; margin-top:3px", text: r.decision === "approved" ? "approved by you" : "permission: " + r.decision }) : null),
          h("td", { class: "num muted", text: r.duration_ms != null ? fmt(r.duration_ms) + " ms" : "—" }),
          h("td", { class: "wrap" }, h("div", { class: "clamp", title: r.result || r.error || "", text: r.result || r.error || "" })))))));
    },
  };

  // ───────────────────────────── Memory view
  const Memory = {
    editing: null,
    async load() {
      const raw = await call("memoryList", $("mem-search").value || ""); if (raw == null) return;
      const { items, count } = JSON.parse(raw);
      $("mem-count").textContent = count + (count === 1 ? " item" : " items");
      const box = $("mem-table"); box.textContent = "";
      if (!items.length) { box.appendChild(h("div", { class: "empty", text: $("mem-search").value ? "No memories match." : "Nothing stored yet. Add one above, or tell Jarvis “remember that …”." })); }
      else box.appendChild(h("table", { class: "table" },
        h("thead", {}, h("tr", {}, h("th", { text: "Kind" }), h("th", { text: "Memory" }), h("th", { text: "Source" }), h("th", { text: "Updated" }), h("th", {}))),
        h("tbody", {}, items.map(m => this.row(m)))));
      this.loadHistory();
    },
    row(m) {
      if (this.editing === m.id) {
        const inp = h("input", { class: "input", value: m.content, maxlength: "1000" });
        const kind = h("select", { class: "input" }, ["fact", "preference", "note", "person", "task"].map(k => h("option", { value: k, selected: k === m.kind, text: k })));
        const pin = h("input", { type: "checkbox" }); pin.checked = !!m.pinned;
        const save = async () => {
          const r = JSON.parse(await call("memoryUpdate", m.id, inp.value, kind.value, pin.checked));
          if (!r.ok) { toast(r.error || "Could not save"); return; }
          this.editing = null; this.load();
        };
        inp.addEventListener("keydown", e => { if (e.key === "Enter") save(); if (e.key === "Escape") { e.stopPropagation(); this.editing = null; this.load(); } });
        setTimeout(() => inp.focus(), 20);
        return h("tr", {}, h("td", {}, kind), h("td", {}, inp), h("td", {}, h("label", { class: "check" }, pin, "Pin")), h("td", {}),
          h("td", {}, h("div", { class: "row-actions" }, h("button", { class: "btn btn-primary btn-sm", onclick: save, text: "Save" }), h("button", { class: "btn btn-secondary btn-sm", onclick: () => { this.editing = null; this.load(); }, text: "Cancel" }))));
      }
      const del = h("button", { class: "btn btn-ghost btn-sm", text: "Delete" });
      del.addEventListener("click", async () => {
        if (del.dataset.armed) { await call("memoryDelete", m.id); this.load(); return; }
        del.dataset.armed = "1"; del.textContent = "Confirm delete"; setTimeout(() => { if (del.isConnected) { delete del.dataset.armed; del.textContent = "Delete"; } }, 3000);
      });
      return h("tr", {},
        h("td", {}, h("span", { class: "tag tag-neutral", text: m.kind }), m.pinned ? h("span", { class: "tag tag-accent", style: "margin-left:4px", text: "pinned" }) : null),
        h("td", { class: "wrap", text: m.content }),
        h("td", { class: "muted", text: m.source }),
        h("td", { class: "muted", style: "white-space:nowrap", text: dstr(m.updated_at * 1000) }),
        h("td", {}, h("div", { class: "row-actions" }, h("button", { class: "btn btn-ghost btn-sm", onclick: () => { this.editing = m.id; this.load(); }, text: "Edit" }), del)));
    },
    async add(e) {
      e.preventDefault();
      const text = $("mem-new").value.trim(); if (!text) return;
      const r = JSON.parse(await call("memoryAdd", text, $("mem-kind").value, $("mem-pin").checked));
      if (!r.ok) { toast(r.error); return; }
      $("mem-new").value = ""; $("mem-pin").checked = false; this.load();
    },
    async loadHistory() {
      const raw = await call("historyList", $("hist-search").value || ""); if (raw == null) return;
      const rows = JSON.parse(raw);
      const box = $("hist-table"); box.textContent = "";
      if (!rows.length) { box.appendChild(h("div", { class: "empty", text: "No conversation stored." })); return; }
      box.appendChild(h("table", { class: "table" }, h("tbody", {}, rows.slice(0, 200).map(r => h("tr", {},
        h("td", { class: "muted", style: "white-space:nowrap; width:120px", text: dstr(r.created_at * 1000) }),
        h("td", { style: "width:90px" }, h("span", { class: "tag " + (r.role === "user" ? "tag-outline" : r.role === "error" ? "tag-neutral" : "tag-accent"), text: r.role === "user" ? "you" + (r.source === "voice" ? " · voice" : r.source === "phone" ? " · phone" : "") : r.role })),
        h("td", { class: "wrap", text: r.text }))))));
    },
    async clearHistory() {
      const b = $("hist-clear");
      if (!b.dataset.armed) { b.dataset.armed = "1"; b.textContent = "Click again to delete all history"; setTimeout(() => { delete b.dataset.armed; b.textContent = "Clear history…"; }, 3500); return; }
      const n = await call("historyClear"); delete b.dataset.armed; b.textContent = "Clear history…";
      toast(`Deleted ${n} messages.`); this.loadHistory();
    },
  };

  // ───────────────────────────── Settings view
  const Settings = {
    data: null, changes: {},
    get(path) { return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), this.data.config); },
    set(path, value) {
      const orig = path === "__startup__" ? this.data.startup_enabled : this.get(path);
      if (JSON.stringify(orig) === JSON.stringify(value)) delete this.changes[path]; else this.changes[path] = value;
      const f = document.querySelector(`[data-field="${CSS.escape(path)}"]`); if (f) f.classList.toggle("changed", path in this.changes);
      this.renderSaveBar();
    },
    async load() {
      const raw = await call("settingsGet"); if (raw == null) return;
      this.data = JSON.parse(raw); this.changes = {}; this.render();
    },
    field(label, path, control, help) {
      return h("div", { class: "field", "data-field": path }, h("label", { text: label }), control, help ? h("div", { class: "help", text: help }) : null);
    },
    select(path, options, label, help, cast) {
      const cur = this.get(path);
      const sel = h("select", { class: "input" }, options.map(o => {
        const [v, t] = Array.isArray(o) ? o : [o, o];
        return h("option", { value: String(v), selected: String(v) === String(cur), text: t });
      }));
      if (!options.some(o => String(Array.isArray(o) ? o[0] : o) === String(cur)) && cur != null) sel.prepend(h("option", { value: String(cur), selected: true, text: String(cur) + " (current)" }));
      sel.addEventListener("change", () => this.set(path, cast ? cast(sel.value) : sel.value));
      return this.field(label, path, sel, help);
    },
    number(path, label, help, step, isFloat) {
      const inp = h("input", { class: "input", type: "number", value: this.get(path), step: step || 1 });
      inp.addEventListener("input", () => { const v = isFloat ? parseFloat(inp.value) : parseInt(inp.value, 10); if (!isNaN(v)) this.set(path, v); });
      return this.field(label, path, inp, help);
    },
    text(path, label, help, list) {
      const inp = h("input", { class: "input", type: "text", value: this.get(path) == null ? "" : this.get(path), list: list || null, spellcheck: "false" });
      inp.addEventListener("input", () => this.set(path, inp.value));
      return this.field(label, path, inp, help);
    },
    toggle(path, label, help) {
      const cb = h("input", { type: "checkbox" }); cb.checked = path === "__startup__" ? !!this.data.startup_enabled : !!this.get(path);
      cb.addEventListener("change", () => this.set(path, cb.checked));
      return h("div", { class: "field", "data-field": path }, h("label", { class: "check", style: "font-size:13px; color:var(--color-text)" }, cb, label), help ? h("div", { class: "help", text: help }) : null);
    },
    async renderPhone() {
      const line = $("ph-line"); if (!line) return;
      const raw = await call("phoneStatus"); if (!raw) return;
      const s = JSON.parse(raw);
      line.textContent = !s.enabled ? "Off. Tick “Phone app” and save to start the server."
        : s.running ? `Listening on ${s.host}:${s.port} · ${s.connected} phone${s.connected === 1 ? "" : "s"} connected.`
        : "Not running: " + (s.error || "starting…");
      if (!s.pair_code) { const p = $("ph-pair"); if (p) p.textContent = ""; }
      const box = $("ph-devices"); box.textContent = "";
      if (!s.devices.length) { box.appendChild(h("div", { class: "help", text: "No phone paired yet." })); return; }
      const when = t => t ? new Date(t * 1000).toLocaleString() : "never";
      s.devices.forEach(d => box.appendChild(h("div", { class: "toolbar" },
        h("span", { class: "tag " + (d.online ? "tag-accent" : "tag-outline"), text: d.name + (d.online ? " · connected" : "") }),
        h("span", { class: "help", text: `paired ${when(d.paired_at)} · last seen ${when(d.last_seen)}` }),
        h("button", { class: "btn btn-ghost btn-sm", onclick: async () => { await call("phoneRevoke", d.id); this.renderPhone(); }, text: "Remove" }))));
    },
    async pairPhone() {
      const box = $("ph-pair"); box.textContent = "";
      const r = JSON.parse(await call("phonePairStart"));
      if (!r.ok) { box.textContent = r.error; return; }
      const qr = h("div", { style: "width:260px; height:260px; flex:none; background:#fff" }); qr.innerHTML = r.qr_svg;   // SVG generated locally by segno
      const svg = qr.querySelector("svg");
      if (svg) { svg.setAttribute("width", "260"); svg.setAttribute("height", "260"); svg.style.display = "block"; svg.setAttribute("shape-rendering", "crispEdges"); }
      box.appendChild(h("div", { class: "toolbar", style: "align-items:flex-start; gap:18px" }, qr,
        h("div", {}, h("div", { text: "Scan this in the Jarvis app on your phone, or enter:" }),
          h("div", { style: "font-family:var(--font-heading); font-size:18px; margin-top:8px", text: `Address ${r.host}   Port ${r.port}` }),
          h("div", { style: "font-family:var(--font-heading); font-size:28px; letter-spacing:4px; margin-top:6px", text: r.code }),
          h("div", { class: "help", text: "The code works once and expires in 10 minutes." }))));
    },
    section(title, note, ...kids) {
      return h("div", { class: "card blueprint section" }, corners(), h("div", { class: "section-title", text: title }), note ? h("div", { class: "section-note", text: note }) : null, ...kids);
    },
    render() {
      const o = this.data.options, root = $("settings-root"); root.textContent = "";
      root.appendChild(h("div", { class: "page-head" }, h("div", {}, h("h2", { text: "Settings" }), h("div", { class: "sub", text: "Saved to config.json in your data folder (only what you change). Voice and model changes apply when the assistant restarts." }))));

      const testLine = h("div", { id: "test-line", class: "help", text: "" });
      root.appendChild(this.section("Claude", "The brain that understands requests and decides which tools to run. It uses your Claude Code login (subscription, no API key).",
        h("div", { class: "grid-2" },
          h("datalist", { id: "model-options" }, (o.models || []).map(m => h("option", { value: m }))),
          this.text("llm.claude.model", "Model", "Any model id your account can use.", "model-options"),
          this.select("llm.claude.effort", ["low", "medium", "high", "xhigh", "max"], "Effort", "Lower is faster; voice commands rarely need more than low."),
          this.number("agent.max_turns", "Max steps per request", "Bounds multi-step tool use."),
          this.number("llm.claude.timeout_seconds", "Idle timeout (s)", "Gives up if Claude is silent this long while no tool is running."),
          this.number("agent.max_seconds", "Total time limit per request (s)"),
          this.number("agent.memory_context_items", "Memories sent with each request")),
        h("div", { class: "toolbar" }, h("button", { class: "btn btn-secondary", id: "btn-test", onclick: () => this.test(), text: "Test connection" }), testLine)));

      const devices = [[null, "System default microphone"]].concat(
        (o.devices || []).map(d => [d.index, `[${d.index}] ${d.name}${d.default ? " · default" : ""}`]));
      root.appendChild(this.section("Microphone", o.devices_error ? "Could not list devices: " + o.devices_error : null,
        h("div", { class: "grid-2" },
          this.select("audio.input_device_index", devices, "Input device", null, v => v === "null" ? null : parseInt(v, 10)),
          this.toggle("greeting.enabled", "Greet me when I open the window", "A new line every time, written by Claude."),
          this.number("pipeline.silence_timeout_ms", "End of speech after silence (ms)"),
          this.number("pipeline.max_recording_ms", "Longest command (ms)"))));

      root.appendChild(this.section("Speech recognition (Whisper)", null,
        h("div", { class: "grid-2" },
          this.select("stt.model_path", o.whisper_models || [], "Model"),
          this.select("stt.device", ["cuda", "cpu", "auto"], "Device"),
          this.select("stt.compute_type", ["int8", "int8_float16", "float16", "float32"], "Compute type", "int8 keeps VRAM low on the 4 GB card."),
          this.number("stt.beam_size", "Beam size"))));

      root.appendChild(this.section("Voice", null,
        h("div", { class: "grid-2" },
          this.toggle("tts.enabled", "Speak replies aloud"),
          this.select("tts.engine", [["chatterbox", "Chatterbox-Turbo (natural, slower; Piper while it loads)"], ["piper", "Piper (instant)"]], "Voice engine"),
          this.select("tts.piper.voice", o.voices || [], "Piper voice", "Voices in the piper folder of the models folder (python scripts/setup_piper.py adds more)."))));

      const perms = this.get("tools.permissions") || {};
      const permRows = (o.tools || []).map(t => {
        const sel = h("select", { class: "input", style: "min-width:110px" }, ["allow", "ask", "deny"].map(p => h("option", { value: p, selected: (perms[t.name] || t.default) === p, text: p + (p === t.default ? " (default)" : "") })));
        sel.addEventListener("change", () => {
          const next = Object.assign({}, this.changes["tools.permissions"] || perms);
          if (sel.value === t.default) delete next[t.name]; else next[t.name] = sel.value;
          this.set("tools.permissions", next);
        });
        return h("tr", {}, h("td", {}, h("span", { class: "tag tag-outline", style: "font-family:var(--font-heading); font-weight:600", text: t.name })),
          h("td", { class: "wrap muted", text: t.description + (t.per_arg ? " Commands marked confirm in your profile always ask." : "") }),
          h("td", {}, h("span", { class: "tag " + (t.risk === "safe" ? "tag-neutral" : "tag-accent"), text: t.risk })), h("td", { "data-field": "tools.permissions" }, sel));
      });
      root.appendChild(this.section("Tools and permissions", "Every action goes through these checks. “ask” shows the permission dialog and asks aloud; “deny” blocks the tool entirely.",
        h("div", { class: "grid-2" },
          this.toggle("dry_run", "Dry run (log actions instead of executing them)"),
          this.number("tools.approval_timeout_ms", "Approval timer (ms)", "Unanswered requests are denied."),
          this.toggle("claude_code.require_confirmation", "Ask before delegating to Claude Code"),
          this.number("claude_code.timeout_seconds", "Claude Code delegation limit (s)")),
        o.tools_error ? h("div", { class: "help", text: o.tools_error }) : h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", { text: "Tool" }), h("th", { text: "What it does" }), h("th", { text: "Risk" }), h("th", { text: "Permission" }))), h("tbody", {}, permRows))));

      const orbSeg = h("div", { class: "seg", role: "radiogroup", "aria-label": "Orb style" }, ["hud", "reticle", "waveform", "particles"].map(sty => {
        const r = h("input", { type: "radio", name: "orb-style", value: sty }); r.checked = (this.get("gui.orb_style") || "hud") === sty;
        r.addEventListener("change", () => { this.set("gui.orb_style", sty); S.orbStyle = sty; });
        return h("label", { class: "seg-opt" }, r, sty);
      }));
      const pw = h("input", { class: "input", type: "password", placeholder: "SMTP / app password", autocomplete: "new-password" });
      const emailLine = h("div", { class: "help", id: "email-line", text: "" });
      const listField = (path, label, help) => {
        const inp = h("input", { class: "input", type: "text", value: (this.get(path) || []).join(", "), spellcheck: "false" });
        inp.addEventListener("input", () => this.set(path, inp.value.split(",").map(x => x.trim()).filter(Boolean)));
        return this.field(label, path, inp, help);
      };
      root.appendChild(this.section("Email", "Jarvis sends mail from your account over SMTP. The password is stored in Windows Credential Manager, never in config files. Mail to anyone outside the trusted list asks you first.",
        h("div", { class: "grid-2" },
          this.toggle("email.enabled", "Let Jarvis send email"),
          this.select("email.preset", [["zoho-in", "Zoho Mail (India, smtp.zoho.in)"], ["zoho", "Zoho Mail (smtp.zoho.com)"], ["gmail", "Gmail (app password)"], ["outlook", "Outlook / Microsoft 365"], ["custom", "Custom server"]], "Provider"),
          this.text("email.smtp_host", "SMTP server"),
          this.number("email.smtp_port", "Port"),
          this.select("email.security", ["ssl", "starttls", "none"], "Security"),
          this.text("email.username", "Username (login)"),
          this.text("email.from_address", "From address"),
          this.text("email.from_name", "From name"),
          listField("email.trusted_recipients", "Trusted recipients (comma separated)", "Mail only to these (and your own address) is sent without asking."),
          this.text("email.signature", "Signature")),
        h("div", { class: "toolbar" }, pw,
          h("button", { class: "btn btn-primary btn-sm", onclick: async () => { const r = JSON.parse(await call("emailSavePassword", pw.value)); pw.value = ""; emailLine.textContent = r.ok ? "Password saved to Windows Credential Manager." : "Not saved: " + r.error; }, text: "Save password" }),
          h("button", { class: "btn btn-secondary btn-sm", onclick: () => { emailLine.textContent = "Sending a test email to yourself…"; call("emailTest"); }, text: "Send test email to myself" }),
          h("button", { class: "btn btn-ghost btn-sm", onclick: async () => { await call("emailForgetPassword"); emailLine.textContent = "Password removed."; }, text: "Forget password" })),
        emailLine));
      call("emailStatus").then(raw => { if (!raw) return; const s = JSON.parse(raw); const l = $("email-line"); if (l && !l.textContent) l.textContent = s.password_saved ? `Password saved for ${s.username}.` : `No password saved for ${s.username || "(no username)"} yet: email can only be drafted, not sent.`; });

      root.appendChild(this.section("Chrome", "Jarvis opens your real Chrome in the profile you name (“open chrome in Marina and go to Gmail”). It does not click or type inside pages. To answer questions from the web it reads public pages in the background, without logins.",
        h("div", { class: "grid-2" },
          this.text("chrome.default_profile", "Default profile", "Name or email used when you don't say one. Empty: the last used profile."),
          listField("chrome.blocked_domains", "Never open or read these sites"))));

      root.appendChild(this.section("Phone app", "Control Jarvis from the Jarvis app on your Android phone: typed and spoken commands, approvals, live status, tasks and a view of the screen. The phone connects over Tailscale (install it on this PC and the phone, same account), so nothing is open to the internet; every phone must be paired.",
        h("div", { class: "grid-2" },
          this.toggle("phone.enabled", "Phone app"),
          this.select("phone.bind", [["tailscale", "Tailscale address (anywhere)"], ["lan", "Home network only"]], "Reachable on"),
          this.toggle("phone.speak_replies", "Also speak replies to phone commands on the PC"),
          this.toggle("phone.allow_screenshot", "Let the phone see the screen"),
          this.number("phone.approval_timeout_s", "Approval timer for phone commands (s)"),
          this.number("phone.port", "Port")),
        h("div", { class: "toolbar" },
          h("button", { class: "btn btn-primary btn-sm", onclick: () => this.pairPhone(), text: "Pair a phone" }),
          h("button", { class: "btn btn-secondary btn-sm", onclick: () => { call("phoneRestart"); $("ph-line").textContent = "Restarting…"; }, text: "Restart server" })),
        h("div", { class: "help", id: "ph-line", text: "" }),
        h("div", { id: "ph-pair" }),
        h("div", { id: "ph-devices" })));
      this.renderPhone();

      root.appendChild(this.section("Windows and tray", null,
        h("div", { class: "grid-2" },
          this.toggle("__startup__", "Start Jarvis when I sign in to Windows", "Adds a shortcut to the Startup folder (starts as the floating orb)."),
          this.toggle("gui.start_minimized", "Start as the floating orb"),
          this.toggle("gui.close_to_tray", "Closing the window keeps Jarvis running (orb + tray)"),
          this.toggle("gui.autostart_pipeline", "Start listening as soon as the app opens"),
          this.select("gui.startup_view", [["tray", "Tray icon only"], ["orb", "Floating orb"]], "When Windows starts, show"),
          this.field("Orb style", "gui.orb_style", orbSeg))));

      const logBox = h("pre", { class: "log-panel", id: "log-panel" });
      root.appendChild(this.section("Logging and diagnostics", "Everything the assistant prints goes to logs/jarvis.log in the data folder (rotating, 5 MB × 3).",
        h("div", { class: "toolbar" },
          h("button", { class: "btn btn-secondary btn-sm", onclick: () => call("openLog"), text: "Open log file" }),
          h("button", { class: "btn btn-secondary btn-sm", onclick: () => call("openLogFolder"), text: "Open log folder" }),
          h("button", { class: "btn btn-secondary btn-sm", onclick: () => call("openConfigFolder"), text: "Open data folder" }),
          h("button", { class: "btn btn-secondary btn-sm", onclick: () => { call("restart"); toast("Restarting the assistant…"); }, text: "Restart assistant" })),
        logBox));
      call("logTail", 250).then(raw => { if (!raw) return; for (const line of JSON.parse(raw)) appendLog(levelOf(line), line); });

      root.appendChild(h("div", { class: "save-bar", id: "save-bar" }));
      this.renderSaveBar();
    },
    renderSaveBar(message, restart) {
      const bar = $("save-bar"); if (!bar) return; bar.textContent = "";
      const n = Object.keys(this.changes).length;
      bar.append(...[h("span", { class: "msg-line", text: message || (n ? `${n} unsaved change${n > 1 ? "s" : ""}` : "No unsaved changes") }),
        h("span", { style: "margin-left:auto" }),
        restart ? h("button", { class: "btn btn-secondary", onclick: () => { call("restart"); toast("Restarting the assistant…"); this.renderSaveBar(); }, text: "Restart assistant now" }) : null,
        h("button", { class: "btn btn-secondary", disabled: !n, onclick: () => this.load(), text: "Discard" }),
        h("button", { class: "btn btn-primary", disabled: !n, onclick: () => this.save(), text: "Save" })].filter(Boolean));
    },
    async save() {
      const r = JSON.parse(await call("settingsSave", JSON.stringify(this.changes)));
      if (!r.ok) { this.renderSaveBar("Not saved: " + r.error); return; }
      if ("gui.orb_style" in this.changes) S.orbStyle = this.changes["gui.orb_style"];
      if ("dry_run" in this.changes) { /* shown after restart via the ready event */ }
      const raw = await call("settingsGet"); this.data = JSON.parse(raw); this.changes = {};
      document.querySelectorAll(".field.changed").forEach(f => f.classList.remove("changed"));
      this.renderSaveBar(r.restart_needed ? "Saved. Restart the assistant to apply voice, model and tool changes." : "Saved.", r.restart_needed);
    },
    test() {
      $("btn-test").disabled = true; $("test-line").textContent = "Starting a Claude session and asking it to use the clock tool…";
      call("testClaude");
    },
    onTest(ev) {
      const b = $("btn-test"); if (b) b.disabled = false;
      const l = $("test-line"); if (l) l.textContent = (ev.ok ? "✓ Works — " : "✗ Failed — ") + ev.detail + ` (${ev.seconds} s, saved settings)`;
    },
  };

  // ───────────────────────────── diagnostics log
  function levelOf(line) { const m = / (DEBUG|INFO|WARNING|ERROR|CRITICAL) /.exec(line) || /\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\]/.exec(line); return m ? m[1] : "INFO"; }
  function appendLog(level, line) {
    const p = $("log-panel"); if (!p) return;
    const atBottom = p.scrollTop + p.clientHeight >= p.scrollHeight - 30;
    p.appendChild(h("div", { class: level, text: line }));
    while (p.childElementCount > 800) p.firstChild.remove();
    if (atBottom) p.scrollTop = p.scrollHeight;
  }
  function onLog(json) { if (S.view !== "settings") return; try { const { level, line } = JSON.parse(json); appendLog(level, line); } catch (e) { /* ignore */ } }

  // ───────────────────────────── keyboard
  function onKey(e) {
    const tag = (e.target && e.target.tagName || "").toLowerCase(); const typing = tag === "input" || tag === "textarea" || tag === "select";
    if (e.key === "Escape") { if (S.approval) answer(false); else cancel(); return; }
    if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === "j") { e.preventDefault(); call("collapse"); return; }
    if (e.ctrlKey && !e.shiftKey && e.key.toLowerCase() === "m") { e.preventDefault(); toggleMute(); return; }
    if (S.approval && !typing) { if (e.key.toLowerCase() === "y") { e.preventDefault(); answer(true); return; } if (e.key.toLowerCase() === "n") { e.preventDefault(); answer(false); return; } }
    if (e.code === "Space" && !typing && !e.repeat && S.view === "assistant" && tag !== "button") { e.preventDefault(); pttDown(true); }
  }
  function onKeyUp(e) { if (e.code === "Space" && S.ptt) pttUp(); }

  // ───────────────────────────── boot
  function bind() {
    document.querySelectorAll(".tab").forEach(t => t.addEventListener("click", () => showView(t.dataset.view)));
    $("btn-collapse").addEventListener("click", () => call("collapse"));
    $("btn-send").addEventListener("click", send);
    $("btn-stop").addEventListener("click", cancel);
    $("btn-power").addEventListener("click", () => call(S.running ? "stop" : "start"));
    $("btn-mute").addEventListener("click", toggleMute);
    $("orb-button").addEventListener("click", orbClick);
    const ptt = $("btn-ptt");
    ptt.addEventListener("mousedown", e => { e.preventDefault(); pttDown(); });
    ptt.addEventListener("mouseup", pttUp); ptt.addEventListener("mouseleave", pttUp);
    $("input").addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); send(); } });
    $("act-refresh").addEventListener("click", () => Activity.load());
    $("mem-form").addEventListener("submit", e => Memory.add(e));
    let mt; $("mem-search").addEventListener("input", () => { clearTimeout(mt); mt = setTimeout(() => Memory.load(), 200); });
    let ht; $("hist-search").addEventListener("input", () => { clearTimeout(ht); ht = setTimeout(() => Memory.loadHistory(), 200); });
    $("hist-clear").addEventListener("click", () => Memory.clearHistory());
    window.addEventListener("keydown", onKey); window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", pttUp);
    setInterval(tickApproval, 100);
    setInterval(() => { if (S.errorUntil && S.errorUntil < Date.now()) { S.errorUntil = 0; renderState(); } }, 500);
  }

  bind();
  renderState();
  new JarvisOrb($("orb"), () => ({
    phase: displayPhase(), style: S.orbStyle, level: S.level,
    approvalFrac: S.approval ? Math.max(0, (S.approval.deadline * 1000 - Date.now()) / (S.approval.timeout_s * 1000)) : 1,
  }));

  connectBridge(bridge => {
    J = bridge;
    J.event.connect(onEvent);
    J.log.connect(onLog);
    J.snapshot(raw => {
      const s = JSON.parse(raw);
      S.phase = s.phase; S.running = s.running; S.voiceOk = s.voice_ok; S.muted = s.muted; S.orbStyle = s.orb_style; S.dryRun = s.dry_run;
      if (s.ready && s.ready.type === "ready") S.info = s.ready;
      if (s.pending_approval) { S.approval = s.pending_approval; renderApproval(); }
      renderState();
      $("input").focus();
    });
  });
})();
