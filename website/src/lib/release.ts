/* The shape of public/latest.json, shared by the server pages and the client download components:
   {"version", "notes", "windows": {"url", "sha256", "size"}, "macos": {...}}. lib/latest.ts reads the file. */

export type Asset = { url: string; sha256: string; size: number; name: string };
export type Latest = { version: string; notes: string | null; windows: Asset | null; macos: Asset | null };
export type Platform = "windows" | "macos";

export const fileName = (url: string) => decodeURIComponent(url.split("?")[0].split("/").pop() ?? "");
export const megabytes = (bytes: number) => `${Math.round(bytes / 1e6)} MB`;
