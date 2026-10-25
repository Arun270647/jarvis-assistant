"use client";

import { useSyncExternalStore } from "react";

export type DetectedOs = "windows" | "macos" | "other" | "unknown";

/* Which download to put first. Safari on Apple silicon still says "Intel Mac OS X", and a browser can't tell
   Apple silicon from Intel, so every Mac gets the Apple-silicon caveat. iPhones and iPads aren't Macs. The server
   render doesn't know ("unknown") and shows both downloads side by side. */
function detect(): DetectedOs {
  const ua = navigator.userAgent || "";
  if (/iPhone|iPad|Android/i.test(ua)) return "other";
  if (/Macintosh|Mac OS X/i.test(ua)) return "macos";
  if (/Windows/i.test(ua)) return "windows";
  return "other";
}

const noop = () => () => {};

export function useOs(): DetectedOs {
  return useSyncExternalStore(noop, detect, () => "unknown");
}
