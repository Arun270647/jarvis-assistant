"use client";

import { ArrowRight } from "lucide-react";
import { AppleIcon, GitHubIcon, WindowsIcon } from "@/components/brand-icons";
import { useOs } from "@/components/use-os";
import { megabytes, type Latest, type Platform } from "@/lib/release";
import { BLOB_URL, RELEASES_URL, REQUIREMENTS } from "@/lib/site";
import { cn } from "@/lib/utils";

export const PLATFORM_NAME: Record<Platform, string> = { windows: "Windows", macos: "Mac" };

export function PlatformIcon({ platform, className }: { platform: Platform; className?: string }) {
  return platform === "windows" ? <WindowsIcon className={className} /> : <AppleIcon className={className} />;
}

/* The download buttons: the visitor's system first (detected in the browser), the other one beside it. Before the
   first release, a "coming soon" note with the way to run it from source. */
export function DownloadButtons({ latest, compact = false, className }: { latest: Latest | null; compact?: boolean; className?: string }) {
  const os = useOs();

  if (!latest) {
    return (
      <div className={className}>
        <div className="flex flex-wrap items-center gap-3">
          <a className={cn("btn btn-primary", !compact && "btn-lg")} href={RELEASES_URL}>
            <GitHubIcon className="h-5 w-5" /> Follow the releases
          </a>
          <a className={cn("btn btn-ghost", !compact && "btn-lg")} href={`${BLOB_URL}README.md#running-from-source`}>
            Run it from source <ArrowRight className="h-4 w-4" aria-hidden />
          </a>
        </div>
        <p className="mt-3 text-sm text-muted">
          <span className="tag mr-2">First release coming soon</span>
          The Windows installer and the Mac disk image will appear here.
        </p>
      </div>
    );
  }

  const order: Platform[] = os === "macos" ? ["macos", "windows"] : ["windows", "macos"];
  const equal = os === "unknown" || os === "other";
  const first = order.find((p) => latest[p]) ?? "windows";

  return (
    <div className={className}>
      <div className="flex flex-wrap gap-3">
        {order.map((p) => {
          const asset = latest[p];
          if (!asset) return null;
          const primary = equal || p === first;
          return (
            <a
              key={p}
              href={asset.url}
              className={cn("btn", primary ? "btn-primary" : "btn-ghost", !compact && "btn-lg")}
              aria-label={`Download Jarvis ${latest.version} for ${PLATFORM_NAME[p]} (${asset.name}, ${megabytes(asset.size)})`}
            >
              <PlatformIcon platform={p} className="h-5 w-5" />
              {primary ? `Download for ${PLATFORM_NAME[p]}` : PLATFORM_NAME[p]}
            </a>
          );
        })}
      </div>
      <p className="mt-3 text-sm text-muted">
        {equal ? (
          <>Version {latest.version} · {REQUIREMENTS.windows} · {REQUIREMENTS.macos}</>
        ) : (
          <>
            Version {latest.version} · {REQUIREMENTS[first]}
            {latest[first] ? ` · ${megabytes(latest[first]!.size)}` : ""}
          </>
        )}
        {os === "other" ? <span className="block">Jarvis runs on Windows PCs and on Macs with Apple silicon.</span> : null}
      </p>
    </div>
  );
}
