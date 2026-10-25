import Link from "next/link";
import { REPO_URL, RELEASES_URL } from "@/lib/site";

const COLUMNS = [
  {
    title: "Get it",
    links: [
      { href: "/download", label: "Download" },
      { href: RELEASES_URL, label: "All releases" },
      { href: "/changelog", label: "Changelog" },
    ],
  },
  {
    title: "Use it",
    links: [
      { href: "/guide", label: "Getting started" },
      { href: "/troubleshooting", label: "Troubleshooting" },
      { href: "/privacy", label: "Privacy and safety" },
    ],
  },
  {
    title: "Project",
    links: [
      { href: REPO_URL, label: "Source code" },
      { href: `${REPO_URL}/blob/main/CONTRIBUTING.md`, label: "Contributing" },
      { href: `${REPO_URL}/blob/main/SECURITY.md`, label: "Report a security problem" },
    ],
  },
];

export function SiteFooter() {
  return (
    <footer className="mt-24 border-t border-divider">
      <div className="mx-auto grid max-w-6xl gap-10 px-4 py-14 sm:px-6 md:grid-cols-[1.4fr_repeat(3,1fr)]">
        <div>
          <div className="font-display text-2xl font-semibold uppercase tracking-[0.28em]">Jarvis</div>
          <p className="mt-3 max-w-xs text-sm text-muted">
            A free, open-source voice assistant for Windows and Mac that does things on your computer, safely.
          </p>
        </div>
        {COLUMNS.map((col) => (
          <div key={col.title}>
            <h2 className="eyebrow">{col.title}</h2>
            <ul className="mt-4 space-y-2.5 text-[0.95rem]">
              {col.links.map((l) => (
                <li key={l.href}>
                  {l.href.startsWith("/") ? (
                    <Link href={l.href} className="text-muted transition-colors hover:text-accent">{l.label}</Link>
                  ) : (
                    <a href={l.href} className="text-muted transition-colors hover:text-accent">{l.label}</a>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <div className="border-t border-divider">
        <div className="mx-auto flex max-w-6xl flex-col gap-2 px-4 py-6 text-sm text-faint sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <span>© The Jarvis authors · Apache License 2.0</span>
          <span>No ads, tracking or telemetry in the app. No analytics on this site.</span>
        </div>
      </div>
    </footer>
  );
}
