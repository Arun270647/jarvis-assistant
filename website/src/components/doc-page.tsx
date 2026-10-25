import type { Doc } from "@/lib/docs";
import { BLOB_URL } from "@/lib/site";

/* A repository document as a page: its own title, an optional lead-in, the rendered Markdown and, on wide
   screens, an "On this page" list of its sections. */
export function DocPage({ doc, eyebrow, source, children }: { doc: Doc; eyebrow: string; source: string; children?: React.ReactNode }) {
  const sections = doc.toc.filter((t) => t.depth === 2);
  return (
    <div className="mx-auto max-w-6xl px-4 pb-8 pt-14 sm:px-6 sm:pt-20">
      <header className="max-w-3xl">
        <p className="eyebrow">{eyebrow}</p>
        <h1 className="mt-3 text-5xl leading-[1.04] sm:text-6xl">{doc.title}</h1>
      </header>
      {children}
      <div className="mt-12 grid gap-12 lg:grid-cols-[minmax(0,1fr)_15rem]">
        <article className="doc min-w-0 max-w-3xl" dangerouslySetInnerHTML={{ __html: doc.html }} />
        {sections.length > 2 ? (
          <nav aria-label="On this page" className="hidden lg:block">
            <div className="sticky top-24 border-l border-divider pl-5">
              <p className="eyebrow text-[0.72rem]">On this page</p>
              <ul className="mt-4 space-y-2.5 text-[0.92rem]">
                {sections.map((s) => (
                  <li key={s.id}>
                    <a href={`#${s.id}`} className="text-muted transition-colors hover:text-accent">{s.text}</a>
                  </li>
                ))}
              </ul>
              <a href={BLOB_URL + source} className="mt-8 block text-sm text-faint transition-colors hover:text-accent">
                This page on GitHub ↗
              </a>
            </div>
          </nav>
        ) : null}
      </div>
    </div>
  );
}
