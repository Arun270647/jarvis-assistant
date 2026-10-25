"use client";

import { Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { GitHubIcon } from "@/components/brand-icons";
import { ThemeToggle } from "@/components/theme-toggle";
import { NAV, REPO_URL } from "@/lib/site";
import { cn } from "@/lib/utils";

const iconButton =
  "inline-grid h-10 w-10 place-items-center text-muted transition-colors hover:text-accent focus-visible:text-accent";

export function SiteHeader() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  return (
    <header className="sticky top-0 z-50 border-b border-divider bg-bg/80 backdrop-blur-md supports-[backdrop-filter]:bg-bg/65">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-3 focus:z-50 focus:bg-accent focus:px-3 focus:py-2 focus:text-accent-ink">
        Skip to content
      </a>
      <div className="mx-auto flex h-16 max-w-6xl items-center gap-4 px-4 sm:px-6">
        <Link href="/" className="group flex items-center gap-2.5" aria-label="Jarvis home" onClick={() => setOpen(false)}>
          {/* eslint-disable-next-line @next/next/no-img-element -- a 32 px mark; next/image adds nothing here */}
          <img src="/brand/jarvis-mark.png" alt="" width={32} height={32} className="h-8 w-8 transition-transform duration-500 group-hover:rotate-[24deg]" />
          <span className="font-display text-xl font-semibold uppercase tracking-[0.28em]">Jarvis</span>
        </Link>

        <nav aria-label="Main" className="ml-auto hidden items-center gap-1 md:flex">
          {NAV.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "relative px-3 py-2 font-display text-[0.95rem] font-semibold uppercase tracking-[0.14em] transition-colors",
                  active ? "text-accent" : "text-muted hover:text-text",
                )}
              >
                {item.label}
                <span className={cn("absolute inset-x-3 -bottom-[13px] h-px bg-accent transition-opacity", active ? "opacity-100" : "opacity-0")} />
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center md:ml-2">
          <a href={REPO_URL} className={iconButton} aria-label="Jarvis on GitHub" title="Jarvis on GitHub">
            <GitHubIcon className="h-[19px] w-[19px]" />
          </a>
          <ThemeToggle className={iconButton} />
          <button
            type="button"
            className={cn(iconButton, "md:hidden")}
            aria-expanded={open}
            aria-controls="mobile-nav"
            aria-label={open ? "Close the menu" : "Open the menu"}
            onClick={() => setOpen((o) => !o)}
          >
            {open ? <X className="h-5 w-5" aria-hidden /> : <Menu className="h-5 w-5" aria-hidden />}
          </button>
        </div>
      </div>

      <nav id="mobile-nav" aria-label="Main" hidden={!open} className="border-t border-divider bg-bg md:hidden">
        <ul className="mx-auto max-w-6xl px-4 py-2">
          {NAV.map((item) => (
            <li key={item.href}>
              <Link
                href={item.href}
                onClick={() => setOpen(false)}
                aria-current={pathname === item.href ? "page" : undefined}
                className={cn(
                  "block border-b border-divider py-3 font-display text-lg font-semibold uppercase tracking-[0.14em] last:border-0",
                  pathname === item.href ? "text-accent" : "text-text",
                )}
              >
                {item.label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
    </header>
  );
}
