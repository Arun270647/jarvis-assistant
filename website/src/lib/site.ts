export const SITE_URL = "https://jarvis-desktop-assistant.vercel.app";
export const REPO_URL = "https://github.com/Arun270647/jarvis-assistant";
export const RELEASES_URL = `${REPO_URL}/releases`;
export const BLOB_URL = `${REPO_URL}/blob/main/`;
export const RAW_URL = "https://raw.githubusercontent.com/Arun270647/jarvis-assistant/main/";

export const NAV = [
  { href: "/download", label: "Download" },
  { href: "/guide", label: "Guide" },
  { href: "/privacy", label: "Privacy" },
  { href: "/changelog", label: "Changelog" },
] as const;

export const REQUIREMENTS = {
  windows: "Windows 10 or 11, 64-bit",
  macos: "Apple silicon (M1 or later), macOS 13 or later",
} as const;
