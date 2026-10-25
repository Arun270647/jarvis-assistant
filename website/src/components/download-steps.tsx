"use client";

import { Check, Copy, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { PLATFORM_NAME, PlatformIcon } from "@/components/download-buttons";
import { useOs } from "@/components/use-os";
import type { Latest, Platform } from "@/lib/release";
import { cn } from "@/lib/utils";

/* Install steps and the first-launch warning for each system (the visitor's first; tabs switch), with the
   command that checks the download against its SHA-256. */

const FALLBACK_NAME: Record<Platform, string> = {
  windows: "Jarvis-Setup-<version>-windows-x64.exe",
  macos: "Jarvis-<version>-macos-arm64.dmg",
};

export function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="inline-grid h-8 w-8 shrink-0 place-items-center text-muted transition-colors hover:text-accent"
      aria-label={done ? "Copied" : label}
      title={done ? "Copied" : label}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          window.setTimeout(() => setDone(false), 1600);
        } catch {
          /* clipboard blocked: the text is selectable */
        }
      }}
    >
      {done ? <Check className="h-4 w-4" aria-hidden /> : <Copy className="h-4 w-4" aria-hidden />}
    </button>
  );
}

function Command({ command }: { command: string }) {
  return (
    <div className="mt-2 flex items-center gap-2 border border-divider bg-surface py-1 pl-3 pr-1 font-mono text-[0.85rem]">
      <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap py-1.5">{command}</code>
      <CopyButton text={command} label="Copy the command" />
    </div>
  );
}

function Step({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <li className="relative grid grid-cols-[2.5rem_1fr] gap-4">
      <span className="grid h-10 w-10 place-items-center border border-accent/40 font-display text-lg font-semibold text-accent">{n}</span>
      <div className="pt-1.5 text-[1.02rem] leading-relaxed">{children}</div>
    </li>
  );
}

export function DownloadSteps({ latest }: { latest: Latest | null }) {
  const os = useOs();
  const [choice, setChoice] = useState<Platform | null>(null);
  const shown: Platform = choice ?? (os === "macos" ? "macos" : "windows");
  const name = latest?.[shown]?.name ?? FALLBACK_NAME[shown];
  const sha = latest?.[shown]?.sha256;

  return (
    <section aria-labelledby="install-heading">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h2 id="install-heading" className="text-4xl sm:text-5xl">Install it in three steps</h2>
        <div role="group" aria-label="Show the steps for" className="flex border border-divider p-1">
          {(["windows", "macos"] as Platform[]).map((p) => (
            <button
              key={p}
              type="button"
              aria-pressed={shown === p}
              onClick={() => setChoice(p)}
              className={cn(
                "flex items-center gap-2 px-4 py-2 font-display text-sm font-semibold uppercase tracking-[0.14em] transition-colors",
                shown === p ? "bg-accent text-accent-ink" : "text-muted hover:text-text",
              )}
            >
              <PlatformIcon platform={p} className="h-4 w-4" /> {PLATFORM_NAME[p]}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-8 grid gap-8 lg:grid-cols-[1.25fr_1fr]">
        <ol className="space-y-6">
          {shown === "windows" ? (
            <>
              <Step n={1}>Open the file you downloaded, <strong className="break-all font-semibold">{name}</strong> (it&apos;s in your <strong>Downloads</strong> folder).</Step>
              <Step n={2}>If Windows says <strong>&ldquo;Windows protected your PC&rdquo;</strong>, click <strong>More info</strong>, then <strong>Run anyway</strong>. Windows asks only once.</Step>
              <Step n={3}>Jarvis installs for you only, with no administrator rights, adds itself to the <strong>Start Menu</strong> and <strong>Settings › Apps</strong>, and opens. The setup asks for a name, downloads the speech and voice models and connects your AI.</Step>
            </>
          ) : (
            <>
              <Step n={1}>Open the file you downloaded, <strong className="break-all font-semibold">{name}</strong>, and drag <strong>Jarvis</strong> onto the <strong>Applications</strong> folder in the window that opens.</Step>
              <Step n={2}>Open <strong>Jarvis</strong> from Applications. The first time, macOS says it can&apos;t check it: click <strong>Done</strong>, open <strong>System Settings › Privacy &amp; Security</strong>, scroll down and click <strong>Open Anyway</strong>, then <strong>Open</strong>.</Step>
              <Step n={3}>The setup asks for a name and for the microphone (click <strong>Allow</strong>), downloads the speech and voice models and connects your AI. Then eject the Jarvis disk image and delete the .dmg.</Step>
            </>
          )}
        </ol>

        <aside className="hud-frame card p-6">
          <div className="flex items-center gap-2 text-warn">
            <ShieldAlert className="h-5 w-5" aria-hidden />
            <h3 className="text-2xl text-text">
              {shown === "windows" ? "“Windows protected your PC”" : "“macOS can’t check Jarvis”"}
            </h3>
          </div>
          <p className="mt-3 text-[0.98rem] text-muted">
            {shown === "windows"
              ? "Jarvis is free and has no budget for a code-signing certificate, so Windows SmartScreen warns the first time you open it. Click More info, then Run anyway."
              : "Jarvis is free and isn't notarized by Apple (that needs a paid developer account), so macOS blocks it the first time. Open Anyway in Privacy & Security lets it start; macOS asks only once. Since macOS 15, right-clicking the app and choosing Open no longer skips this."}
          </p>
          <p className="mt-4 text-[0.98rem] text-muted">
            To make sure the file is the one published here, open {shown === "windows" ? "PowerShell" : "Terminal"} in your Downloads folder and run:
          </p>
          <Command command={shown === "windows" ? `Get-FileHash ${name}` : `shasum -a 256 ${name}`} />
          <p className="mt-4 text-[0.98rem] text-muted">The result must match this SHA-256:</p>
          {sha ? (
            <div className="mt-2 flex items-start gap-2 border border-divider bg-surface py-1 pl-3 pr-1">
              <code className="min-w-0 flex-1 break-all py-1.5 font-mono text-[0.8rem] leading-relaxed text-accent">{sha}</code>
              <CopyButton text={sha} label="Copy the SHA-256" />
            </div>
          ) : (
            <p className="mt-2 text-sm text-faint">It&apos;s listed with each release.</p>
          )}
        </aside>
      </div>
    </section>
  );
}
