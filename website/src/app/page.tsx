import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { DownloadButtons } from "@/components/download-buttons";
import { HeroDemo } from "@/components/hero-demo";
import { Features } from "@/components/home/features";
import { Pipeline } from "@/components/home/pipeline";
import { Providers } from "@/components/home/providers";
import { Safety } from "@/components/home/safety";
import { BackgroundBeams } from "@/components/ui/background-beams";
import { readLatest } from "@/lib/latest";

export const ensureStatic = "navigation";

export default function Home() {
  const latest = readLatest();
  return (
    <>
      <section className="relative overflow-hidden border-b border-divider">
        <div aria-hidden className="dot-grid absolute inset-0 [mask-image:radial-gradient(ellipse_70%_60%_at_60%_40%,black,transparent)]" />
        <BackgroundBeams className="opacity-70 [mask-image:linear-gradient(to_right,transparent_20%,black_70%)] dark:opacity-100" />
        <div className="relative mx-auto grid max-w-6xl items-center gap-12 px-4 pb-20 pt-14 sm:px-6 md:pt-20 lg:grid-cols-[1.05fr_1fr] lg:gap-10 lg:pb-28">
          <div>
            <p className="rise tag">Free and open source · Windows and Mac</p>
            <h1 className="rise rise-1 mt-6 text-[2.9rem] leading-[1.02] sm:text-6xl lg:text-[4.4rem]">
              The voice assistant that actually <span className="text-accent">does things</span> on your computer. Safely.
            </h1>
            <p className="rise rise-2 mt-6 max-w-xl text-lg leading-relaxed text-muted sm:text-xl">
              Click the orb or press Ctrl+Shift+J and say what you want. Jarvis opens apps, finds files, plays music,
              remembers things and works in your projects, and asks you first before anything important.
            </p>
            <DownloadButtons latest={latest} className="rise rise-3 mt-9" />
            <ul className="rise rise-4 mt-6 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-faint">
              {["No account with us", "No telemetry"].map((t) => (
                <li key={t} className="flex items-center gap-2">
                  <span aria-hidden className="h-1.5 w-1.5 rotate-45 border border-accent" />
                  {t}
                </li>
              ))}
              <li>
                <Link href="/guide" className="inline-flex items-center gap-1.5 text-muted underline-offset-4 hover:text-accent hover:underline">
                  Read the 5-minute guide <ArrowRight className="h-3.5 w-3.5" aria-hidden />
                </Link>
              </li>
            </ul>
          </div>
          <div className="rise rise-2 mx-auto w-full max-w-[34rem]">
            <HeroDemo />
          </div>
        </div>
      </section>

      <div className="space-y-28 pt-24 sm:space-y-36 sm:pt-28">
        <Pipeline />
        <Features />
        <Providers />
        <Safety />

        <section aria-labelledby="get-it" className="mx-auto max-w-6xl px-4 sm:px-6">
          <div className="screen hud-frame hud-lg relative overflow-hidden px-6 py-14 text-center sm:px-12 sm:py-20" style={{ boxShadow: "0 0 0 1px rgba(116,225,255,.12)" }}>
            <BackgroundBeams className="opacity-80" />
            <div className="relative">
              <p className="eyebrow text-[#74e1ff]">Get Jarvis</p>
              <h2 id="get-it" className="mx-auto mt-3 max-w-2xl text-4xl text-white sm:text-6xl">Ready when you are.</h2>
              <p className="mx-auto mt-4 max-w-xl text-lg text-white/70">
                Download it, answer a few questions, and talk to it in about five minutes. Free, and yours to read,
                change and share under the Apache License 2.0.
              </p>
              <div className="mt-9 flex justify-center [&_p]:text-center">
                <DownloadButtons latest={latest} className="flex flex-col items-center" />
              </div>
            </div>
          </div>
        </section>
      </div>
    </>
  );
}
