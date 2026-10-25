"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { HudOrb, type OrbPhase, type OrbState } from "@/components/hud-orb";
import { HyperText } from "@/components/ui/hyper-text";
import { TextGenerateEffect } from "@/components/ui/text-generate-effect";
import { cn } from "@/lib/utils";

/* A looping demo of the app's main screen: the orb goes through the real phases while three requests play out,
   each through a real tool and the same checks the app uses. Restarting the PC is one of the actions that always
   needs a click or a key, so that request shows the approval and is declined. With reduced motion the first
   exchange is shown complete and nothing moves. */

type Scenario = {
  said: string;
  tool: string;
  arg: string;
  reply: string;
  approval?: string;
};

const SCENARIOS: Scenario[] = [
  { said: "Open Notepad", tool: "open_app", arg: "notepad", reply: "Notepad is open." },
  {
    said: "Remember that my car is parked on level 3", tool: "remember", arg: "car parked on level 3",
    reply: "Got it. I'll remember your car is on level 3.",
  },
  {
    said: "Restart the computer", tool: "system_command", arg: "restart", approval: "restart the PC",
    reply: "Okay, I won't restart it.",
  },
];

const STEPS: { phase: OrbPhase; ms: number }[] = [
  { phase: "idle", ms: 1500 },
  { phase: "listening", ms: 2300 },
  { phase: "transcribing", ms: 800 },
  { phase: "thinking", ms: 1300 },
  { phase: "executing", ms: 1100 },
  { phase: "speaking", ms: 2900 },
];
const APPROVAL_MS = 3400;

const LABEL: Record<OrbPhase, string> = {
  idle: "Ready", listening: "Listening", transcribing: "Transcribing", thinking: "Thinking",
  executing: "Running a tool", approval: "Waiting for you", speaking: "Speaking",
};

type View = { scenario: number; phase: OrbPhase; showSaid: boolean; tool: "none" | "running" | "done" | "denied"; showReply: boolean };
const STILL: View = { scenario: 0, phase: "idle", showSaid: true, tool: "done", showReply: true };

export function HeroDemo() {
  const [view, setView] = useState<View>({ scenario: 0, phase: "idle", showSaid: false, tool: "none", showReply: false });
  const timing = useRef({ phase: "idle" as OrbPhase, start: 0, ms: 1 });
  const [still, setStill] = useState(false);

  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
    let timer = 0;
    let cancelled = false;

    const run = (scenario: number, step: number) => {
      if (cancelled) return;
      const s = SCENARIOS[scenario];
      const base = STEPS[step];
      const phase: OrbPhase = s.approval && base.phase === "executing" ? "approval" : base.phase;
      const ms = phase === "approval" ? APPROVAL_MS : base.ms;
      timing.current = { phase, start: performance.now(), ms };
      setView({
        scenario,
        phase,
        showSaid: step >= 3,
        tool: step < 4 ? "none" : step === 4 ? "running" : s.approval ? "denied" : "done",
        showReply: step >= 5,
      });
      timer = window.setTimeout(() => {
        if (step + 1 < STEPS.length) run(scenario, step + 1);
        else run((scenario + 1) % SCENARIOS.length, 0);
      }, ms);
    };

    if (reduce.matches) {
      timer = window.setTimeout(() => setStill(true), 0);
    } else {
      run(0, 0);
    }
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, []);

  const getState = useCallback((): OrbState => {
    const { phase, start, ms } = timing.current;
    const now = performance.now();
    const t = now / 1000;
    const level = phase === "listening" ? .35 + .55 * Math.abs(Math.sin(t * 3.1) * Math.sin(t * 1.7 + 1)) : 0;
    const approvalFrac = phase === "approval" ? Math.max(0, 1 - (now - start) / ms) : 1;
    return { phase, level, approvalFrac };
  }, []);

  const v = still ? STILL : view;
  const s = SCENARIOS[v.scenario];
  const hot = v.phase !== "idle";

  return (
    <div className="screen hud-frame hud-lg relative overflow-hidden" style={{ boxShadow: "0 30px 80px rgba(0,0,0,.45), 0 0 0 1px rgba(116,225,255,.12)" }}>
      {/* title bar, as in the app */}
      <div className="flex items-center justify-between border-b border-white/10 bg-black/25 px-4 py-2.5 text-[0.78rem]">
        <div className="flex items-center gap-2.5">
          <span className={cn("h-2 w-2 rounded-full transition-colors duration-300", hot ? "bg-[#74e1ff] shadow-[0_0_10px_#35cdfa]" : "bg-[#14acdf]/70")} />
          <span className="font-display font-semibold uppercase tracking-[0.2em] text-[#b3f0ff]">
            <HyperText text={LABEL[v.phase]} />
          </span>
        </div>
        <span className="font-display uppercase tracking-[0.18em] text-white/45">Ctrl+Shift+J</span>
      </div>

      <div className="grid gap-0 p-4 sm:p-6">
        <div className="relative mx-auto aspect-square w-full max-w-[22rem]">
          <HudOrb getState={getState} className="h-full w-full" label="The Jarvis orb, the app's main control: it glows and moves differently while it listens, thinks, runs a tool and speaks." />
        </div>

        {/* transcript */}
        <div className="relative mt-2 min-h-[10.5rem] space-y-2.5 text-[0.95rem] leading-snug" aria-live="off">
          <p
            aria-hidden
            className={cn(
              "absolute inset-x-0 top-0 text-center font-display text-sm uppercase tracking-[0.2em] text-white/40 transition-opacity duration-300",
              v.phase === "idle" && !v.showSaid ? "opacity-100" : "opacity-0",
            )}
          >
            Click the orb and say what you want
          </p>
          <Line who="You" visible={v.showSaid || v.phase === "listening"}>
            {v.showSaid ? <span className="text-white">“{s.said}”</span> : <ListeningBars />}
          </Line>
          <Line who="Tool" visible={v.tool !== "none"}>
            <span className="font-mono text-[0.85rem] text-[#b3f0ff]">
              {s.tool}(<span className="text-[#74e1ff]">&quot;{s.arg}&quot;</span>)
            </span>
            <ToolBadge state={v.tool} approval={!!s.approval} />
          </Line>
          <Line who="Jarvis" visible={v.showReply}>
            {v.showReply ? <TextGenerateEffect key={`${v.scenario}-${still}`} words={s.reply} className="text-white" /> : null}
          </Line>
        </div>
      </div>

      {/* approval card: actions that always ask need a click or Y, not a spoken yes */}
      <div
        className={cn(
          "pointer-events-none absolute inset-x-4 bottom-4 border border-[#74e1ff]/40 bg-[#03111e]/95 p-4 backdrop-blur transition-all duration-300 sm:inset-x-6 sm:bottom-6",
          v.phase === "approval" ? "translate-y-0 opacity-100" : "translate-y-4 opacity-0",
        )}
        aria-hidden={v.phase !== "approval"}
      >
        <div className="font-display text-xs font-semibold uppercase tracking-[0.2em] text-[#74e1ff]">Jarvis wants to</div>
        <div className="mt-1 font-display text-2xl font-semibold text-white">{s.approval ? s.approval[0].toUpperCase() + s.approval.slice(1) : ""}</div>
        <div className="mt-1 text-sm text-white/60">This always needs your click or key.</div>
        <div className="mt-3 flex gap-2">
          <span className="btn btn-primary min-h-9 px-3 text-sm" style={{ "--cut": "8px" } as React.CSSProperties}>Allow <kbd className="font-mono text-xs opacity-70">Y</kbd></span>
          <span className="btn btn-ghost min-h-9 px-3 text-sm text-white" style={{ "--cut": "8px" } as React.CSSProperties}>Deny <kbd className="font-mono text-xs opacity-70">N</kbd></span>
        </div>
        <ApprovalBar active={v.phase === "approval"} ms={APPROVAL_MS} />
      </div>
    </div>
  );
}

