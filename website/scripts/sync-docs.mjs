// Copies the guide's screenshots (docs/images in the repository) to public/docs-images before `next dev` and
// `next build`, so the rendered guide can show them. The copy is generated, not committed.
import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const site = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = join(site, "..", "docs", "images");
const target = join(site, "public", "docs-images");

rmSync(target, { recursive: true, force: true });
if (existsSync(source)) {
  mkdirSync(target, { recursive: true });
  cpSync(source, target, { recursive: true });
  console.log(`[sync-docs] copied ${source} -> ${target}`);
} else {
  console.warn(`[sync-docs] ${source} not found; the guide will have no screenshots`);
}
