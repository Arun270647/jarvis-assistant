import { Cloud, HardDrive, KeyRound, Sparkles } from "lucide-react";

/* The providers in docs/PROVIDERS.md and Settings › AI. */

const GROUPS = [
  {
    icon: <Sparkles className="h-5 w-5" aria-hidden />,
    title: "Sign in with Claude",
    badge: "Default",
    text: "Use the Claude Pro or Max plan you already have. Jarvis uses Claude Code's sign-in and never sees or stores your password.",
    names: ["Claude"],
  },
  {
    icon: <Cloud className="h-5 w-5" aria-hidden />,
    title: "Bring an API key",
    text: "Pay per use with a key from a provider. Free tiers from Groq, OpenRouter and Gemini work too.",
    names: ["Anthropic API", "OpenAI", "OpenRouter", "Groq", "Google Gemini"],
  },
  {
    icon: <HardDrive className="h-5 w-5" aria-hidden />,
    title: "Run a model on your PC",
    text: "Keep even the requests at home with a local model. No key needed.",
    names: ["Ollama", "LM Studio", "Any OpenAI-compatible server"],
  },
];

export function Providers() {
  return (
    <section aria-labelledby="providers" className="mx-auto max-w-6xl px-4 sm:px-6">
      <div className="grid gap-10 lg:grid-cols-[1fr_1.6fr] lg:items-end">
        <div>
          <p className="eyebrow">Connect your AI</p>
          <h2 id="providers" className="mt-3 text-4xl sm:text-5xl">Use the AI you already have.</h2>
        </div>
        <p className="flex items-start gap-3 text-lg text-muted">
          <KeyRound className="mt-1.5 h-5 w-5 shrink-0 text-accent" aria-hidden />
          Switch any time in Settings › AI. Keys are kept in Windows Credential Manager or the macOS Keychain, and
          masked in logs and history.
        </p>
      </div>
      <div className="mt-10 grid gap-4 md:grid-cols-3">
        {GROUPS.map((g) => (
          <div key={g.title} className="hud-frame card flex flex-col p-6">
            <div className="flex items-center justify-between">
              <span className="grid h-10 w-10 place-items-center bg-accent/12 text-accent">{g.icon}</span>
              {g.badge ? <span className="tag">{g.badge}</span> : null}
            </div>
            <h3 className="mt-5 text-[1.6rem]">{g.title}</h3>
            <p className="mt-2 text-[0.97rem] leading-relaxed text-muted">{g.text}</p>
            <ul className="mt-5 flex flex-wrap gap-2 pt-1">
              {g.names.map((n) => (
                <li key={n} className="border border-divider px-2.5 py-1 text-sm text-text/85">{n}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
