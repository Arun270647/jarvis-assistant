import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileName, type Asset, type Latest } from "@/lib/release";

/* public/latest.json: what the release workflow publishes for each release, and what the app's updater reads.
   Until the first release the file is {"version": null}, and the pages say the first release is coming. The site
   is rebuilt whenever the file changes, so the pages are static. Server-only (it reads the file). */

function asset(value: unknown): Asset | null {
  if (!value || typeof value !== "object") return null;
  const a = value as Record<string, unknown>;
  if (typeof a.url !== "string" || !/^https:\/\//.test(a.url)) return null;
  return { url: a.url, sha256: String(a.sha256 ?? ""), size: Number(a.size) || 0, name: fileName(a.url) };
}

export function readLatest(): Latest | null {
  try {
    const raw = JSON.parse(readFileSync(join(process.cwd(), "public", "latest.json"), "utf8"));
    if (!raw || typeof raw.version !== "string" || !raw.version) return null;
    const latest: Latest = {
      version: raw.version,
      notes: typeof raw.notes === "string" && /^https:\/\//.test(raw.notes) ? raw.notes : null,
      windows: asset(raw.windows),
      macos: asset(raw.macos),
    };
    return latest.windows || latest.macos ? latest : null;
  } catch {
    return null;
  }
}
