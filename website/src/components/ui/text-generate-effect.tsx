"use client";

import { motion, stagger, useAnimate, useReducedMotion } from "motion/react";
import { useEffect } from "react";
import { cn } from "@/lib/utils";

/* Aceternity UI's Text Generate Effect (ui.aceternity.com/components/text-generate-effect), adapted: words blur
   in one after another whenever the text changes, inline; plain text for people who ask for less motion. */
export function TextGenerateEffect({ words, className, duration = 0.45 }: { words: string; className?: string; duration?: number }) {
  const [scope, animate] = useAnimate();
  const reduce = useReducedMotion();

  useEffect(() => {
    if (!scope.current || reduce) return;
    animate(".tg-word", { opacity: 1, filter: "blur(0px)" }, { duration, delay: stagger(0.07) });
  }, [words, reduce, animate, scope, duration]);

  if (reduce) return <span className={className}>{words}</span>;

  return (
    <span ref={scope} className={cn("inline", className)}>
      <span className="sr-only">{words}</span>
      <span aria-hidden>
        {words.split(" ").map((word, i) => (
          <span key={`${words}-${i}`}>
            <motion.span className="tg-word inline-block opacity-0" style={{ filter: "blur(8px)" }}>{word}</motion.span>{" "}
          </span>
        ))}
      </span>
    </span>
  );
}
