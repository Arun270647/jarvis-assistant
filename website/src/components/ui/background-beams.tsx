"use client";

import { motion, useReducedMotion } from "motion/react";
import { memo } from "react";
import { cn } from "@/lib/utils";

/* Aceternity UI's Background Beams (ui.aceternity.com/components/background-beams), recoloured to the app's
   cyan, with fewer paths, seeded timings (the same on the server and in the browser) and no motion for people
   who ask for less. */
const PATHS = Array.from({ length: 26 }, (_, i) => {
  const x = (n: number) => n + i * 14, y = (n: number) => n - i * 16;
  return `M${x(-380)} ${y(-189)}C${x(-380)} ${y(-189)} ${x(-312)} ${y(216)} ${x(152)} ${y(343)}C${x(616)} ${y(470)} ${x(684)} ${y(875)} ${x(684)} ${y(875)}`;
});
const seeded = (i: number, k: number) => {
  const x = Math.sin(i * 12.9898 + k * 78.233) * 43758.5453;
  return x - Math.floor(x);
};

export const BackgroundBeams = memo(function BackgroundBeams({ className }: { className?: string }) {
  const reduce = useReducedMotion();
  return (
    <div className={cn("pointer-events-none absolute inset-0 overflow-hidden", className)} aria-hidden>
      <svg className="absolute h-full w-full" width="100%" height="100%" viewBox="0 0 696 316" fill="none" preserveAspectRatio="xMidYMid slice">
        <path d={PATHS.join("")} stroke="url(#beams-faint)" strokeOpacity="0.07" strokeWidth="0.5" />
        {PATHS.map((d, i) => (
          <motion.path key={i} d={d} stroke={`url(#beam-${i})`} strokeOpacity="0.5" strokeWidth="0.6" />
        ))}
        <defs>
          {PATHS.map((_, i) => (
            <motion.linearGradient
              id={`beam-${i}`}
              key={i}
              initial={{ x1: "0%", x2: "0%", y1: "0%", y2: "0%" }}
              animate={reduce ? undefined : { x1: ["0%", "100%"], x2: ["0%", "95%"], y1: ["0%", "100%"], y2: ["0%", `${93 + seeded(i, 1) * 8}%`] }}
              transition={{ duration: seeded(i, 2) * 10 + 10, ease: "easeInOut", repeat: Infinity, delay: seeded(i, 3) * 10 }}
            >
              <stop stopColor="#35cdfa" stopOpacity="0" />
              <stop stopColor="#35cdfa" />
              <stop offset="32.5%" stopColor="#14acdf" />
              <stop offset="100%" stopColor="#08618a" stopOpacity="0" />
            </motion.linearGradient>
          ))}
          <radialGradient id="beams-faint" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(352 34) rotate(90) scale(555 1560.62)">
            <stop offset="0.0666667" stopColor="#74e1ff" />
            <stop offset="0.243243" stopColor="#74e1ff" />
            <stop offset="0.43594" stopColor="#74e1ff" stopOpacity="0" />
          </radialGradient>
        </defs>
      </svg>
    </div>
  );
});
