"use client";

import { useEffect, useRef, useState } from "react";

/* The letter-scramble from 21st.dev's "Animated Download" (isaiahbjork): when the text changes, letters cycle
   through the alphabet and settle left to right. Server render and reduced motion show the plain text. */
const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

export function HyperText({ text, className, ms = 520 }: { text: string; className?: string; ms?: number }) {
  const [frame, setFrame] = useState<string | null>(null);
  const previous = useRef(text);

  useEffect(() => {
    if (previous.current === text) return;
    previous.current = text;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let step = 0;
    const steps = Math.max(8, text.length * 3);
    const timer = window.setInterval(() => {
      step += 1;
      const settled = Math.floor((step / steps) * text.length);
      if (step >= steps) {
        window.clearInterval(timer);
        setFrame(null);
        return;
      }
      setFrame(text.split("").map((ch, i) => (ch === " " || i < settled ? ch : LETTERS[(i * 7 + step * 13) % 26])).join(""));
    }, ms / steps);
    return () => { window.clearInterval(timer); setFrame(null); };
  }, [text, ms]);

  return (
    <span className={className}>
      <span className="sr-only">{text}</span>
      <span aria-hidden>{frame ?? text}</span>
    </span>
  );
}
