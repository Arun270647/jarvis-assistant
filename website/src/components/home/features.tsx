import { AppWindow, AudioWaveform, Brain, CircleDollarSign, EyeOff, ListChecks, ShieldCheck, Wrench } from "lucide-react";
import { BentoGrid, BentoGridItem } from "@/components/ui/bento-grid";

/* What works today, from README.md ("What works today"). */

function Waveform() {
  return (
    <div aria-hidden className="flex h-24 items-center gap-[3px] overflow-hidden border-b border-divider pb-4">
      {Array.from({ length: 64 }, (_, i) => {
        const h = 18 + Math.round(70 * Math.abs(Math.sin(i * 0.45) * Math.cos(i * 0.13)));
        return (
          <span
            key={i}
            className="w-[3px] shrink-0 bg-accent/70"
            style={{ height: `${h}%`, animation: `wave 1.6s ${(i % 16) * 0.06}s ease-in-out infinite alternate` }}
          />
        );
      })}
      <style>{`@keyframes wave { from { transform: scaleY(.45); opacity: .55; } to { transform: scaleY(1); opacity: 1; } }`}</style>
    </div>
  );
}

function PolicyChips() {
  const rows: [string, string][] = [["allow", "Runs"], ["ask", "Asks you first"], ["deny", "Blocked"]];
  return (
    <div aria-hidden className="grid grid-cols-3 gap-2 border-b border-divider pb-4">
      {rows.map(([k, v]) => (
        <div key={k} className="border border-divider bg-surface/60 px-3 py-2.5">
          <div className="font-mono text-sm text-accent">{k}</div>
          <div className="mt-0.5 text-xs text-faint">{v}</div>
        </div>
      ))}
    </div>
  );
}

const ITEMS = [
  {
    title: "Ears and voice on your computer",
    icon: <AudioWaveform className="h-6 w-6" aria-hidden />,
    header: <Waveform />,
    className: "lg:col-span-2",
    description: (
      <>
        Silero VAD and faster-whisper recognise speech on your graphics card or processor. Replies use natural Kokoro
        voices by default, or your system&apos;s voice, Piper or Chatterbox. No microphone? Type instead.
      </>
    ),
  },
  {
    title: "Your choice of AI",
    icon: <Brain className="h-6 w-6" aria-hidden />,
    description: (
      <>Claude through your own Claude account by default, or an API key for Anthropic, OpenAI, OpenRouter, Groq or Gemini, or a model running on your PC.</>
    ),
  },
  {
    title: "Real tools",
    icon: <Wrench className="h-6 w-6" aria-hidden />,
    description: (
      <>Open apps, media keys, web search and reading, memory, project folders, terminals, desktop control, Chrome profiles, email and coding agents.</>
    ),
  },
  {
    title: "Tasks with several steps",
    icon: <ListChecks className="h-6 w-6" aria-hidden />,
    description: <>Bigger goals become tracked tasks with a plan and checks Jarvis verifies itself, with pause, resume and retry.</>,
  },
  {
    title: "One safety layer for every model",
    icon: <ShieldCheck className="h-6 w-6" aria-hidden />,
    header: <PolicyChips />,
    className: "lg:col-span-2",
    description: (
      <>
        Each tool call is validated, then allowed, asked about or denied, with spoken and on-screen approvals,
        protected folders, timeouts and an activity log. Risky groups stay off until you turn them on.
      </>
    ),
  },
  {
    title: "Costs you can see",
    icon: <CircleDollarSign className="h-6 w-6" aria-hidden />,
    description: <>Tokens and an estimated cost for each day in Activity, and an optional daily spending limit.</>,
  },
  {
    title: "A proper desktop app",
    icon: <AppWindow className="h-6 w-6" aria-hidden />,
    className: "lg:col-span-2",
    description: <>A floating orb, a tray or menu bar icon, the Ctrl+Shift+J shortcut (⌃⇧J on a Mac), and no console windows. A setup wizard walks you through everything.</>,
  },
  {
    title: "Private by design",
    icon: <EyeOff className="h-6 w-6" aria-hidden />,
    className: "lg:col-span-2",
    description: <>No ads, no tracking, no telemetry. Your history and memories stay on your computer; only the requests you make go to the AI you chose.</>,
  },
];

export function Features() {
  return (
    <section aria-labelledby="features" className="mx-auto max-w-6xl px-4 sm:px-6">
      <p className="eyebrow">What it does</p>
      <h2 id="features" className="mt-3 max-w-3xl text-4xl sm:text-5xl">An assistant that can actually help.</h2>
      <BentoGrid className="mt-10">
        {ITEMS.map((item) => (
          <BentoGridItem key={item.title} {...item} />
        ))}
      </BentoGrid>
    </section>
  );
}
