import { readFileSync } from "node:fs";
import { join, posix } from "node:path";
import type { Element, ElementContent, Root, Text } from "hast";
import rehypeSlug from "rehype-slug";
import rehypeStringify from "rehype-stringify";
import remarkGfm from "remark-gfm";
import remarkParse from "remark-parse";
import remarkRehype from "remark-rehype";
import { unified } from "unified";
import { visit } from "unist-util-visit";
import { BLOB_URL, RAW_URL } from "@/lib/site";

/* The guide, the safety page, troubleshooting and the changelog are the repository's own Markdown files, rendered
   at build time so the site never drifts from them. Headings get GitHub's ids (rehype-slug uses github-slugger),
   because the app's ? buttons open help.url + "#<GitHub anchor>". Links to repository files go to GitHub, or to
   this site when the file has a page here; images from docs/images are copied to /docs-images by
   scripts/sync-docs.mjs before each build. */

const REPO_ROOT = join(process.cwd(), "..");

export const DOC_PAGES: Record<string, string> = {
  "docs/GETTING_STARTED.md": "/guide",
  "docs/SECURITY_MODEL.md": "/privacy",
  "docs/TROUBLESHOOTING.md": "/troubleshooting",
  "CHANGELOG.md": "/changelog",
};

export type TocEntry = { id: string; text: string; depth: number };
export type Doc = { title: string; html: string; toc: TocEntry[] };

function textOf(node: ElementContent | Element): string {
  if (node.type === "text") return (node as Text).value;
  if ("children" in node) return node.children.map((c) => textOf(c as ElementContent)).join("");
  return "";
}

function rewrite(href: string, docPath: string, kind: "link" | "image"): string {
  if (/^([a-z]+:|#|\/\/)/i.test(href)) return href;
  const [path, hash] = href.split("#");
  const target = posix.normalize(posix.join(posix.dirname(docPath), path));
  const suffix = hash ? `#${hash}` : "";
  if (kind === "image") {
    return target.startsWith("docs/images/") ? `/docs-images/${target.slice("docs/images/".length)}` : RAW_URL + target;
  }
  if (DOC_PAGES[target]) return DOC_PAGES[target] + suffix;
  return BLOB_URL + target + suffix;
}

function site(docPath: string, toc: TocEntry[], meta: { title: string }) {
  return (tree: Root) => {
    visit(tree, "element", (node, index, parent) => {
      if (node.tagName === "h1" && parent && typeof index === "number" && !meta.title) {
        meta.title = textOf(node).trim();
        parent.children.splice(index, 1);
        return index;
      }
      if ((node.tagName === "h2" || node.tagName === "h3") && typeof node.properties.id === "string") {
        const id = node.properties.id;
        toc.push({ id, text: textOf(node).trim(), depth: node.tagName === "h2" ? 2 : 3 });
        node.children.push({
          type: "element", tagName: "a",
          properties: { className: ["anchor"], href: `#${id}`, ariaLabel: `Link to ${textOf(node).trim()}` },
          children: [{ type: "text", value: "#" }],
        });
      }
      if (node.tagName === "a" && typeof node.properties.href === "string" && node.properties.className === undefined) {
        const href = rewrite(node.properties.href, docPath, "link");
        node.properties.href = href;
        if (/^https?:/.test(href)) node.properties.rel = ["noopener", "noreferrer"];
      }
      if (node.tagName === "img" && typeof node.properties.src === "string") {
        node.properties.src = rewrite(node.properties.src, docPath, "image");
        node.properties.loading = "lazy";
        node.properties.decoding = "async";
      }
      if (node.tagName === "table" && parent && typeof index === "number") {
        parent.children[index] = { type: "element", tagName: "div", properties: { className: ["table-wrap"] }, children: [node] };
        return index + 1;
      }
    });
  };
}

export function renderDoc(docPath: string): Doc {
  const markdown = readFileSync(join(REPO_ROOT, docPath), "utf8");
  const toc: TocEntry[] = [];
  const meta = { title: "" };
  const html = String(
    unified()
      .use(remarkParse)
      .use(remarkGfm)
      .use(remarkRehype)
      .use(rehypeSlug)
      .use(() => site(docPath, toc, meta))
      .use(rehypeStringify)
      .processSync(markdown),
  );
  return { title: meta.title, html, toc };
}
