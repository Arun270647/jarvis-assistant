import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/site";

export default function sitemap(): MetadataRoute.Sitemap {
  return ["", "/download", "/guide", "/privacy", "/troubleshooting", "/changelog"].map((path) => ({
    url: `${SITE_URL}${path}`,
    changeFrequency: path === "/changelog" || path === "/download" ? "weekly" : "monthly",
    priority: path === "" ? 1 : path === "/download" ? 0.9 : 0.7,
  }));
}
