import type { Metadata } from "next";
import { DocPage } from "@/components/doc-page";
import { renderDoc } from "@/lib/docs";

export const ensureStatic = "navigation";

const SOURCE = "docs/TROUBLESHOOTING.md";
const doc = renderDoc(SOURCE);

export const metadata: Metadata = {
  title: "Troubleshooting",
  description: "What Jarvis's messages mean, fixes for common problems, and how to report one.",
  alternates: { canonical: "/troubleshooting" },
};

export default function TroubleshootingPage() {
  return <DocPage doc={doc} eyebrow="Help" source={SOURCE} />;
}
