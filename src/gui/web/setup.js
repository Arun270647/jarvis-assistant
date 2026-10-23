/* First-run setup wizard (backend: src/app/setup.py through bridge.py). Shown instead of the main window
   while setup isn't complete; resumes at the last saved screen. Every screen has one primary button, Back,
   and its extra choices behind a link. Downloads run in the background, with a bar at the bottom.

   Preloader → Name your AI → What {name} needs → Connect → Microphone → Voice → About you →
   What {name} may do → Start-up → Try it. */
(() => {
  const A = window.JarvisApp, h = A.h, call = A.call, T = window.JarvisStrings;
  const { JarvisOrb } = window.JarvisUI;
  const SCREENS = ["name", "models", "connect", "microphone", "voice", "about", "permissions", "startup", "try"];
  const W = { active: false, st: null, screen: "name", choices: {}, jobs: {}, claude: null, login: null, other: false,
              orbPhase: "idle", level: 0, mic: null, voice: null, speak: true, folders: [], tried: null, provider: null };
  const $ = id => document.getElementById(id);
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const N = s => String(s || "").replace(/\{name\}/g, T.name);
  const mb = bytes => (bytes / 1e6 >= 1000 ? (bytes / 1e9).toFixed(1) + " GB" : Math.round(bytes / 1e6) + " MB");
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // ───────────────────────────── shell
  function mount() {
    if ($("setup")) return;
    const root = h("section", { class: "setup", id: "setup", role: "dialog", "aria-modal": "true", "aria-label": "Setup" },
      h("div", { class: "setup-orb", id: "setup-orb-wrap" }, h("canvas", { id: "setup-orb", width: "520", height: "520" })),
      h("div", { class: "setup-line", id: "setup-line", "aria-live": "polite" }),
      h("nav", { class: "setup-dots", id: "setup-dots", "aria-label": "Setup progress" }),
      h("div", { class: "setup-body", id: "setup-body" }),
      h("div", { class: "setup-downloads", id: "setup-downloads", hidden: true }));
    document.body.appendChild(root);
    new JarvisOrb($("setup-orb"), () => ({ phase: W.orbPhase, style: "hud", level: W.level, approvalFrac: 1 }));
  }

  async function open() {
    mount();
    W.active = true;
    document.body.classList.add("in-setup");
    $("setup").classList.add("preloading");
    const started = performance.now();
    typeLine("Welcome.");
    const raw = await Promise.all([call("setupState"), sleep(700).then(() => typeLine("Waking up…"))]).then(r => r[0]);
    W.st = JSON.parse(raw || "{}");
    T.setName(W.st.name);
    (W.st.downloads || []).forEach(j => { W.jobs[j.option] = j; });
    W.speak = W.st.speak; W.folders = (W.st.folders || []).slice(); W.mic = W.st.microphone;
    W.st.groups.forEach(g => { W.choices[g.id] = g.selected; });
    W.voice = W.choices.tts;
    await sleep(Math.max(0, 1500 - (performance.now() - started)));      // never flash, never fake progress
    typeLine("Let's get you set up.");
    await sleep(reduceMotion ? 300 : 900);
    $("setup").classList.remove("preloading");
    $("setup-line").textContent = "";
    const saved = SCREENS.indexOf(W.st.step);
    go(W.st.step && saved >= 0 && saved < SCREENS.length - 1 && W.st.step !== SCREENS[0] ? SCREENS[saved + 1] : SCREENS[0]);
    renderDownloads();
  }

  function typeLine(text) {
    const el = $("setup-line"); if (!el) return;
    if (reduceMotion) { el.textContent = text; return; }
    el.textContent = ""; let i = 0;
    const tick = () => { if (i <= text.length) { el.textContent = text.slice(0, i++); setTimeout(tick, 28); } };
    tick();
  }

  function close() {
    W.active = false;
    document.body.classList.remove("in-setup");
    const el = $("setup"); if (el) el.remove();
  }

  function go(screen) {
    W.screen = screen;
    W.orbPhase = "idle";
    renderDots();
    render();
    const first = document.querySelector("#setup-body input, #setup-body .btn-primary");
    if (first) setTimeout(() => first.focus(), 30);
  }
  const next = () => go(SCREENS[Math.min(SCREENS.length - 1, SCREENS.indexOf(W.screen) + 1)]);
  const back = () => go(SCREENS[Math.max(0, SCREENS.indexOf(W.screen) - 1)]);

  function renderDots() {
    const box = $("setup-dots"); box.textContent = "";
    SCREENS.forEach((s, i) => box.appendChild(h("span", { class: "dot" + (s === W.screen ? " on" : SCREENS.indexOf(W.screen) > i ? " done" : ""),
      title: s, "aria-current": s === W.screen ? "step" : null })));
  }

  function page(title, lead, body, actions) {
    const box = $("setup-body"); box.textContent = "";
    box.appendChild(h("div", { class: "setup-card card blueprint" }, A.corners(),
      h("h1", { class: "setup-title", text: title }),
      lead ? h("p", { class: "setup-lead", text: lead }) : null,
      h("div", { class: "setup-content" }, body),
      h("div", { class: "setup-actions" },
        SCREENS.indexOf(W.screen) > 0 ? h("button", { class: "btn btn-ghost", onclick: back, text: "Back" }) : h("span"),
        h("span", { style: "flex:1" }), ...(actions || []))));
  }
  const primary = (text, onclick, disabled) => h("button", { class: "btn btn-primary", onclick, disabled: !!disabled, text });
  const secondary = (text, onclick) => h("button", { class: "btn btn-secondary", onclick, text });
  const link = (text, onclick) => h("button", { class: "setup-link", onclick, text });
  const note = (text, kind) => h("div", { class: "setup-note" + (kind ? " " + kind : ""), text });

  async function save(step, data) {
    const r = JSON.parse(await call("setupSave", step, JSON.stringify(data || {})) || "{}");
    if (!r.ok) A.toast(r.error || "Couldn't save that.");
    return r.ok;
  }

  function render() {
    ({ name: screenName, models: screenModels, connect: screenConnect, microphone: screenMic, voice: screenVoice,
       about: screenAbout, permissions: screenPermissions, startup: screenStartup, try: screenTry })[W.screen]();
  }

  // ───────────────────────────── 1. Name your AI
  function screenName() {
    const hint = note("Short names with two or three syllables are easiest to recognise.");
    const inp = h("input", { class: "input setup-name", value: T.name, maxlength: "20", spellcheck: "false", "aria-label": "Name" });
    let valid = true;
    const cont = primary("Continue", async () => {
      if (!valid) return;
      if (await save("name", { name: inp.value.trim() })) { T.setName(inp.value.trim()); await reload(); next(); }
    });
    const check = async () => {
      const r = JSON.parse(await call("checkAssistantName", inp.value.trim()) || "{}");
      valid = !r.error; cont.disabled = !valid;
      hint.textContent = r.error || r.warning || "Short names with two or three syllables are easiest to recognise.";
      hint.className = "setup-note" + (r.error || r.warning ? " warn" : "");
    };
    inp.addEventListener("input", check);
    inp.addEventListener("keydown", e => { if (e.key === "Enter") cont.click(); });
    page("Name your AI", "This is what you'll call your assistant. It will answer to this name and use it to introduce itself.",
      [h("div", { class: "toolbar" }, inp, secondary("▶ Hear it", () => call("hearName", inp.value.trim() || T.name))),
       h("div", { class: "chips" }, h("span", { class: "chips-label", text: "Ideas:" }),
         W.st.name_ideas.map(n => h("button", { class: "chip", onclick: () => { inp.value = n; check(); }, text: n }))),
       hint], [cont]);
  }

  // ───────────────────────────── 1b. What {name} needs
  function screenModels() {
    const groups = W.st.groups.filter(g => g.id !== "tts");
    const voice = W.st.groups.find(g => g.id === "tts");
    const rows = groups.concat(voice ? [voice] : []).map(g => modelRow(g));
    const total = W.st.groups.reduce((sum, g) => {
      const o = g.options.find(x => x.id === W.choices[g.id]); return sum + (o && o.status !== "installed" ? o.size_mb : 0);
    }, 0);
    page(`${T.name} needs a few free AI models to hear and speak.`,
      "They run on your computer, so your voice never leaves it. We picked the best ones for your computer.",
      [h("div", { class: "model-table" }, rows,
        h("div", { class: "model-row" }, h("div", { class: "model-what" }, h("b", { text: "AI brain" }),
          h("div", { class: "help", text: "Understands requests and decides what to do." })),
          h("div", { class: "model-pick", text: "Claude (online, next screen)" }), h("div", { class: "model-size", text: "—" }), h("div"))),
       note(total ? `Total: about ${total >= 1000 ? (total / 1000).toFixed(1) + " GB" : total + " MB"}. Stored in ${W.st.models_dir}`
         : `Everything is already downloaded. Stored in ${W.st.models_dir}`)],
      [primary(total ? "Download and continue" : "Continue", async () => {
        const choices = {}; groups.forEach(g => { choices[g.id] = W.choices[g.id]; });
        if (voice) choices.tts = W.choices.tts;
        if (await save("models", { choices })) next();
      }), h("span", { class: "help", text: total ? "Downloading happens in the background while you finish setup." : "" })]);
  }

  function modelRow(g) {
    const sel = g.options.find(o => o.id === W.choices[g.id]) || g.options[0];
    const details = h("div", { class: "model-details", hidden: true });
    const showDetails = (o, mode) => {
      details.hidden = false; details.textContent = "";
      if (mode === "change") {
        details.appendChild(h("div", { class: "options" }, g.options.map(x => h("label", { class: "option" + (x.id === sel.id ? " on" : "") },
          h("input", { type: "radio", name: "opt-" + g.id, checked: x.id === sel.id, onchange: () => { W.choices[g.id] = x.id; if (g.id === "tts") W.voice = x.id; render(); } }),
          h("span", { class: "option-name", text: x.name }), x.recommended ? h("span", { class: "tag tag-accent", text: "Recommended" }) : null,
          h("span", { class: "help", text: x.summary }), h("span", { class: "option-size", text: x.size_mb ? x.size_mb + " MB" : "built in" })))));
        return;
      }
      details.append(h("p", { text: o.about || o.summary }),
        h("div", { class: "help", text: "License: " + o.license }),
        h("div", { class: "toolbar" },
          o.page ? secondary("Official page", () => call("openUrl", o.page)) : null,
          o.license_url ? link("License", () => call("openUrl", o.license_url)) : null),
        ...(o.files || []).map(f => h("div", { class: "file-row" }, h("span", { class: "file-name", text: `${f.name} · ${mb(f.size)}` }),
          link("Download manually", () => call("openUrl", f.url)),
          link("I already have this file", async () => {
            const r = JSON.parse(await call("setupImportFile", o.id, f.name) || "{}");
            if (!r.cancelled) A.toast(r.ok ? `${f.name} is ready.` : r.error);
            if (r.ok) refreshState();
          }))));
    };
    const status = sel.status === "installed" ? "✓ downloaded" : sel.status === "builtin" ? "built in" : sel.size_mb + " MB";
    const reason = sel.id === g.recommended && g.reason ? ` (${g.reason})` : "";
    return h("div", { class: "model-group" },
      h("div", { class: "model-row" },
        h("div", { class: "model-what" }, h("b", { text: g.title }), h("div", { class: "help", text: g.explain })),
        h("div", { class: "model-pick" }, h("span", { text: sel.name }), h("span", { class: "help", text: reason })),
        h("div", { class: "model-size", text: status }),
        h("div", { class: "model-actions" },
          g.options.length > 1 ? link("Change", () => showDetails(sel, "change")) : null,
          h("button", { class: "info-btn", title: "Details", "aria-label": `Details about ${sel.name}`, onclick: () => showDetails(sel, "info"), text: "ⓘ" }))),
      details);
  }

  async function reload() {                      // texts follow the name, statuses follow the downloads
    const raw = await call("setupState"); if (raw) W.st = JSON.parse(raw);
  }
  async function refreshState() { await reload(); render(); }

  // ───────────────────────────── 2. Connect your AI
  function screenConnect() {
    if (W.claude == null) { call("claudeStatus"); }
    const c = W.claude || {}, login = W.login;
    const body = [];
    let cont = null;
    if (login === "waiting") {
      W.orbPhase = "thinking";
      body.push(note("Finish signing in in your browser, then come back here."),
        h("div", { class: "toolbar" }, secondary("Open the sign-in page again", () => call("claudeLogin")), link("Cancel", () => call("claudeLoginCancel"))));
    } else if (c.signed_in) {
      W.orbPhase = "idle";
      body.push(note(`✓ Signed in as ${c.email || "your Claude account"}${c.plan ? ` (${c.plan} plan)` : ""}.`, "ok"));
      cont = primary("Continue", () => next());
    } else if (W.claude == null) {
      body.push(note("Checking Claude…"));
    } else if (!c.installed) {
      body.push(note("Claude needs Claude Code, Anthropic's official app, on this computer. Install it, then come back here."),
        h("div", { class: "toolbar" }, secondary("Get Claude Code", () => call("openUrl", "https://docs.claude.com/en/docs/claude-code/setup")),
          link("Check again", () => { W.claude = null; render(); })));
    } else {
      if (login === "timeout") body.push(note("Sign-in wasn't finished.", "warn"));
      if (login === "error") body.push(note("Couldn't start the sign-in.", "warn"));
      body.push(h("button", { class: "btn btn-primary btn-big", onclick: () => { W.login = "waiting"; render(); call("claudeLogin"); },
        text: login === "timeout" || login === "error" ? "Try again" : "Sign in with Claude" }),
        note("Opens your browser. You can sign in with Google or your email."),
        h("div", { class: "help", text: "Uses your Claude subscription through Claude Code, Anthropic's official app." }));
    }
    body.push(h("div", { class: "setup-sep" }), link(W.other ? "Hide other ways to connect" : "Other ways to connect ›", () => { W.other = !W.other; render(); }));
    if (W.other) body.push(otherWays());
    const done = c.signed_in || W.provider === "ok";
    page(`Connect ${T.name} to an AI`, `${T.name} uses Claude to understand what you ask. Sign in with your Claude account (Pro or Max plan).`, body,
      [cont || (done ? primary("Continue", () => next()) : link("Set this up later in Settings", () => next()))]);
  }

  function otherWays() {
    const providers = (W.st.ai.providers || []).filter(p => p.kind !== "claude_code");
    const pick = h("select", { class: "input" }, providers.map(p => h("option", { value: p.name, text: p.label })));
    const key = h("input", { class: "input", type: "password", placeholder: "API key", autocomplete: "new-password" });
    const model = h("input", { class: "input", placeholder: "Model (Fetch models lists them)", list: "setup-models", spellcheck: "false" });
    const list = h("datalist", { id: "setup-models" });
    const line = note(W.providerLine || "");
    const local = () => (providers.find(p => p.name === pick.value) || {}).local;
    const refresh = () => { key.hidden = local(); };
    pick.addEventListener("change", refresh); refresh();
    W.onModels = models => { list.textContent = ""; models.forEach(m => list.appendChild(h("option", { value: m }))); line.textContent = `${models.length} models found.`; };
    return h("div", { class: "other-ways" },
      h("div", { class: "grid-2" }, h("div", { class: "field" }, h("label", { text: "Provider" }), pick),
        h("div", { class: "field" }, h("label", { text: "API key (not needed for a model on this PC)" }), key),
        h("div", { class: "field" }, h("label", { text: "Model" }), model, list)),
      h("div", { class: "toolbar" },
        secondary("Fetch models", async () => { if (key.value) await call("aiSaveKey", pick.value, key.value); line.textContent = "Asking for the model list…"; call("aiFetchModels", pick.value, ""); }),
        h("button", { class: "btn btn-primary btn-sm", onclick: async () => {
          if (!model.value.trim()) { line.textContent = "Pick a model first."; return; }
          if (key.value) { const r = JSON.parse(await call("aiSaveKey", pick.value, key.value) || "{}"); if (!r.ok) { line.textContent = r.error; return; } }
          const r = JSON.parse(await call("settingsSave", JSON.stringify({ "llm.main.provider": pick.value, [`llm.providers.${pick.value}.model`]: model.value.trim() })) || "{}");
          if (!r.ok) { line.textContent = r.error; return; }
          line.textContent = "Checking: the model has to answer and use a tool…"; W.orbPhase = "thinking"; call("testConnection");
        }, text: "Check" }),
        local() ? link("Get Ollama", () => call("openUrl", "https://ollama.com/download")) : null),
      line);
  }

  // ───────────────────────────── 3. Microphone
  function screenMic() {
    const devices = W.st.devices || [];
    const sel = h("select", { class: "input" }, [h("option", { value: "null", text: "System default microphone" })].concat(
      devices.map(d => h("option", { value: String(d.index), selected: String(d.index) === String(W.mic), text: d.name }))));
    sel.addEventListener("change", () => { W.mic = sel.value === "null" ? null : parseInt(sel.value, 10); });
    const meter = h("div", { class: "meter" }, h("div", { class: "meter-fill", id: "mic-meter" }));
    const result = h("div", { id: "mic-result" });
    const test = secondary(`Test: say “Hello ${T.name}”`, () => { result.textContent = "Listening for 5 seconds…"; W.orbPhase = "listening"; call("micTest", sel.value); });
    page("Let's check your microphone", `${T.name} only listens when you click the orb or hold the talk key. Nothing is recorded in the background.`,
      devices.length ? [h("div", { class: "field" }, h("label", { text: "Microphone" }), sel), meter, h("div", { class: "toolbar" }, test), result]
        : [note(`No microphone found. You can still type to ${T.name}.`, "warn")],
      [primary(devices.length ? "Continue" : "Continue without voice input", async () => { if (await save("microphone", { device: W.mic })) next(); })]);
  }

  function onMicTest(ev) {
    W.orbPhase = "idle"; W.level = 0;
    const box = $("mic-result"); if (!box) return;
    box.textContent = "";
    if (ev.ok) box.appendChild(note(ev.heard ? `✓ I heard: “${ev.heard.trim()}”` : "✓ I can hear you. (Speech recognition is still downloading.)", "ok"));
    else if (ev.blocked) box.append(note(`${T.name} can't use the microphone because it's turned off in privacy settings.`, "warn"),
      h("div", { class: "toolbar" }, secondary("Open privacy settings", () => call("openMicPrivacy"))));
    else box.appendChild(note(ev.error || "That didn't work.", "warn"));
  }

  // ───────────────────────────── 4. Voice
  function screenVoice() {
    const g = W.st.groups.find(x => x.id === "tts");
    const audio = h("audio", { preload: "none" });
    const cards = (g ? g.options : []).map(o => h("label", { class: "voice-card" + (o.id === W.voice ? " on" : "") },
      h("input", { type: "radio", name: "voice", checked: o.id === W.voice, onchange: () => { W.voice = o.id; render(); } }),
      h("span", { class: "option-name", text: o.name }),
      h("button", { class: "btn btn-ghost btn-sm", title: "Play a sample", "aria-label": `Play a sample of ${o.name}`,
        onclick: e => { e.preventDefault(); if (o.id === "system") call("hearName", T.name); else if (o.sample) { audio.src = o.sample; audio.play(); W.orbPhase = "speaking"; } }, text: "▶" }),
      h("span", { class: "help", text: o.summary + (o.size_mb ? ` · ${o.size_mb} MB` : " · nothing to download") })));
    audio.addEventListener("ended", () => { W.orbPhase = "idle"; });
    const quiet = h("input", { type: "checkbox" }); quiet.checked = !W.speak;
    quiet.addEventListener("change", () => { W.speak = !quiet.checked; });
    page("Pick a voice", null, [h("div", { class: "voices" }, cards), audio,
      h("label", { class: "check" }, quiet, "Don't speak, just show text")],
      [primary("Continue", async () => { if (await save("voice", { option: W.voice, speak: W.speak })) next(); })]);
  }

  // ───────────────────────────── 5. About you
  function screenAbout() {
    const user = h("input", { class: "input", value: W.st.user_name || "", placeholder: "Your name", spellcheck: "false" });
    const list = h("div", { class: "folders" });
    const draw = () => { list.textContent = ""; W.folders.forEach((f, i) => list.appendChild(h("div", { class: "file-row" }, h("span", { class: "file-name", text: f }),
      link("Remove", () => { W.folders.splice(i, 1); draw(); })))); };
    draw();
    const done = async () => { if (await save("about", { user_name: user.value.trim(), folders: W.folders })) next(); };
    page("About you", null, [h("div", { class: "field" }, h("label", { text: "What should I call you?" }), user),
      h("div", { class: "field" }, h("label", { text: "Folders I can work in (optional)" }), list,
        h("div", { class: "toolbar" }, secondary("+ Add a folder", async () => { const p = await call("pickFolder"); if (p && !W.folders.includes(p)) { W.folders.push(p); draw(); } })),
        h("div", { class: "help", text: `${T.name} can read files in these folders. It will ask before changing anything.` }))],
      [link("Skip", () => next()), primary("Continue", done)]);
  }

  // ───────────────────────────── 6. What {name} may do
  function screenPermissions() {
    let preset = W.preset || W.st.preset;
    const cards = W.st.presets.map(p => h("label", { class: "preset" + (p.id === preset ? " on" : "") },
      h("input", { type: "radio", name: "preset", checked: p.id === preset, onchange: () => { W.preset = p.id; render(); } }),
      h("span", { class: "option-name", text: p.title }), h("span", { class: "help", text: p.text })));
    page(`What may ${T.name} do?`, null, [h("div", { class: "presets" }, cards),
      note(`You can change this any time in Settings. ${T.name} always asks before deleting, sending email or shutting down.`)],
      [primary("Continue", async () => { if (await save("permissions", { preset: W.preset || W.st.preset })) next(); })]);
  }

  // ───────────────────────────── 7. Start-up
  function screenStartup() {
    const cb = h("input", { type: "checkbox" }); cb.checked = W.autostart != null ? W.autostart : true;
    cb.addEventListener("change", () => { W.autostart = cb.checked; });
    page("Start-up", null, [h("label", { class: "check" }, cb, `Start ${T.name} when I sign in to my computer`),
      note("Your shortcut to talk: Ctrl+Shift+J")],
      [primary("Continue", async () => { if (await save("startup", { autostart: cb.checked })) next(); })]);
  }

  // ───────────────────────────── 8. Try it
  function screenTry() {
    call("setupStartAssistant");
    const input = h("input", { class: "input", placeholder: "Or type it here", spellcheck: "false" });
    const out = h("div", { class: "try-out", id: "try-out", "aria-live": "polite" }, W.tried ? [note(`${T.name}: ${W.tried}`, "ok"),
      note("🎉 That's it! More ideas: “Open Notepad”, “Play music”, “Remember that my car is parked on level 3”.")] : []);
    const send = () => { if (input.value.trim()) { call("send", input.value.trim()); input.value = ""; } };
    input.addEventListener("keydown", e => { if (e.key === "Enter") send(); });
    const downloading = Object.values(W.jobs).some(j => ["queued", "downloading", "verifying"].includes(j.state));
    page("You're ready. Try it now.", `Click the orb and say: “What time is it?”`,
      [h("div", { class: "toolbar" }, secondary("🎙 Talk", () => call("listen")), input, secondary("Send", send)),
       h("div", { class: "help", id: "try-state", text: W.tryState || "" }),
       downloading ? note("Voice turns on by itself when the downloads finish; typing works now.") : null, out],
      [primary("Finish", async () => { if (await save("finish", {})) { close(); A.toast(`${T.name} is ready.`); } })]);
  }

  // ───────────────────────────── downloads bar
  function renderDownloads() {
    const bar = $("setup-downloads"); if (!bar) return;
    const jobs = Object.values(W.jobs).filter(j => j.state !== "builtin");
    const active = jobs.find(j => ["downloading", "verifying"].includes(j.state)) || jobs.find(j => j.state === "queued");
    const failed = jobs.find(j => j.state === "failed");
    bar.hidden = !active && !failed;
    bar.textContent = "";
    if (failed && !active) {
      bar.append(h("span", { text: `Download paused: ${failed.name}. ${failed.error || ""}` }),
        link("Retry", () => call("setupDownload", failed.option)));
      return;
    }
    if (!active) return;
    const pct = active.total ? Math.round(active.done / active.total * 100) : 0;
    const eta = active.eta_s ? ` · about ${active.eta_s > 90 ? Math.round(active.eta_s / 60) + " minutes" : active.eta_s + " s"}` : "";
    bar.append(h("span", { text: active.state === "verifying" ? `Checking ${active.name}…`
      : active.state === "queued" ? `Waiting to download ${active.name}…` : `Downloading ${active.name}… ${mb(active.done)} of ${mb(active.total)}${eta}` }),
      h("div", { class: "meter small" }, h("div", { class: "meter-fill", style: `width:${pct}%` })));
  }

  // ───────────────────────────── events
  function onEvent(ev) {
    switch (ev.type) {
      case "download": W.jobs[ev.option] = ev; renderDownloads(); if (ev.state === "done" && W.screen === "models") refreshState(); break;
      case "claude_status": W.claude = ev; if (W.screen === "connect") render(); break;
      case "claude_login": W.login = ev.state === "signed_in" ? null : ev.state; if (ev.state === "signed_in") W.claude = Object.assign({}, ev, { installed: true });
        if (W.screen === "connect") render(); break;
      case "settings_test": W.orbPhase = "idle"; W.provider = ev.ok ? "ok" : "failed";
        W.providerLine = (ev.ok ? "✓ Works: " : "✗ ") + ev.detail; if (W.screen === "connect") render(); break;
      case "ai_models": if (W.onModels && ev.ok) W.onModels(ev.models); break;
      case "mic_level": W.level = ev.v; { const m = $("mic-meter"); if (m) m.style.width = Math.round(ev.v * 100) + "%"; } break;
      case "mic_test": onMicTest(ev); break;
      case "phase": W.orbPhase = ev.phase; if (W.screen === "try") { W.tryState = { listening: "Listening…", transcribing: "Listening…", thinking: "Thinking…", executing: "Working…", speaking: "Speaking…", loading: "Starting up…" }[ev.phase] || ""; const s = $("try-state"); if (s) s.textContent = W.tryState; } break;
      case "reply": if (W.screen === "try" && !ev.greeting) { W.tried = ev.text; render(); } break;
      case "settings_changed": if (ev.assistant_name) T.setName(ev.assistant_name); break;
      default: break;
    }
  }

  window.JarvisSetup = { open, onEvent, go, get active() { return W.active; } };     // go: screenshots and dev checks
})();
