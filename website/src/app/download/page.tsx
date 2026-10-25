import type { Metadata } from "next";
import { CopyButton, DownloadSteps } from "@/components/download-steps";
import { DownloadButtons, PLATFORM_NAME, PlatformIcon } from "@/components/download-buttons";
import { readLatest } from "@/lib/latest";
import { megabytes, type Platform } from "@/lib/release";
import { RELEASES_URL, REQUIREMENTS } from "@/lib/site";

export const ensureStatic = "navigation";

export const metadata: Metadata = {
  title: "Download",
  description: "Download Jarvis for Windows (an installer, no administrator rights needed) or for Macs with Apple silicon (a disk image), and check the file's SHA-256.",
  alternates: { canonical: "/download" },
};

const NEEDS = [
  ["A computer", `${REQUIREMENTS.windows}, or a Mac with ${REQUIREMENTS.macos}.`],
  ["A microphone", "Most laptops have one. Without one, you can type to Jarvis."],
  ["An internet connection", "For the setup's downloads and for your AI."],
  ["An AI to connect", "A Claude Pro or Max plan, an API key from another provider, or a model on your own PC."],
];

export default function DownloadPage() {
  const latest = readLatest();
  const platforms: Platform[] = ["windows", "macos"];

  return (
    <div className="mx-auto max-w-6xl px-4 pb-8 pt-14 sm:px-6 sm:pt-20">
      <header className="grid gap-10 lg:grid-cols-[1.3fr_1fr] lg:items-end">
        <div>
          <p className="eyebrow">Download</p>
          <h1 className="mt-3 text-5xl sm:text-6xl">Get Jarvis</h1>
          <p className="mt-5 max-w-xl text-lg text-muted">
            Free and open source. The download has its own copy of everything Jarvis needs; the setup then downloads
            the speech and voice models for your computer.
          </p>
          <DownloadButtons latest={latest} className="mt-8" />
        </div>
        <dl className="hud-frame card grid gap-4 p-6">
          {NEEDS.map(([k, v]) => (
            <div key={k}>
              <dt className="font-display text-sm font-semibold uppercase tracking-[0.16em] text-accent">{k}</dt>
              <dd className="mt-1 text-[0.97rem] text-muted">{v}</dd>
            </div>
          ))}
        </dl>
      </header>

      <div className="mt-24">
        <DownloadSteps latest={latest} />
      </div>

      <section aria-labelledby="all" className="mt-24">
        <h2 id="all" className="text-4xl">All downloads</h2>
        {latest ? (
          <>
            <div className="mt-6 overflow-x-auto border border-divider">
              <table className="w-full min-w-[40rem] border-collapse text-left text-[0.95rem]">
                <thead>
                  <tr className="bg-panel font-display text-[0.8rem] uppercase tracking-[0.14em] text-muted">
                    <th scope="col" className="px-4 py-3 font-semibold">File</th>
                    <th scope="col" className="px-4 py-3 font-semibold">Version</th>
                    <th scope="col" className="px-4 py-3 font-semibold">Size</th>
                    <th scope="col" className="px-4 py-3 font-semibold">SHA-256</th>
                  </tr>
                </thead>
                <tbody>
                  {platforms.map((p) => {
                    const a = latest[p];
                    if (!a) return null;
                    return (
                      <tr key={p} className="border-t border-divider align-top">
                        <td className="px-4 py-4">
                          <a href={a.url} className="flex items-start gap-2 text-accent underline decoration-accent/40 underline-offset-4 hover:decoration-accent">
                            <PlatformIcon platform={p} className="mt-1 h-4 w-4 shrink-0" />
                            <span className="break-all">{a.name}</span>
                          </a>
                          <span className="mt-1 block pl-6 text-sm text-faint">{PLATFORM_NAME[p]}</span>
                        </td>
                        <td className="px-4 py-4 whitespace-nowrap">{latest.version}</td>
                        <td className="px-4 py-4 whitespace-nowrap">{megabytes(a.size)}</td>
                        <td className="px-4 py-4">
                          <div className="flex items-start gap-1">
                            <code className="break-all font-mono text-[0.8rem] leading-relaxed text-muted">{a.sha256}</code>
                            <CopyButton text={a.sha256} label={`Copy the SHA-256 of ${a.name}`} />
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-[0.97rem]">
              {latest.notes ? <a className="text-accent underline-offset-4 hover:underline" href={latest.notes}>What&apos;s new in {latest.version}</a> : null}
              <a className="text-accent underline-offset-4 hover:underline" href={RELEASES_URL}>Every release on GitHub</a>
            </p>
          </>
        ) : (
          <p className="mt-6 text-lg text-muted">
            The first release is coming soon. Follow it on{" "}
            <a className="text-accent underline underline-offset-4" href={RELEASES_URL}>GitHub</a>.
          </p>
        )}
      </section>
    </div>
  );
}