function Line({ who, visible, children }: { who: string; visible: boolean; children: React.ReactNode }) {
  return (
    <div className={cn("grid grid-cols-[4.2rem_1fr] items-baseline gap-3 transition-all duration-300", visible ? "opacity-100" : "translate-y-1 opacity-0")}>
      <span className="font-display text-[0.72rem] font-semibold uppercase tracking-[0.2em] text-white/45">{who}</span>
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">{children}</div>
    </div>
  );
}

function ToolBadge({ state, approval }: { state: View["tool"]; approval: boolean }) {
  if (state === "running") {
    return <span className="tag border-[#74e1ff]/30 bg-[#35cdfa]/10 text-[#74e1ff]">{approval ? "Asks you" : "Checking"}</span>;
  }
  if (state === "denied") return <span className="tag border-[#ffcf7a]/40 bg-[#ffcf7a]/10 text-[#ffcf7a]">Denied by you</span>;
  if (state === "done") return <span className="tag border-[#74e1ff]/30 bg-[#35cdfa]/10 text-[#74e1ff]">Allowed · done</span>;
  return null;
}

function ListeningBars() {
  return (
    <span className="flex h-5 items-end gap-[3px]"><span className="sr-only">Listening</span>
      {Array.from({ length: 14 }, (_, i) => (
        <span
          key={i}
          className="w-[3px] origin-bottom bg-[#74e1ff]"
          style={{ height: "100%", animation: `bars 0.9s ${(i % 7) * 0.08}s ease-in-out infinite alternate`, transform: "scaleY(0.25)" }}
        />
      ))}
      <style>{`@keyframes bars { from { transform: scaleY(.18); } to { transform: scaleY(1); } }`}</style>
    </span>
  );
}

function ApprovalBar({ active, ms }: { active: boolean; ms: number }) {
  return (
    <div className="mt-3 h-[2px] w-full bg-white/10">
      <div
        className="h-full bg-[#74e1ff]"
        style={{ width: active ? "0%" : "100%", transition: active ? `width ${ms}ms linear` : "none" }}
      />
    </div>
  );
}
