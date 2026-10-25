import type { Metadata } from "next";
import { DocPage } from "@/components/doc-page";
import { renderDoc } from "@/lib/docs";

// The app's ? buttons open this page (help.url) with GitHub's anchors, e.g. #2-set-up-the-setup-wizard.
export const ensureStatic = "navigation";

const SOURCE = "docs/GETTING_STARTED.md";
const doc = renderDoc(SOURCE);

export const metadata: Metadata = {
  title: "Getting started",
  description: "Install Jarvis, go through the setup wizard and learn the everyday basics in about five minutes.",
  alternates: { canonical: "/guide" },
};

export default function GuidePage() {
  return <DocPage doc={doc} eyebrow="Guide" source={SOURCE} />;
}
