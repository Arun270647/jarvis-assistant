/* Texts that mention the assistant, in one place. {name} is what the user calls the assistant
   (Settings › General › Name, or the setup wizard); until they pick one it is the product name, Jarvis.
   Other placeholders ({state}, {spoken}, ...) are filled by the caller. This file is also where
   translations will start, so new user-facing text that names the assistant belongs here.

   JarvisStrings.t(key, vars)   the text for key, placeholders filled
   JarvisStrings.setName(name)  rename: refreshes every element marked data-s / data-s-title / data-s-aria
   JarvisStrings.name           the current name */
(() => {
  const PRODUCT = "Jarvis";
  const STRINGS = {
    "window.title": "{name}",
    "orb.title": "{name} orb",
    "orb.start": "Start {name}",
    "orb.talk": "Talk to {name}",
    "orb.open": "Open {name}",
    "orb.mini": "{name} — click to open, drag to move",
    "orb.mini.state": "{name} — {state} · click to open, drag to move",
    "approval.spoken": "{name} asked aloud: “{spoken}”. Answer by voice (yes / no), press Y or N, type it, or choose below.",
    "approval.spoken_click": "{name} asked aloud: “{spoken}”. This one needs your click or key: press Y or N, type it, or choose below. A spoken no denies it; a spoken yes is not enough.",
    "agent.empty": "Give {name} an objective, for example “prepare the demo shop for deployment, test it and fix what you can”. Multi-step work appears here with its plan, actions, verification and outcome.",
    "activity.sub": "Every tool {name} ran for you, with its real result, and what the AI used. Live while a task runs.",
    "activity.empty": "No tool has run yet. Ask {name} to open an app, control media, or remember something.",
    "memory.sub": "What {name} remembers about you. The AI sees the relevant items with each request.",
    "memory.empty": "Nothing stored yet. Add one above, or tell {name} “remember that …”.",
    "settings.general": "What you call your assistant. It answers to this name and uses it to introduce itself.",
    "settings.name.help": "Short names with two or three syllables are easiest to recognise.",
    "settings.email": "{name} sends mail from your account over SMTP. The password is stored in Windows Credential Manager, never in config files. Mail to anyone outside the trusted list asks you first.",
    "settings.email.toggle": "Let {name} send email",
    "settings.chrome": "{name} opens your real Chrome in the profile you name (“open chrome in Marina and go to Gmail”). It does not click or type inside pages. To answer questions from the web it reads public pages in the background, without logins.",
    "settings.startup": "Start {name} when I sign in to Windows",
    "settings.untrusted": "Ask before changes after {name} reads web pages, screens or files",
    "settings.untrusted.help": "Protects against instructions hidden in what {name} reads (prompt injection). After a web page or a screen, every change asks first; after your own project files, actions outside the project do.",
    "settings.voice_high_risk.help": "Deleting files, emailing people outside your trusted list, shutting down or restarting and typing into a running program normally need a click or the Y key, so a voice in the room or on a video can't approve them.",
    "settings.extensions": "Extra tools from Python files in the tools_ext folder. {name} loads only the ones ticked here, and every extension tool still asks or is checked like the built-in ones.",
    "settings.close_to_tray": "Closing the window keeps {name} running (orb + tray)",
    "settings.budget.help": "API providers. When today's estimated cost reaches it, {name} pauses until tomorrow.",
    "about.uninstall": "Uninstall {name}",
    "about.uninstall.confirm": "Click again to uninstall {name}",
    "about.uninstall.done": "Uninstalled. {name} closes now; what's left is removed once it has closed.",
    "about.install.help": "Install for me copies {name} to {to}, adds it to the Start Menu and to Settings › Apps, and deletes the copy in {from}. No administrator rights needed.",
    "about.install.done": "Installed. {name} closes and opens again from its new folder.",
    "about.update.done": "{name} was updated to version {version}.",
    "setup.install": "{name} is running from {from}. Install it so it's in the Start Menu and Settings › Apps instead of your downloads.",
    "standalone": `This page is the ${PRODUCT} desktop UI.<br>Start it with <b>pythonw jarvis_gui.pyw</b>.`,
  };
  let name = PRODUCT;

  function t(key, vars) {
    const text = key in STRINGS ? STRINGS[key] : key;
    return text.replace(/\{(\w+)\}/g, (m, k) => (k === "name" ? name : vars && k in vars ? String(vars[k]) : m));
  }

  function setName(next) {
    name = (next || "").trim() || PRODUCT;
    document.querySelectorAll("[data-s]").forEach(el => { el.textContent = t(el.dataset.s); });
    document.querySelectorAll("[data-s-title]").forEach(el => { el.title = t(el.dataset.sTitle); });
    document.querySelectorAll("[data-s-aria]").forEach(el => { el.setAttribute("aria-label", t(el.dataset.sAria)); });
    const mark = document.getElementById("brand-mark");
    if (mark) mark.textContent = name.charAt(0).toUpperCase();
    const title = document.documentElement.dataset.sTitleKey;
    if (title) document.title = t(title);
  }

  window.JarvisStrings = { t, setName, PRODUCT, get name() { return name; } };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => setName(name));
  else setName(name);
})();
