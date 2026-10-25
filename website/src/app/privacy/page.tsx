import { EyeOff, HardDrive, KeyRound, Mic } from "lucide-react";
import type { Metadata } from "next";
import { DocPage } from "@/components/doc-page";
import { renderDoc } from "@/lib/docs";

export const ensureStatic = "navigation";

const SOURCE = "docs/SECURITY_MODEL.md";
const doc = renderDoc(SOURCE);

export const metadata: Metadata = {
  title: "Privacy and safety",
  description: "What Jarvis may do on your computer, how every action is checked, what leaves your computer, and how to limit it.",
  alternates: { canonical: "/privacy" },
};

// From docs/GETTING_STARTED.md ("Privacy in one paragraph") and docs/SECURITY_MODEL.md ("What leaves your PC").
const PRIVACY = [
  { icon: Mic, title: "Listens only when you ask", text: "Listening starts when you click the orb or press the shortcut, never in the background." },
  { icon: HardDrive, title: "Speech stays on your computer", text: "Speech recognition and the voices run on your computer. Your history and memories stay there too." },
  { icon: KeyRound, title: "Only your request goes out", text: "The text of your request, what the tools read for it and any screenshots you allow go to the AI you connected. Nothing else." },
  { icon: EyeOff, title: "No ads, tracking or telemetry", text: "Jarvis doesn't report anything about you or how you use it. Keys live in Credential Manager or the Keychain." },
];

export default function PrivacyPage() {
  return (
    <DocPage doc={{ ...doc, title: "Privacy and safety" }} eyebrow="Privacy" source={SOURCE}>
      <ul className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {PRIVACY.map(({ icon: Icon, title, text }) => (
          <li key={title} className="hud-frame card p-5">
            <Icon className="h-6 w-6 text-accent" aria-hidden />
            <h2 className="mt-4 text-xl">{title}</h2>
            <p className="mt-1.5 text-[0.95rem] leading-relaxed text-muted">{text}</p>
          </li>
        ))}
      </ul>
      <p className="mt-14 font-display text-2xl font-semibold">{doc.title}</p>
    </DocPage>
  );
}
