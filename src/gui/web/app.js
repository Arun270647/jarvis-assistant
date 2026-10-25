/* Jarvis main window. Renders real pipeline events (via the Qt WebChannel bridge
   `jarvis`) and calls real backend slots. No sample data: everything shown comes
   from the running assistant, its database or its settings. */
(function () {
  "use strict";
  const { JarvisOrb, LABEL, COLOR, BUSY, connectBridge } = window.JarvisUI;
  const t = window.JarvisStrings.t;
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
    const orbAria = !S.running ? t("orb.start") : (ph === "idle" || ph === "paused") && S.voiceOk ? t("orb.talk") : (LABEL[ph] || ph);
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
      h("div", { class: "dialog-body", text: t(a.voice_ok === false ? "approval.spoken_click" : "approval.spoken", { spoken: a.spoken }) + ` Nothing runs until you allow it; the request is denied automatically when the timer runs out.` }),
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
    if (window.JarvisSetup && window.JarvisSetup.active) window.JarvisSetup.onEvent(ev);
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
        ["signed_out", "claude_missing", "offline", "limit", "ai_setup", "bad_key"].forEach(Banners.clear);
        if (ev.voice) ["mic_blocked", "mic_missing", "stt_missing", "voice_off"].forEach(Banners.clear);
        if (document.activeElement === document.body) $("input").focus();
        break;
      case "level": S.level = ev.v; return;
      case "transcript": caption(ev.text, ""); break;
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
        if (ev.code) Banners.show(ev);
        caption(undefined, ev.message || ev.detail, "error");
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
      case "ai_models": Settings.onModels(ev); return;
      case "banner": Banners.show(ev); return;
      case "update_check": case "update_progress": case "update_ready": case "install": Settings.onUpdate(ev); return;
      case "open_settings": Settings.open(ev.section); return;
      case "claude_login": Banners.onClaudeLogin(ev); return;
      case "claude_install": Banners.onClaudeInstall(ev); return;
      case "download":
        if (S.view === "settings") Settings.onDownload(ev);
        if (ev.state === "failed" && !(window.JarvisSetup && window.JarvisSetup.active))
          Banners.show({ code: "download_failed", message: `${ev.name} didn't finish downloading.`, fix: { label: "Retry", action: "download", arg: ev.option } });
        if (ev.state === "done") Banners.clear("download_failed");
        return;
      case "usage": if (S.view === "activity") Activity.loadUsage(); return;
      case "email_test": { const l = $("email-line"); if (l) l.textContent = (ev.ok ? "✓ " : "✗ ") + ev.detail; return; }
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
      case "settings_changed":
        if (ev.config && ev.config.orb_style) S.orbStyle = ev.config.orb_style;
        if (ev.assistant_name) window.JarvisStrings.setName(ev.assistant_name);
        return;
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
    async loadUsage() {
      const box = $("act-usage"); if (!box) return;
      const raw = await call("usageSummary"); if (raw == null) return;
      const u = JSON.parse(raw); box.textContent = "";
      if (u.error) { box.appendChild(h("div", { class: "help", text: u.error })); return; }
      const money = v => "$" + (v > 0 && v < 0.01 ? v.toFixed(4) : (v || 0).toFixed(2));
      const k = n => (n || 0) >= 1e6 ? (n / 1e6).toFixed(1) + "M" : (n || 0) >= 1000 ? Math.round(n / 1000) + "k" : String(n || 0);
      box.appendChild(h("div", { class: "help", text: `Today: ${money(u.today_usd)} estimated` +
        (u.budget_usd ? ` of your ${money(u.budget_usd)} daily limit.` : ". No daily limit set (Settings › AI).") }));
      if (!u.days.length) { box.appendChild(h("div", { class: "empty", text: "No AI requests recorded yet." })); return; }
      box.appendChild(h("table", { class: "table" },
        h("thead", {}, h("tr", {}, ["Day", "Requests", "Input", "Cached", "Output", "Estimated cost"].map((t, i) => h("th", { class: i ? "num" : "", text: t })))),
        h("tbody", {}, u.days.map(d => h("tr", {},
          h("td", { text: d.day }), h("td", { class: "num", text: String(d.requests) }),
          h("td", { class: "num", text: k(d.input_tokens + d.cache_write) }), h("td", { class: "num", text: k(d.cache_read) }),
          h("td", { class: "num", text: k(d.output_tokens) }),
          h("td", { class: "num", text: money(d.cost_usd) + (d.unpriced ? ` (+${d.unpriced} without a price)` : "") }))))));
    },
    async load() {
      Activity.loadUsage();               // load() is also passed around unbound (setTimeout)
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
      if (!rows.length) { box.appendChild(h("div", { class: "empty", text: t("activity.empty") })); return; }
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
      if (!items.length) { box.appendChild(h("div", { class: "empty", text: $("mem-search").value ? "No memories match." : t("memory.empty") })); }
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
        h("td", { style: "width:90px" }, h("span", { class: "tag " + (r.role === "user" ? "tag-outline" : r.role === "error" ? "tag-neutral" : "tag-accent"), text: r.role === "user" ? "you" + (r.source === "voice" ? " · voice" : "") : r.role })),
        h("td", { class: "wrap", text: r.text }))))));
    },
    async clearHistory() {
      const b = $("hist-clear");
      if (!b.dataset.armed) { b.dataset.armed = "1"; b.textContent = "Click again to delete all history"; setTimeout(() => { delete b.dataset.armed; b.textContent = "Clear history…"; }, 3500); return; }
      const n = await call("historyClear"); delete b.dataset.armed; b.textContent = "Clear history…";
      toast(`Deleted ${n} messages.`); this.loadHistory();
    },
  };

  // Settings sections with a matching section in docs/GETTING_STARTED.md (the ? buttons).
  const HELP = { "general": "2-set-up-the-setup-wizard", "ai": "5-other-ways-to-connect", "microphone": "6-somethings-not-working",
                 "voice": "6-somethings-not-working", "models": "what-gets-downloaded",
                 "tools-and-permissions": "4-change-what-jarvis-is-allowed-to-do", "about": "7-update-and-uninstall" };

  // ───────────────────────────── banners: an explained error and its fix (src/app/errors.py)
  const Banners = {
    items: new Map(),
    show(ev) {
      if (!ev.code || !ev.message) return;
      this.items.set(ev.code, ev); this.render();
    },
    clear(code) { if (Banners.items.delete(code)) Banners.render(); },
    note(code, message) { const b = this.items.get(code); if (b) { this.items.set(code, Object.assign({}, b, { message, busy: true })); this.render(); } },
    render() {
      const box = $("banners"); box.textContent = "";
      [...this.items.values()].slice(-2).forEach(b => box.appendChild(h("div", { class: "banner" },
        h("span", { class: "msg", text: b.message }),
        b.fix && !b.busy ? h("button", { class: "btn btn-primary btn-sm", onclick: () => this.fix(b), text: b.fix.label }) : null,
        h("button", { class: "close", title: "Dismiss", "aria-label": "Dismiss", onclick: () => this.clear(b.code), text: "×" }))));
    },
    fix(b) {
      const { action, arg } = b.fix;
      if (action === "open_mic_privacy") call("openMicPrivacy");
      else if (action === "settings") Settings.open(arg);
      else if (action === "restart") { call("restart"); this.clear(b.code); toast("Restarting the assistant…"); }
      else if (action === "download") { call("setupDownload", arg); this.clear(b.code); }
      else if (action === "open_url") call("openUrl", arg);
      else if (action === "update_restart") { this.note(b.code, "Restarting to update…"); call("updateRestart").then(raw => { const r = JSON.parse(raw || "{}"); if (!r.ok) this.show({ code: "update", message: r.error }); }); }
      else if (action === "claude_login") { call("claudeLogin"); this.note(b.code, "Finish signing in in your browser, then come back here."); }
      else if (action === "claude_install") { call("claudeInstall"); this.note(b.code, "Getting Claude ready… (about a minute)"); }
    },
    onClaudeLogin(ev) {
      if (ev.state === "signed_in") { this.clear("signed_out"); toast(`Signed in as ${ev.email || "your Claude account"}.`); call("restart"); }
      else if (ev.state !== "waiting") this.show({ code: "signed_out", message: ev.state === "timeout" ? "Sign-in wasn't finished." : "You're signed out of Claude.", fix: { label: "Sign in", action: "claude_login" } });
    },
    onClaudeInstall(ev) {
      if (ev.state === "done") { this.clear("claude_missing"); this.show({ code: "signed_out", message: "Claude Code is ready. Sign in with your Claude account.", fix: { label: "Sign in", action: "claude_login" } }); }
      else if (ev.state === "failed") this.show({ code: "claude_missing", message: ev.error || "Claude Code didn't install.", fix: { label: "Install it yourself", action: "open_url", arg: "https://docs.claude.com/en/docs/claude-code/setup" } });
    },
  };

  // ───────────────────────────── Settings view
  const Settings = {
    data: null, changes: {},
    get(path) { return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), this.data.config); },
    cur(path) { return path in this.changes ? this.changes[path] : this.get(path); },
    editable(path) { return (this.data.editable || []).includes(path); },
    set(path, value) {
      const orig = path === "__startup__" ? this.data.startup_enabled : this.get(path);
      if (JSON.stringify(orig) === JSON.stringify(value)) delete this.changes[path]; else this.changes[path] = value;
      const f = document.querySelector(`[data-field="${CSS.escape(path)}"]`); if (f) f.classList.toggle("changed", path in this.changes);
      this.renderSaveBar();
    },
    async load() {
      const raw = await call("settingsGet"); if (raw == null) return;
      this.data = JSON.parse(raw); this.changes = {}; this.render();
      if (this.focus) { const sec = $("sec-" + this.focus); this.focus = null; if (sec) sec.scrollIntoView({ block: "start" }); }
    },
    open(section) { this.focus = section; showView("settings"); },
    field(label, path, control, help) {
      if (help && control.setAttribute) control.setAttribute("title", help);
      return h("div", { class: "field" + (path in this.changes ? " changed" : ""), "data-field": path }, h("label", { text: label }), control, help ? h("div", { class: "help", text: help }) : null);
    },
    select(path, options, label, help, cast) {
      const cur = this.cur(path);
      const sel = h("select", { class: "input" }, options.map(o => {
        const [v, t] = Array.isArray(o) ? o : [o, o];
        return h("option", { value: String(v), selected: String(v) === String(cur), text: t });
      }));
      if (!options.some(o => String(Array.isArray(o) ? o[0] : o) === String(cur)) && cur != null) sel.prepend(h("option", { value: String(cur), selected: true, text: String(cur) + " (current)" }));
      sel.addEventListener("change", () => this.set(path, cast ? cast(sel.value) : sel.value));
      return this.field(label, path, sel, help);
    },
    number(path, label, help, step, isFloat) {
      const inp = h("input", { class: "input", type: "number", value: this.cur(path), step: step || 1 });
      inp.addEventListener("input", () => { const v = isFloat ? parseFloat(inp.value) : parseInt(inp.value, 10); if (!isNaN(v)) this.set(path, v); });
      return this.field(label, path, inp, help);
    },
    text(path, label, help, list) {
      const inp = h("input", { class: "input", type: "text", value: this.cur(path) == null ? "" : this.cur(path), list: list || null, spellcheck: "false" });
      inp.addEventListener("input", () => this.set(path, inp.value));
      return this.field(label, path, inp, help);
    },
    toggle(path, label, help) {
      const cb = h("input", { type: "checkbox" }); cb.checked = path === "__startup__" ? !!this.cur(path) || (!(path in this.changes) && !!this.data.startup_enabled) : !!this.cur(path);
      cb.addEventListener("change", () => this.set(path, cb.checked));
      return h("div", { class: "field", "data-field": path }, h("label", { class: "check", style: "font-size:13px; color:var(--color-text)", title: help || label }, cb, label), help ? h("div", { class: "help", text: help }) : null);
    },
    section(title, note, ...kids) {
      const key = title.toLowerCase().replace(/[^a-z0-9]+/g, "-");
      const guide = HELP[key];
      return h("div", { class: "card blueprint section", id: "sec-" + key }, corners(),
        h("div", { class: "section-title" }, title, guide ? h("button", { class: "help-btn", title: `Help: ${title}`, "aria-label": `Help: ${title}`, onclick: () => call("openHelp", guide), text: "?" }) : null), note ? h("div", { class: "section-note", text: note }) : null, ...kids);
    },
    render() {
      const o = this.data.options, root = $("settings-root"); root.textContent = "";
      root.appendChild(h("div", { class: "page-head" }, h("div", {}, h("h2", { text: "Settings" }), h("div", { class: "sub", text: "Saved to config.json in your data folder (only what you change). Voice and model changes apply when the assistant restarts." }))));

      root.appendChild(this.section("General", t("settings.general"), h("div", { class: "grid-2" }, this.nameField()),
        h("div", { class: "toolbar" }, h("button", { class: "btn btn-secondary btn-sm", onclick: () => call("setupRunAgain"), text: "Run setup again" }))));
      root.appendChild(this.section("AI", t("The brain that understands requests and decides which tools to run. Claude uses your Claude account through Claude Code; the other providers use an API key, or a model running on this {pc}."),
        h("div", { id: "ai-body", class: "ai-body" })));
      this.renderAI();

      const devices = [[null, "System default microphone"]].concat(
        (o.devices || []).map(d => [d.index, `[${d.index}] ${d.name}${d.default ? " · default" : ""}`]));
      root.appendChild(this.section("Microphone", o.devices_error ? "Could not list devices: " + o.devices_error : null,
        h("div", { class: "grid-2" },
          this.select("audio.input_device_index", devices, "Input device", null, v => v === "null" ? null : parseInt(v, 10)),
          this.toggle("greeting.enabled", "Greet me when I open the window", "A new line every time, written by the background model."),
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
          this.select("tts.engine", [["kokoro", "Kokoro (the default)"], ["chatterbox", "Chatterbox-Turbo"], ["piper", "Piper"], ["system", t("{voice}")]], "Voice engine", t("Kokoro sounds natural and runs on the processor. Chatterbox sounds the most natural but is slow without an NVIDIA card (Kokoro speaks while it loads). Piper is the quickest. The {voice} needs no download. Kokoro and Piper install their engine the first time you download one of their voices in Models below.")),
          this.select("tts.kokoro.voice", o.kokoro_voices || [], "Kokoro voice", "The first letter is the accent (a: American, b: British) and the second is f or m. Download Kokoro in Models below to get all its voices."),
          this.number("tts.kokoro.speed", "Kokoro speed", "1 is normal; 0.5 to 2.", 0.1, true),
          this.select("tts.system.voice", [["", t("{os} default")]].concat((o.system_voices || []).map(v => [v, v])), t("{voice}"), "Also used whenever the chosen voice can't start."),
          this.select("tts.piper.voice", o.voices || [], "Piper voice", "Voices in the piper folder of the models folder."))));

      root.appendChild(this.section("Models", "The AI models that hear and speak, on this computer. Changes apply when the assistant restarts.",
        h("div", { id: "models-body" }, h("div", { class: "empty", text: "Loading…" }))));
      this.renderModels();

      const perms = this.get("tools.permissions") || {};
      const groupOn = id => {
        const g = (o.tool_groups || []).find(x => x.id === id);
        if (!g || g.locked) return true;
        const cur = this.cur("tools.groups") || {};
        return id in cur ? !!cur[id] : g.on;
      };
      const permRows = (o.tools || []).map(t => {
        const choices = t.always ? ["ask", "deny"] : ["allow", "ask", "deny"];   // never "always allow" for these
        const now = choices.includes(perms[t.name] || t.default) ? (perms[t.name] || t.default) : "ask";
        const sel = h("select", { class: "input", style: "min-width:110px", "aria-label": `Permission for ${t.name}` }, choices.map(p => h("option", { value: p, selected: now === p, text: p + (p === t.default ? " (default)" : "") })));
        sel.addEventListener("change", () => {
          const next = Object.assign({}, this.changes["tools.permissions"] || perms);
          if (sel.value === t.default) delete next[t.name]; else next[t.name] = sel.value;
          this.set("tools.permissions", next);
        });
        return h("tr", { "data-group": t.group }, h("td", {}, h("span", { class: "tag tag-outline", style: "font-family:var(--font-heading); font-weight:600", text: t.name })),
          h("td", { class: "wrap muted", text: t.description + (t.note ? " " + t.note : t.per_arg ? " Asks for some uses; the dialog says why." : "") }),
          h("td", { class: "muted", text: t.group_title }),
          h("td", {}, h("span", { class: "tag " + (t.risk === "safe" ? "tag-neutral" : "tag-accent"), text: t.risk })), h("td", { "data-field": "tools.permissions" }, sel));
      });
      const syncRows = () => permRows.forEach(r => {
        const off = !!r.dataset.group && !groupOn(r.dataset.group);
        r.style.opacity = off ? "0.45" : ""; r.title = off ? "Its group is off: the assistant doesn't have this tool." : "";
      });
      const groupList = h("div", { class: "grid-2" }, (o.tool_groups || []).map(g => {
        const cb = h("input", { type: "checkbox", disabled: g.locked }); cb.checked = groupOn(g.id);
        cb.addEventListener("change", () => {
          this.set("tools.groups", Object.assign({}, this.get("tools.groups") || {}, this.changes["tools.groups"] || {}, { [g.id]: cb.checked }));
          syncRows();
        });
        return h("div", { class: "field", "data-field": "tools.groups" },
          h("label", { class: "check", style: "font-size:13px; color:var(--color-text)" }, cb, g.title + (g.locked ? " (always on)" : "")),
          h("div", { class: "help", text: g.text }));
      }));
      syncRows();
      root.appendChild(this.section("Tools and permissions", "What the assistant may do at all, then how each tool is checked. A group that is off isn't even shown to the AI. “ask” shows the permission dialog and asks aloud; “deny” blocks the tool.",
        h("div", { class: "section-title", style: "font-size:12px; margin-top:4px", text: "What it may do" }),
        groupList,
        window.JarvisStrings.os === "macos" && window.JarvisSetup ? h("div", { class: "field" },
          h("div", { class: "section-title", style: "font-size:12px; margin-top:4px", text: "What macOS allows" }),
          window.JarvisSetup.permissionPanel(["microphone", "accessibility", "screen"])) : null,
        h("div", { class: "grid-2" },
          this.toggle("tools.untrusted_asks", t("settings.untrusted"), t("settings.untrusted.help")),
          this.toggle("voice_approval.high_risk", "Accept a spoken yes for high-risk actions", t("settings.voice_high_risk.help"))),
        h("div", { class: "grid-2" },
          this.toggle("dry_run", "Dry run (log actions instead of executing them)"),
          this.number("tools.approval_timeout_ms", "Approval timer (ms)", "Unanswered requests are denied."),
          this.select("coding_agent.cli", [["claude", "Claude Code (claude -p)"], ["codex", "Codex (codex exec)"], ["aider", "Aider (aider --message)"], ["custom", "Custom command (coding_agent.custom_command)"], ["off", "Off"]], "Coding agent for hidden runs", "What delegate_to_coding_agent runs in a project folder. Visible terminals work with any of them."),
          this.toggle("coding_agent.require_confirmation", "Ask before delegating to the coding agent"),
          this.number("coding_agent.timeout_seconds", "Coding agent time limit (s)")),
        o.tools_error ? h("div", { class: "help", text: o.tools_error }) : h("table", { class: "table" }, h("thead", {}, h("tr", {}, h("th", { text: "Tool" }), h("th", { text: "What it does" }), h("th", { text: "Group" }), h("th", { text: "Risk" }), h("th", { text: "Permission" }))), h("tbody", {}, permRows))));

      const exts = o.extensions || [];
      const extOn = () => this.cur("tools.extensions") || [];
      root.appendChild(this.section("Extensions", t("settings.extensions"),
        h("div", { class: "banner", style: "position:static; margin:0 0 10px" }, h("span", { class: "msg", text: t("settings.extensions.warning") })),
        exts.length ? h("div", { class: "grid-2" }, exts.map(name => {
          const cb = h("input", { type: "checkbox" }); cb.checked = extOn().includes(name);
          cb.addEventListener("change", () => {
            const next = extOn().filter(n => n !== name); if (cb.checked) next.push(name);
            this.set("tools.extensions", next);
          });
          return h("div", { class: "field", "data-field": "tools.extensions" }, h("label", { class: "check", style: "font-size:13px; color:var(--color-text)" }, cb, name), h("div", { class: "help", text: `tools_ext/${name}.py` }));
        })) : h("div", { class: "help", text: "No extensions in the tools_ext folder." })));

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
      root.appendChild(this.section("Email", t("settings.email"),
        h("div", { class: "grid-2" },
          this.toggle("email.enabled", t("settings.email.toggle"), (o.tool_groups || []).some(g => g.id === "email" && !g.on) ? "The Email group under Tools and permissions is off, so sending is off too until you switch it on there." : null),
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
          h("button", { class: "btn btn-primary btn-sm", onclick: async () => { const r = JSON.parse(await call("emailSavePassword", pw.value)); pw.value = ""; emailLine.textContent = r.ok ? t("Password saved to {keystore}.") : "Not saved: " + r.error; }, text: "Save password" }),
          h("button", { class: "btn btn-secondary btn-sm", onclick: () => { emailLine.textContent = "Sending a test email to yourself…"; call("emailTest"); }, text: "Send test email to myself" }),
          h("button", { class: "btn btn-ghost btn-sm", onclick: async () => { await call("emailForgetPassword"); emailLine.textContent = "Password removed."; }, text: "Forget password" })),
        emailLine));
      call("emailStatus").then(raw => { if (!raw) return; const s = JSON.parse(raw); const l = $("email-line"); if (l && !l.textContent) l.textContent = s.password_saved ? `Password saved for ${s.username}.` : `No password saved for ${s.username || "(no username)"} yet: email can only be drafted, not sent.`; });

      root.appendChild(this.section("Chrome", t("settings.chrome"),
        h("div", { class: "grid-2" },
          this.text("chrome.default_profile", "Default profile", "Name or email used when you don't say one. Empty: the last used profile."),
          listField("chrome.blocked_domains", "Never open or read these sites"))));

      const mac = window.JarvisStrings.os === "macos";
      root.appendChild(this.section(mac ? "Windows and menu bar" : "Windows and tray", null,
        h("div", { class: "grid-2" },
          this.toggle("__startup__", t("settings.startup"), mac ? "Adds a login item for your account (starts as the floating orb)."
            : "Adds a shortcut to the Startup folder (starts as the floating orb)."),
          this.toggle("gui.start_minimized", "Start as the floating orb"),
          this.toggle("gui.close_to_tray", t("settings.close_to_tray")),
          this.toggle("gui.autostart_pipeline", "Start listening as soon as the app opens"),
          this.select("gui.startup_view", [["tray", mac ? "Menu bar icon only" : "Tray icon only"], ["orb", "Floating orb"]], t("When {os} starts, show")),
          this.field("Orb style", "gui.orb_style", orbSeg))));

      root.appendChild(this.aboutSection());

      const logBox = h("pre", { class: "log-panel", id: "log-panel" });
      root.appendChild(this.section("Logging and diagnostics", "Everything the assistant prints goes to logs/jarvis.log in the data folder (rotating, 5 MB × 3).",
        h("div", { class: "toolbar" },
          h("button", { class: "btn btn-secondary btn-sm", onclick: () => call("openLog"), text: "Open log file" }),
          h("button", { class: "btn btn-secondary btn-sm", onclick: () => call("openLogFolder"), text: "Open log folder" }),
          h("button", { class: "btn btn-secondary btn-sm", onclick: () => call("openConfigFolder"), text: "Open data folder" }),
          h("button", { class: "btn btn-secondary btn-sm", onclick: async () => { const r = JSON.parse(await call("saveReport")); toast(r.ok ? "Report saved: " + r.path : "Could not save the report: " + r.error); }, text: "Save a report" }),
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
    provider() {
      const list = (this.data.ai && this.data.ai.providers) || [];
      return list.find(p => p.name === this.cur("llm.main.provider")) || list[0] || { name: "claude_code", kind: "claude_code", label: "Claude", models: [] };
    },
    renderAI() {
      const box = $("ai-body"); if (!box) return; box.textContent = "";
      const ai = this.data.ai || { providers: [], problems: [] };
      const p = this.provider(), base = `llm.providers.${p.name}.`;
      const provider = this.select("llm.main.provider", ai.providers.map(x => [x.name, x.label]), "Provider",
        p.kind === "claude_code" ? "Sign in to Claude Code once with your Claude account; no API key needed." : null);
      provider.querySelector("select").addEventListener("change", () => this.renderAI());
      const listId = "ai-models-" + p.name;
      const known = [...new Set([...(p.models || []), ...((this.fetched || {})[p.name] || [])])];
      const fields = [provider, h("datalist", { id: listId }, known.map(m => h("option", { value: m }))),
        this.text(base + "model", "Model", p.kind === "claude_code" ? "Any model id your Claude plan can use."
          : p.local ? "The model's name as the server lists it (Fetch models, or `ollama list`)." : "A model id from the provider (Fetch models lists them).", listId)];
      if (this.editable(base + "effort")) fields.push(this.select(base + "effort", ["low", "medium", "high", "xhigh", "max"], "Effort", "Lower is faster; voice commands rarely need more than medium."));
      if (this.editable(base + "base_url")) fields.push(this.text(base + "base_url", "Server address (base URL)", p.local ? t("Where the server listens on this {pc}.") : null));
      const timeout = this.editable(base + "timeout_seconds") ? base + "timeout_seconds" : "llm.timeout_seconds";
      fields.push(this.number(timeout, p.kind === "claude_code" ? "Idle timeout (s)" : "Wait for the model (s)",
        p.kind === "claude_code" ? "Gives up if Claude is silent this long while no tool is running." : "Longest silence while waiting for the model."));
      if (this.editable(base + "vision")) fields.push(this.select(base + "vision", [[null, "Guess from the model name"], [true, "Yes"], [false, "No"]], "The model can see images",
        "Screenshots go to models that can see; the others get a text note.", v => v === "null" ? null : v === "true"));
      box.appendChild(h("div", { class: "grid-2" }, ...fields));
      if (p.kind !== "claude_code") box.appendChild(this.keyRow(p));
      box.appendChild(this.aiFacts(p, base));
      const util = ai.providers.find(x => x.name === (this.cur("llm.utility.provider") || p.name)) || p;
      const utilDefault = this.cur(`llm.providers.${util.name}.utility_model`) || this.cur(`llm.providers.${util.name}.model`) || "the main model";
      box.appendChild(h("div", { class: "grid-2" },
        this.select("llm.utility.provider", [["", "Same as the main provider"]].concat(ai.providers.map(x => [x.name, x.label])), "Background jobs (greetings, summaries)", "A small, cheap model writes greetings and shortens long conversations."),
        this.text("llm.utility.model", "Background model", `Empty: ${utilDefault}.`),
        this.select("tools.profile", [["auto", t("Automatic (short list for models on this {pc})")], ["full", "All tools"], ["core", "Short list (apps, media, memory, web, read-only files, tasks)"], ["custom", "Custom (tools.custom_list in config.json)"]], "Tools the model sees", "Small local models do better with the short list. Permissions apply either way."),
        this.number("agent.max_turns", "Max steps per request", "Bounds multi-step tool use."),
        this.number("agent.max_seconds", "Total time limit per request (s)"),
        this.number("agent.memory_context_items", "Memories sent with each request"),
        this.number("llm.history_budget_tokens", "Conversation size before summarizing (tokens)", "API providers; Claude Code manages its own."),
        this.budgetField()));
      if (ai.problems && ai.problems.length) box.appendChild(h("div", { class: "help", style: "color:var(--color-danger, #e5484d)", text: "Not ready (saved settings): " + ai.problems.join("; ") }));
      box.appendChild(h("div", { class: "toolbar" }, h("button", { class: "btn btn-secondary", id: "btn-test", onclick: () => this.test(), text: "Test connection" }), h("div", { id: "test-line", class: "help", text: "" })));
    },
    keyRow(p) {
      const status = p.key_source === "saved" ? t("settings.key_saved")
        : p.key_source ? `Using ${p.key_source.slice(4)} from the environment.`
        : p.needs_key ? "No API key saved yet." : "No key needed (save one if your server asks for it).";
      const line = h("div", { class: "help", id: "ai-key-line", text: status });
      const pw = h("input", { class: "input", type: "password", placeholder: "Paste the API key", autocomplete: "new-password", spellcheck: "false" });
      return h("div", { class: "facts" }, h("div", { class: "toolbar" }, pw,
        h("button", { class: "btn btn-primary btn-sm", onclick: async () => { const r = JSON.parse(await call("aiSaveKey", p.name, pw.value)); pw.value = ""; if (r.ok) p.key_source = "saved"; line.textContent = r.ok ? t("settings.key_saved") : "Not saved: " + r.error; }, text: "Save key" }),
        h("button", { class: "btn btn-ghost btn-sm", onclick: async () => { await call("aiForgetKey", p.name); p.key_source = null; line.textContent = "Key removed."; }, text: "Forget key" }),
        h("button", { class: "btn btn-secondary btn-sm", onclick: () => { line.textContent = "Asking the server for its models…"; call("aiFetchModels", p.name, this.cur(`llm.providers.${p.name}.base_url`) || ""); }, text: "Fetch models" })), line);
    },
    aiFacts(p, base) {
      const model = this.cur(base + "model") || "";
      let vision = p.kind !== "openai_compat" ? true : this.cur(base + "vision");
      const guessed = vision == null; if (guessed) vision = !!p.vision_guess;
      const tags = [p.kind === "claude_code" ? "Your Claude plan" : p.local ? t("Runs on this {pc}") : "Cloud API", "Uses tools",
        (vision ? "Sees images" : "Text only") + (guessed ? " (guess)" : "")];
      if (p.kind === "anthropic") tags.push("Prompt caching");
      const profile = this.cur("tools.profile"), short = profile === "core" || (profile === "auto" && p.local);
      const tokens = (this.data.options.prompt_tokens || {})[short ? "core" : "full"] || 0, kt = Math.round(tokens / 1000);
      let cost;
      if (p.kind === "claude_code") cost = "Included in your Claude plan (its usage limits apply).";
      else if (p.local) cost = t("Free: the model runs on this {pc}, and nothing leaves it.");
      else {
        const price = ((this.data.config.llm || {}).prices || {})[model];
        cost = !price ? `About ${kt}k tokens go with each request. No price is known for this model; add one under llm.prices in config.json to see costs.`
          : `About ${kt}k tokens go with each request: ≈ $${(tokens * price.input / 1e6).toFixed(3)} for the first` +
            (p.kind === "anthropic" ? `, ≈ $${(tokens * (price.cache_read != null ? price.cache_read : price.input * 0.1) / 1e6).toFixed(4)} for later ones with caching` : "") + ", plus the answer.";
      }
      return h("div", { class: "facts" }, h("div", { class: "toolbar" }, tags.map(t => h("span", { class: "tag tag-outline", text: t }))), h("div", { class: "help", text: cost }));
    },
    aboutSection() {
      const info = h("div", { class: "kv", id: "about-kv" });
      const updateLine = h("div", { class: "help", id: "update-line", text: "" });
      const wipe = h("input", { type: "checkbox" });
      const planLine = h("div", { class: "help", id: "uninstall-line", text: "" });
      const uninstallBtn = h("button", { class: "btn btn-secondary btn-sm", text: t("about.uninstall") });
      uninstallBtn.addEventListener("click", async () => {
        if (!uninstallBtn.dataset.armed) {
          const p = JSON.parse(await call("uninstallPlan", wipe.checked) || "{}");
          const parts = [p.startup && "the start-up shortcut", (p.shortcuts || []).length && "the shortcuts", p.apps_entry && "the Apps list entry",
            (p.credentials || []).length && "saved keys and passwords", (p.folders || []).length && `your data (${p.folders.join(", ")})`,
            p.app_folder && "the app itself"].filter(Boolean);
          planLine.textContent = (parts.length ? "This removes " + parts.join(", ") + "." : "There is nothing to remove besides closing the app.") +
            (p.app_folder ? "" : " The app folder stays (it runs from source).") + " Click again to uninstall.";
          uninstallBtn.dataset.armed = "1"; uninstallBtn.textContent = t("about.uninstall.confirm");
          return;
        }
        const r = JSON.parse(await call("uninstallRun", wipe.checked) || "{}");
        planLine.textContent = r.ok ? t("about.uninstall.done") : "Couldn't uninstall: " + r.error;
      });
      wipe.addEventListener("change", () => { delete uninstallBtn.dataset.armed; uninstallBtn.textContent = t("about.uninstall"); planLine.textContent = ""; });
      const updateActions = h("div", { class: "toolbar", id: "update-actions" });
      const installBox = h("div", { id: "install-box" });
      call("aboutInfo").then(raw => {
        if (!raw) return; const a = JSON.parse(raw);
        const running = a.install && a.install.installed ? "installed copy" : a.installed ? `from ${a.install && a.install.from || "its own folder"}` : "from source";
        [["Version", a.version], ["Settings and memories", a.data], ["Models", a.models], ["Running", running]]
          .forEach(([k, v]) => info.appendChild(h("div", { class: "kv-row" }, h("span", { class: "k", text: k }), h("span", { class: "v", text: v }))));
        if (!a.updates_url) updateLine.textContent = "Update checks start once the download site is set up.";
        if (a.update_ready) this.onUpdate({ type: "update_ready", version: a.update_ready });
        if (a.install && a.install.available) {
          const desktop = h("input", { type: "checkbox" });
          installBox.append(h("div", { class: "help", text: t("about.install.help", { to: a.install.to, from: a.install.from }) }),
            h("label", { class: "check", style: "font-size:13px" }, desktop, "Also put a shortcut on the desktop"),
            h("div", { class: "toolbar" }, h("button", { class: "btn btn-secondary btn-sm", id: "install-btn", text: "Install for me",
              onclick: e => { e.target.disabled = true; call("installForMe", desktop.checked, true); } }), h("div", { class: "help", id: "install-line" })));
        }
      });
      return this.section("About", null, info,
        h("div", { class: "toolbar" }, h("button", { class: "btn btn-secondary btn-sm", onclick: () => { updateLine.textContent = "Checking…"; updateActions.textContent = ""; call("checkUpdates"); }, text: "Check for updates" }), updateLine),
        updateActions, installBox,
        this.toggle("updates.auto_check", "Check for updates when the app starts"),
        h("label", { class: "check", style: "font-size:13px" }, wipe, "When uninstalling, also delete my settings, memories, downloaded models and saved keys"),
        h("div", { class: "toolbar" }, uninstallBtn), planLine);
    },
    onUpdate(ev) {
      const line = $("update-line"), actions = $("update-actions");
      if (ev.type === "install") {
        const l = $("install-line"), b = $("install-btn");
        if (ev.state === "copying") { if (l) l.textContent = `Copying to ${ev.to}… (this takes a minute)`; }
        else if (ev.state === "done") { if (l) l.textContent = t("about.install.done"); }
        else { if (l) l.textContent = ev.error || "Installing didn't work."; if (b) b.disabled = false; }
        return;
      }
      if (!line || !actions) return;
      actions.textContent = "";
      if (ev.type === "update_progress") { line.textContent = `Downloading version ${ev.version}… ${ev.percent}%`; return; }
      if (ev.type === "update_ready") {
        line.textContent = `Version ${ev.version} is ready.`;
        actions.appendChild(h("button", { class: "btn btn-primary btn-sm", text: "Restart to update", onclick: async () => {
          line.textContent = "Restarting to update…";
          const r = JSON.parse(await call("updateRestart") || "{}"); if (!r.ok) line.textContent = r.error; } }));
        return;
      }
      line.textContent = ev.error ? ev.error : ev.available ? `Version ${ev.version} is available.` : "You have the latest version.";
      if (ev.available && ev.can_install)
        actions.appendChild(h("button", { class: "btn btn-primary btn-sm", text: "Download and install", onclick: () => { line.textContent = "Starting the download…"; actions.textContent = ""; call("updateInstall"); } }));
      else if (ev.available && ev.download && ev.download.url)
        actions.appendChild(h("button", { class: "btn btn-secondary btn-sm", text: "Get it", onclick: () => call("openUrl", ev.notes || ev.download.url) }));
    },
    async renderModels() {
      const box = $("models-body"); if (!box) return;
      const raw = await call("modelsState"); if (!raw) return;
      const m = JSON.parse(raw);
      const jobs = {}; (m.jobs || []).forEach(j => { jobs[j.option] = j; });
      box.textContent = "";
      m.groups.forEach(g => box.appendChild(h("div", { class: "models-group" },
        h("div", { class: "models-title" }, h("b", { text: g.title }), h("span", { class: "help", text: " · " + g.explain })),
        h("table", { class: "table" }, h("tbody", {}, g.options.map(o => this.modelRow(g, o, jobs[o.id])))))));
      box.appendChild(h("div", { class: "help", text: `Stored in ${m.models_dir}` }));
    },
    modelRow(g, o, job) {
      const inUse = g.selected === o.id;
      const active = job && ["queued", "downloading", "verifying", "installing"].includes(job.state);
      const state = active ? (job.state === "verifying" ? "Checking…" : job.state === "installing" ? `Installing ${job.file || "the add-on"}…` : job.state === "queued" ? "Waiting…" : `Downloading ${job.total ? Math.round(job.done / job.total * 100) : 0}%`)
        : job && job.state === "failed" ? "Download failed: " + (job.error || "")
        : o.status === "partial" && o.addon_missing ? "Needs its engine: click Download"
        : { installed: "Downloaded", builtin: "Built in", partial: "Partly downloaded" }[o.status] || "Not downloaded";
      const btn = (text, onclick, kind) => h("button", { class: "btn btn-sm " + (kind || "btn-secondary"), onclick, text });
      const files = h("div", { class: "help", hidden: true }, (o.files || []).map(f => h("div", { class: "toolbar" },
        h("span", { text: `${f.name} · ${Math.round(f.size / 1e6) || "<1"} MB` }),
        btn("Download manually", () => call("openUrl", f.url), "btn-ghost"),
        btn("I already have this file", async () => {
          const r = JSON.parse(await call("setupImportFile", o.id, f.name) || "{}");
          if (!r.cancelled) toast(r.ok ? `${f.name} is ready.` : r.error);
          this.renderModels();
        }, "btn-ghost"))));
      return h("tr", {},
        h("td", { class: "wrap" }, h("b", { text: o.name }),
          inUse ? h("span", { class: "tag tag-accent", style: "margin-left:6px", text: "in use" }) : null,
          o.recommended ? h("span", { class: "tag tag-outline", style: "margin-left:6px", text: "recommended" }) : null,
          h("div", { class: "help", text: o.summary }), files),
        h("td", { class: "num muted", text: o.size_mb ? o.size_mb + " MB" : "—" }),
        h("td", { class: "muted wrap", text: state }),
        h("td", {}, h("div", { class: "row-actions" },
          inUse ? null : btn("Use this", async () => {
            const r = JSON.parse(await call("modelsUse", o.id) || "{}");
            toast(r.ok ? `${o.name} is used after the assistant restarts.` : r.error); this.renderModels();
          }),
          (o.files || []).length && !active && o.status !== "installed" ? btn("Download", () => { call("setupDownload", o.id); }) : null,
          active ? btn("Stop", () => call("setupCancelDownload", o.id)) : null,
          (o.files || []).length && !active && o.status !== "missing" ? btn(o.status === "installed" ? "Delete" : "Remove", async e => {
            const b = e.currentTarget;
            if (!b.dataset.armed) { b.dataset.armed = "1"; b.textContent = inUse ? "In use: delete anyway?" : "Click again to delete"; setTimeout(() => { if (b.isConnected) { delete b.dataset.armed; b.textContent = "Delete"; } }, 3500); return; }
            const r = JSON.parse(await call("modelsDelete", o.id) || "{}");
            toast(r.ok ? `Deleted ${o.name} (${Math.round(r.freed / 1e6)} MB freed).` : r.error); this.renderModels();
          }, "btn-ghost") : null,
          (o.files || []).length ? btn("Files", () => { files.hidden = !files.hidden; }, "btn-ghost") : null)));
    },
    onDownload(ev) {
      if (this._modelsTimer) return;                     // progress arrives 4× a second; redraw at most once a second
      this._modelsTimer = setTimeout(() => { this._modelsTimer = null; this.renderModels(); }, ev.state === "downloading" ? 1000 : 50);
    },
    nameField() {
      const path = "assistant.name";
      const hint = h("div", { class: "help", text: t("settings.name.help") });
      const inp = h("input", { class: "input", type: "text", value: this.cur(path) || "", maxlength: "20", spellcheck: "false" });
      inp.addEventListener("input", () => {
        const value = inp.value.trim();
        call("checkAssistantName", value).then(raw => {
          const r = JSON.parse(raw || "{}");
          hint.textContent = r.error || r.warning || t("settings.name.help");
          hint.classList.toggle("warn", !!(r.error || r.warning));
          if (!r.error) { this.set(path, value); } else { delete this.changes[path]; this.renderSaveBar(); }
        });
      });
      return h("div", { class: "field" + (path in this.changes ? " changed" : ""), "data-field": path }, h("label", { text: "Name" }), inp, hint);
    },
    budgetField() {
      const path = "llm.daily_budget_usd", v = this.cur(path);
      const inp = h("input", { class: "input", type: "number", min: "0", step: "0.5", value: v == null ? "" : v, placeholder: "No limit" });
      inp.addEventListener("input", () => { const s = inp.value.trim(); if (s === "") this.set(path, null); else { const n = parseFloat(s); if (!isNaN(n)) this.set(path, n); } });
      return this.field("Daily spending limit (USD)", path, inp, t("settings.budget.help"));
    },
    onModels(ev) {
      if (ev.ok) { this.fetched = this.fetched || {}; this.fetched[ev.provider] = ev.models; if (this.provider().name === ev.provider) this.renderAI(); }
      const l = $("ai-key-line");
      if (l) l.textContent = ev.ok ? `${ev.models.length} model${ev.models.length === 1 ? "" : "s"} found: pick one in the Model field.` : "Could not list models: " + ev.error;
    },
    test() {
      $("btn-test").disabled = true; $("test-line").textContent = "Connecting and asking the model to use the clock tool…";
      call("testConnection");
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
      window.JarvisStrings.setOS(s.os);
      window.JarvisStrings.setName(s.assistant_name);
      if (s.setup_needed && window.JarvisSetup) window.JarvisSetup.open();
      if (s.ready && s.ready.type === "ready") S.info = s.ready;
      if (s.pending_approval) { S.approval = s.pending_approval; renderApproval(); }
      renderState();
      $("input").focus();
    });
  });
})();
