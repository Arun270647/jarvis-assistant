import type { Metadata } from "next";
import { DocPage } from "@/components/doc-page";
import { renderDoc } from "@/lib/docs";
import { RELEASES_URL } from "@/lib/site";

export const ensureStatic = "navigation";

const SOURCE = "CHANGELOG.md";
const doc = renderDoc(SOURCE);

export const metadata: Metadata = {
  title: "Changelog",
  description: "What changed in each version of Jarvis.",
  alternates: { canonical: "/changelog" },
};

export default function ChangelogPage() {
  return (
    <DocPage doc={doc} eyebrow="Release notes" source={SOURCE}>
      <p className="mt-5 max-w-2xl text-lg text-muted">
        Downloads for every version, with their SHA-256 sums, are on{" "}
        <a className="text-accent underline underline-offset-4" href={RELEASES_URL}>GitHub Releases</a>.
      </p>
    </DocPage>
  );
}
