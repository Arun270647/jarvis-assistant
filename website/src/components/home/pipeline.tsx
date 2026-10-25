/* How a request flows, from README.md: click / hold Space → VAD → Whisper (local) → AI model (your choice) →
   tools (policy-checked) → voice (local); typed text joins before the AI model. */

const STAGES = [
  { title: "You ask", detail: "Click the orb, hold Space or press Ctrl+Shift+J (⌃⇧J on a Mac). Or just type.", tag: "Push to talk" },
  { title: "It hears", detail: "Silero VAD notices when you've finished; Whisper turns your words into text.", tag: "On your computer" },
  { title: "Your AI decides", detail: "Claude through your own account, or any model you connect, picks the tools.", tag: "Your choice" },
  { title: "Tools act", detail: "Every tool call passes the same checks: allow, ask you first, or deny.", tag: "Policy-checked" },
  { title: "It answers", detail: "The reply is spoken with a natural Kokoro voice or your system's own.", tag: "On your computer" },
];

export function Pipeline() {
  return (
    <section aria-labelledby="how" className="mx-auto max-w-6xl px-4 sm:px-6">
      <p className="eyebrow">How it works</p>
      <h2 id="how" className="mt-3 max-w-3xl text-4xl sm:text-5xl">Your voice in, a finished job out.</h2>
      <p className="mt-4 max-w-2xl text-lg text-muted">
        Speech is recognised and spoken on your computer. Only the text of your request goes to the AI you chose, and
        nothing runs without passing the safety checks.
      </p>

      <div className="relative mt-12 md:mt-20">
        {/* the signal rail above the stages: a light travels along it, left to right */}
        <span aria-hidden className="pipeline-line absolute -top-8 left-[10%] right-[10%] hidden h-px md:block" />
        <ol className="grid gap-4 md:grid-cols-5">
          {STAGES.map((s, i) => (
            <li key={s.title} className="hud-frame card relative p-5">
              <span aria-hidden className="absolute -top-8 left-1/2 hidden h-8 w-px bg-divider md:block" />
              <span aria-hidden className="absolute -top-[2.3rem] left-1/2 hidden h-2.5 w-2.5 -translate-x-1/2 rotate-45 border border-accent bg-bg md:block" />
              <div className="flex items-center justify-between">
                <span className="grid h-9 w-9 place-items-center border border-accent/50 bg-bg font-display text-base font-semibold text-accent">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <span className="font-display text-[0.7rem] font-semibold uppercase tracking-[0.18em] text-faint">{s.tag}</span>
              </div>
              <h3 className="mt-4 text-2xl">{s.title}</h3>
              <p className="mt-2 text-[0.95rem] leading-relaxed text-muted">{s.detail}</p>
            </li>
          ))}
        </ol>
      </div>

      <style>{`
        .pipeline-line {
          background: linear-gradient(90deg, transparent, var(--accent) 50%, transparent) 0 0 / 30% 100% no-repeat,
                      linear-gradient(90deg, var(--divider), var(--divider));
          animation: pipeline 5.5s linear infinite;
        }
        @keyframes pipeline { from { background-position: -40% 0, 0 0; } to { background-position: 140% 0, 0 0; } }
      `}</style>
    </section>
  );
}
