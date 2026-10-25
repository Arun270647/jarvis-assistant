"use client";

import { Moon, Sun } from "lucide-react";
import { useSyncExternalStore } from "react";

/* Dark by default. The choice is kept in localStorage ("jarvis-theme") and applied before the first paint by the
   inline script in the root layout, so the page never flashes the other theme. ?theme=light in the address shows
   the light theme without changing the saved choice. */
export const THEME_SCRIPT = `try{var q=new URLSearchParams(location.search).get("theme"),t=q||localStorage.getItem("jarvis-theme");document.documentElement.dataset.theme=t==="light"?"light":"dark"}catch(e){document.documentElement.dataset.theme="dark"}`;

function subscribe(onChange: () => void) {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => observer.disconnect();
}
const current = () => (document.documentElement.dataset.theme === "light" ? "light" : "dark");

export function ThemeToggle({ className }: { className?: string }) {
  const theme = useSyncExternalStore(subscribe, current, () => "dark");
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      className={className}
      aria-label={`Switch to the ${next} theme`}
      title={`Switch to the ${next} theme`}
      onClick={() => {
        document.documentElement.dataset.theme = next;
        try { localStorage.setItem("jarvis-theme", next); } catch { /* private window: the choice lasts this visit */ }
      }}
    >
      {theme === "dark" ? <Sun className="h-[18px] w-[18px]" aria-hidden /> : <Moon className="h-[18px] w-[18px]" aria-hidden />}
    </button>
  );
}
