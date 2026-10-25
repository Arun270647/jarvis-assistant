/* Floating orb: shows the live assistant state and what Jarvis says; click opens the
   main window, drag moves it. Approvals can be answered here too. */
(function () {
  "use strict";
  const { JarvisOrb, LABEL, COLOR, BUSY, connectBridge } = window.JarvisUI;
  const $ = id => document.getElementById(id);
  let J = null;
  const S = { phase: "stopped", voiceOk: false, muted: false, level: 0, approval: null, caption: null, captionUntil: 0, errorUntil: 0, orbStyle: "hud" };

  function displayPhase() {
    if (S.approval) return "approval";
    if (S.errorUntil > Date.now() && ["idle", "stopped"].includes(S.phase)) return "error";
    if (S.phase === "idle" && !S.voiceOk) return "text";
    if (S.phase === "idle" && S.muted) return "paused";
    return S.phase;
  }

  function render() {
    const ph = displayPhase();
    document.documentElement.style.setProperty("--state-color", COLOR[ph] || COLOR.stopped);
    const label = $("mini-label");
    const showLabel = BUSY.includes(ph) || ph === "error" || ph === "paused" || ph === "stopped";
    label.hidden = !showLabel; label.textContent = LABEL[ph] || ph;
    $("mini").title = window.JarvisStrings.t("orb.mini.state", { state: LABEL[ph] || ph });
    const slot = $("caption-slot"); slot.textContent = "";
    if (S.approval) {
      const box = document.createElement("div"); box.className = "card blueprint caption";
      ["tl", "tr", "bl", "br"].forEach(c => { const i = document.createElement("i"); i.className = "corner " + c; box.appendChild(i); });
      box.appendChild(document.createTextNode(S.approval.title));
      const acts = document.createElement("div"); acts.className = "acts";
      const deny = document.createElement("button"); deny.className = "btn btn-secondary btn-sm"; deny.textContent = "Deny";
      const allow = document.createElement("button"); allow.className = "btn btn-primary btn-sm"; allow.textContent = "Allow";
      deny.onclick = () => J && J.approve(S.approval.id, false); allow.onclick = () => J && J.approve(S.approval.id, true);
      acts.append(deny, allow); box.appendChild(acts); slot.appendChild(box);
    } else if (S.caption && S.captionUntil > Date.now()) {
      const box = document.createElement("div"); box.className = "card blueprint caption";
      ["tl", "tr", "bl", "br"].forEach(c => { const i = document.createElement("i"); i.className = "corner " + c; box.appendChild(i); });
      box.appendChild(document.createTextNode(S.caption)); slot.appendChild(box);
    }
  }
  function caption(text, ms) { S.caption = text; S.captionUntil = Date.now() + (ms || 4200); render(); setTimeout(render, (ms || 4200) + 50); }

  function onEvent(json) {
    let ev; try { ev = JSON.parse(json); } catch (e) { return; }
    switch (ev.type) {
      case "phase":
        S.phase = ev.phase;
        if (ev.phase === "listening") S.level = 0;
        if (ev.phase === "speaking" && ev.text) caption(ev.text, 900 + ev.text.length * 60);
        if (ev.phase === "executing" && ev.purpose) caption(ev.purpose, 3600);
        if (ev.phase === "stopped") S.approval = null;
        break;
      case "ready": S.voiceOk = !!ev.voice; break;
      case "level": S.level = ev.v; return;
      case "transcript": caption("“" + ev.text + "”", 3600); break;
      case "approval_request": S.approval = ev; break;
      case "approval_resolved": S.approval = null; break;
      case "error": S.errorUntil = Date.now() + 3400; caption("Error · " + ev.stage + ": " + ev.detail, 6000); setTimeout(render, 3500); break;
      case "mute": S.muted = !!ev.muted; break;
      case "settings_changed":
        if (ev.config && ev.config.orb_style) S.orbStyle = ev.config.orb_style;
        if (ev.assistant_name) window.JarvisStrings.setName(ev.assistant_name);
        break;
      default: return;
    }
    render();
  }

  // Click opens the main window; dragging (more than a few pixels) moves the orb.
  let down = null;
  $("mini").addEventListener("mousedown", e => { if (e.button === 0) down = { x: e.screenX, y: e.screenY, moved: false }; });
  window.addEventListener("mousemove", e => {
    if (!down || down.moved) return;
    if (Math.abs(e.screenX - down.x) + Math.abs(e.screenY - down.y) > 5) { down.moved = true; J && J.startMove(); }
  });
  window.addEventListener("mouseup", () => { if (down && !down.moved && J) J.expand(); down = null; });
  $("mini").addEventListener("keydown", e => { if ((e.key === "Enter" || e.key === " ") && J) J.expand(); });

  new JarvisOrb($("orb"), () => ({
    phase: displayPhase(), style: S.orbStyle, level: S.level,
    approvalFrac: S.approval ? Math.max(0, (S.approval.deadline * 1000 - Date.now()) / (S.approval.timeout_s * 1000)) : 1,
  }));

  connectBridge(bridge => {
    J = bridge;
    J.event.connect(onEvent);
    J.snapshot(raw => {
      const s = JSON.parse(raw);
      S.phase = s.phase; S.voiceOk = s.voice_ok; S.muted = s.muted; S.orbStyle = s.orb_style; S.approval = s.pending_approval;
      window.JarvisStrings.setOS(s.os);
      window.JarvisStrings.setName(s.assistant_name);
      render();
    });
  });
})();
