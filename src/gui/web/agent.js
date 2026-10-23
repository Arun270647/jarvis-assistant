/* Agent workspace: tasks from the database with live updates.
   Shows objective, status, plan, success checks + verification evidence, the event timeline,
   and real controls (pause / resume with an answer / cancel / retry). No chain-of-thought:
   only actions, evidence, decisions and outcomes recorded by the task layer. */
(function () {
  "use strict";
  const A = window.JarvisApp;
  const { h, corners, call, dstr, toast } = A;
  const $ = id => document.getElementById(id);
  const ACTIVE = ["QUEUED", "PLANNING", "RUNNING", "WAITING_FOR_APPROVAL", "VERIFYING"];
  const TERMINAL = ["COMPLETED", "FAILED", "CANCELLED"];
  const KIND_LABEL = {
    accepted: "objective", inspected: "inspection", plan: "plan", checks_changed: "criteria", tool: "action",
    tool_ok: "succeeded", tool_failed: "failed", tool_blocked: "not run", retry: "retry", step: "step",
    progress: "progress", approval: "approval", verifying: "verifying", check_pass: "check passed",
    check_fail: "check failed", completed: "completed", failed: "failed", blocked: "blocked", paused: "paused",
    resumed: "resumed", cancelled: "cancelled", continue: "continuing", limit: "limit", interrupted: "interrupted",
    capability_gap: "missing tool",
  };

  const V = { tasks: [], selected: null, detail: null, visible: false, listTimer: null, newCount: 0 };

  const statusTag = st => h("span", { class: "tag status-tag st-" + st, text: st.replace(/_/g, " ").toLowerCase() });
  const elapsed = t => {
    const end = t.ended_at || (ACTIVE.includes(t.status) ? Date.now() / 1000 : t.updated_at);
    const s = Math.max(0, Math.round(end - t.created_at));
    return s < 60 ? s + " s" : Math.floor(s / 60) + " min " + (s % 60) + " s";
  };

  async function loadList() {
    const raw = await call("taskList", 60); if (raw == null) return;
    const d = JSON.parse(raw);
    V.tasks = d.tasks;
    if (V.selected == null && V.tasks.length) V.selected = (d.active || V.tasks[0].id);
    renderList();
  }

  function renderList() {
    const box = $("task-items"); if (!box) return; box.textContent = "";
    if (!V.tasks.length) { box.appendChild(h("div", { class: "empty", style: "padding:14px", text: "No tasks yet." })); return; }
    for (const t of V.tasks) {
      box.appendChild(h("button", { class: "task-item", "aria-current": t.id === V.selected ? "true" : "false", onclick: () => select(t.id) },
        h("div", { class: "ti-top" }, h("span", { text: "#" + t.id }), statusTag(t.status), t.project ? h("span", { text: t.project }) : null,
          h("span", { style: "margin-left:auto", text: dstr(t.updated_at * 1000) })),
        h("div", { class: "ti-obj", text: t.objective })));
    }
  }

  async function select(id) {
    V.selected = id; renderList();
    const raw = await call("taskGet", id); if (raw == null) return;
    V.detail = JSON.parse(raw);
    renderDetail();
  }

  function control(label, fn, primary) {
    return h("button", { class: "btn " + (primary ? "btn-primary" : "btn-secondary"), onclick: fn, text: label });
  }

  function renderDetail() {
    const root = $("task-detail"); root.textContent = "";
    const t = V.detail;
    const inner = h("div", { class: "page-inner" });
    root.appendChild(inner);
    if (!t) { inner.appendChild(h("div", { class: "empty", text: "Select a task." })); return; }

    const controls = [];
    if (ACTIVE.includes(t.status)) controls.push(control("Pause", () => act("taskPause", t.id, "Pausing…")));
    if (t.status === "BLOCKED" || t.status === "PAUSED") controls.push(control("Resume", () => act("taskResume", t.id, "Resuming…", answerValue()), true));
    if (!TERMINAL.includes(t.status)) controls.push(control("Cancel task", () => act("taskCancel", t.id, "Cancelling…")));
    if (t.status === "FAILED" || t.status === "CANCELLED" || t.status === "BLOCKED") controls.push(control("Retry as new task", () => act("taskRetry", t.id, "Retrying…")));

    inner.appendChild(h("div", { class: "task-head" },
      h("div", { class: "card-kicker", text: "Task #" + t.id + (t.parent_id ? " · retry of #" + t.parent_id : "") }),
      h("h2", { text: t.objective }),
      h("div", { class: "task-meta" }, statusTag(t.status), t.project ? h("span", { text: "project " + t.project }) : null,
        h("span", { text: "started " + dstr(t.created_at * 1000) }), h("span", { text: "elapsed " + elapsed(t) }),
        h("span", { text: (t.tool_calls || 0) + " actions" }), t.verify_attempts ? h("span", { text: t.verify_attempts + " verification run(s)" }) : null,
        t.attempts ? h("span", { text: "resumed " + t.attempts + "×" }) : null),
      controls.length ? h("div", { class: "task-controls" }, controls) : null));

    if ((t.status === "BLOCKED" || t.status === "PAUSED") && t.blocked_reason) {
      inner.appendChild(h("div", { class: "blocked-box" }, h("div", { class: "section-title", text: t.status === "PAUSED" ? "Paused" : "Waiting for you" }),
        h("div", { class: "selectable", text: t.blocked_reason }),
        h("input", { class: "input", id: "task-answer", placeholder: "Optional answer or instruction, then press Resume" })));
    }
    if (t.summary) inner.appendChild(section("Outcome", h("div", { class: "selectable", style: "font-size:14px", text: t.summary })));

    const plan = t.plan || [];
    inner.appendChild(section("Plan", plan.length ? h("div", { class: "steps" }, plan.map(s => h("div", { class: "step" },
      h("span", {}, h("i", { class: "step-ico " + (s.status || "pending"), title: s.status || "pending" })),
      h("span", { class: "sid", text: s.id }), h("span", { text: s.title }),
      h("span", { class: "tag tag-neutral", style: "font-size:10px", text: (s.status || "pending") + ((s.depends_on || []).length ? " · after " + s.depends_on.join(", ") : "") }),
      s.note ? h("span", { class: "snote", text: s.note }) : null))) : h("div", { class: "empty", text: "No plan yet." })));

    const checks = t.checks || [];
    const ver = {};
    (t.verification || []).forEach(r => { ver[r.n] = r; });
    inner.appendChild(section("Success checks", checks.length ? h("div", { class: "checks" }, checks.map((c, i) => {
      const r = ver[i + 1];
      return h("div", { class: "chk" },
        h("span", { class: "tag " + (r ? (r.passed ? "tag-accent" : "tag-outline") : "tag-neutral"), style: "font-size:10px; justify-content:center", text: r ? (r.passed ? "pass" : "fail") : "pending" }),
        h("div", {}, h("div", { text: c.description }), h("div", { style: "font-size:11px; color:var(--dim-45)", text: describeCheck(c) }),
          r && r.evidence ? h("pre", { text: r.evidence }) : null));
    })) : h("div", { class: "empty", text: "Defined when the plan is created." })));

    const tl = h("div", { class: "timeline", id: "task-timeline" });
    (t.events || []).forEach(e => tl.appendChild(eventRow(e)));
    inner.appendChild(section("Timeline", tl));
  }

  function describeCheck(c) {
    switch (c.type) {
      case "command": return "runs " + (c.command || []).join(" ") + " (expects exit " + (c.expect_exit || 0) + (c.expect_text ? `, “${c.expect_text}”` : "") + ")";
      case "file_exists": return "file exists: " + c.path;
      case "file_contains": return `${c.path} contains “${c.text}”`;
      case "process_running": return "process running: " + c.name;
      case "http_status": return "GET " + c.url + " → " + (c.expect_status || 200);
      default: return c.type;
    }
  }

  function eventRow(e) {
    return h("div", { class: "tl kind-" + e.kind }, h("span", { class: "t", text: new Date(e.created_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) }),
      h("span", { class: "k", text: KIND_LABEL[e.kind] || e.kind }), h("span", { class: "m", text: e.message }));
  }

  function section(title, body) {
    return h("div", { class: "card blueprint section" }, corners(), h("div", { class: "section-title", text: title }), body);
  }

  const answerValue = () => { const el = $("task-answer"); return el ? el.value.trim() : ""; };

  async function act(slot, id, msg, extra) {
    const r = slot === "taskResume" ? await call(slot, id, extra || "") : await call(slot, id);
    toast(r === "rejected" ? "The assistant is not running. Press Start." : r === "not-active" ? "That task is no longer active." : msg);
    setTimeout(() => select(id), 400);
  }

  function refreshListSoon() { clearTimeout(V.listTimer); V.listTimer = setTimeout(loadList, 300); }

  // Live events from the backend (forwarded by app.js).
  function onEvent(ev) {
    if (ev.type === "task_update" && ev.task) {
      const t = ev.task;
      if (!V.tasks.some(x => x.id === t.id)) { V.newCount++; if (!V.visible) badge(); if (ACTIVE.includes(t.status)) V.selected = t.id; }
      refreshListSoon();
      if (V.visible && t.id === V.selected) {
        V.detail = Object.assign({}, V.detail && V.detail.id === t.id ? V.detail : {}, t, { events: (V.detail && V.detail.id === t.id ? V.detail.events : []) || [] });
        renderDetail();
      }
    } else if (ev.type === "task_event" && ev.event) {
      const e = ev.event;
      if (V.visible && V.detail && V.detail.id === e.task_id) {
        V.detail.events = (V.detail.events || []).concat([e]);
        const tl = $("task-timeline");
        if (tl) tl.appendChild(eventRow(e));
      }
    }
  }

  function badge() {
    const c = $("agent-count");
    if (!c) return;
    c.hidden = V.visible || V.newCount === 0; c.textContent = V.newCount;
  }

  function show() {
    V.visible = true; V.newCount = 0; badge();
    loadList().then(() => { if (V.selected != null) select(V.selected); });
  }
  function hide() { V.visible = false; }

  $("tasks-refresh") && $("tasks-refresh").addEventListener("click", () => show());
  A.agent = { onEvent, show, hide, select };
})();
