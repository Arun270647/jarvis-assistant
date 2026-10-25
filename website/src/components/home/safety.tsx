import { ArrowRight } from "lucide-react";
import Link from "next/link";

/* docs/SECURITY_MODEL.md, "In short", with an illustrative activity list. */

const POINTS = [
  { title: "Risky abilities start switched off", text: "Changing files, Command Prompts, controlling other apps, email and hidden coding-agent runs wait until you turn them on." },
  { title: "One checkpoint for every AI", text: "Whichever model you use, each action is allowed, asked about first, or denied by the same rules." },
  { title: "Some things always need your hand", text: "Deleting a file, emailing someone new, shutting down or restarting: a spoken “yes” isn't enough, you click Allow or press Y." },
  { title: "Hidden instructions don't get a say", text: "After Jarvis reads a web page, a screen or a file, actions that change something ask you first." },
];

const LOG: { call: string; verdict: "allow" | "ask" | "deny"; why: string }[] = [
  { call: 'open_app("notepad")', verdict: "allow", why: "Basics" },
  { call: 'read_webpage("recipe site")', verdict: "allow", why: "now reading outside content" },
  { call: 'send_email("…")', verdict: "ask", why: "after reading a web page" },
  { call: 'system_command("restart")', verdict: "ask", why: "click or Y only" },
  { call: 'read_file("my-project/.env")', verdict: "deny", why: "secret files are never opened" },
];

const VERDICT = {
  allow: "text-[#74e1ff] border-[#74e1ff]/35 bg-[#35cdfa]/10",
  ask: "text-[#ffcf7a] border-[#ffcf7a]/40 bg-[#ffcf7a]/10",
  deny: "text-[#ff8a8a] border-[#ff8a8a]/40 bg-[#ff8a8a]/10",
};

export function Safety() {
  return (
    <section aria-labelledby="safety" className="mx-auto max-w-6xl px-4 sm:px-6">
      <div className="grid gap-12 lg:grid-cols-[1fr_1.05fr] lg:items-center">
        <div>
          <p className="eyebrow">Safe on your computer</p>
          <h2 id="safety" className="mt-3 text-4xl sm:text-5xl">It asks before it does anything important.</h2>
          <ul className="mt-8 space-y-6">
            {POINTS.map((p) => (
              <li key={p.title} className="grid grid-cols-[1.25rem_1fr] gap-4">
                <span aria-hidden className="mt-2 h-2.5 w-2.5 rotate-45 border border-accent" />
                <div>
                  <h3 className="text-xl">{p.title}</h3>
                  <p className="mt-1 text-muted">{p.text}</p>
                </div>
              </li>
            ))}
          </ul>
          <Link href="/privacy" className="btn btn-ghost mt-9">
            Privacy and safety in detail <ArrowRight className="h-4 w-4" aria-hidden />
          </Link>
        </div>

        <figure className="screen hud-frame hud-lg overflow-hidden" style={{ boxShadow: "0 30px 80px rgba(0,0,0,.4), 0 0 0 1px rgba(116,225,255,.12)" }}>
          <div className="flex items-center justify-between border-b border-white/10 bg-black/25 px-4 py-2.5">
            <span className="font-display text-[0.78rem] font-semibold uppercase tracking-[0.2em] text-[#b3f0ff]">Activity</span>
            <span className="font-display text-[0.72rem] uppercase tracking-[0.18em] text-white/45">Policy engine</span>
          </div>
          <ul className="divide-y divide-white/[0.07] px-2 py-2 font-mono text-[0.82rem] sm:px-4">
            {LOG.map((row) => (
              <li key={row.call} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-2 py-3.5">
                <span className={`border px-2 py-0.5 font-display text-[0.72rem] font-semibold uppercase tracking-[0.16em] ${VERDICT[row.verdict]}`}>
                  {row.verdict}
                </span>
                <span className="min-w-0 flex-1 truncate text-white/90">{row.call}</span>
                <span className="w-full text-[0.76rem] text-white/45 sm:w-auto">{row.why}</span>
              </li>
            ))}
          </ul>
          <figcaption className="border-t border-white/10 px-6 py-3 text-xs text-white/45">
            An example of how the same rules treat different requests.
          </figcaption>
        </figure>
      </div>
    </section>
  );
}
