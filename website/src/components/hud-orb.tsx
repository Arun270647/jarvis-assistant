"use client";

import { useEffect, useRef } from "react";

/* The app's HUD orb (src/gui/web/orb.js, "hud" style), drawn on a canvas and driven by the demo's phase.
   The palette is the app's dark-theme cyan ramp; the orb sits on the always-dark "screen" in both page themes.
   Rendering pauses off screen and in background tabs; with reduced motion it draws one still frame. */

export type OrbPhase = "idle" | "listening" | "transcribing" | "thinking" | "executing" | "approval" | "speaking";
export type OrbState = { phase: OrbPhase; level: number; approvalFrac: number };

type RGB = [number, number, number];
const C = {
  a100: [228, 251, 255], a200: [179, 240, 255], a300: [116, 225, 255], a400: [53, 205, 250],
  a500: [20, 172, 223], a600: [11, 134, 180], a700: [8, 97, 138], paper: [245, 245, 248],
} satisfies Record<string, RGB>;

const PALETTE: Record<OrbPhase, [RGB, RGB, RGB, number, number, number]> = {
  idle: [C.a500, C.a500, C.a300, .45, .5, .35],
  listening: [C.a300, C.a300, C.a100, .85, 0, .6],
  transcribing: [C.a400, C.a400, C.a200, .6, 0, 1.6],
  thinking: [C.a400, C.a500, C.a200, .6, 0, 3.4],
  executing: [C.a300, C.a300, C.a100, .75, 0, 1.2],
  approval: [C.a200, C.a300, C.a100, .7, 1.2, .2],
  speaking: [C.a200, C.a100, C.paper, 1, 0, .5],
};

const rgba = (c: RGB, a: number) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${Math.max(0, Math.min(1, a))})`;

type Anim = { ring: RGB; core: RGB; hi: RGB; glow: number; spd: number; ang: number; lv: number; env: number; cd: number; br: number };

function draw(ctx: CanvasRenderingContext2D, S: number, t: number, ph: OrbPhase, A: Anim, font: string) {
  const c = S / 2, R = S / 2, u = S / 520, small = S < 260, TAU = Math.PI * 2;
  const arc = (r: number, a0: number, a1: number, col: RGB, al: number, w: number) => {
    ctx.beginPath(); ctx.arc(c, c, r, a0, a1); ctx.strokeStyle = rgba(col, al); ctx.lineWidth = w; ctx.stroke();
  };
  ctx.clearRect(0, 0, S, S);
  ctx.lineCap = "butt";

  const g = ctx.createRadialGradient(c, c, R * .2, c, c, R);
  g.addColorStop(0, rgba(A.core, .30 * (.4 + A.glow))); g.addColorStop(.55, rgba(A.core, .10 * (.4 + A.glow))); g.addColorStop(1, rgba(A.core, 0));
  ctx.fillStyle = g; ctx.fillRect(0, 0, S, S);

  const spin = t * .12 + A.ang * .25;
  for (let k = 0; k < 4; k++) { const a = spin * .5 + k * TAU / 4; arc(R * .965, a + .12, a + TAU / 4 - .12, A.ring, .55, u * 1.4); }

  const nT = small ? 60 : 120;
  for (let i = 0; i < nT; i++) {
    const a = i / nT * TAU - spin * .2, big = i % 10 === 0, r0 = R * .885, len = big ? R * .045 : R * .02;
    ctx.beginPath(); ctx.moveTo(c + Math.cos(a) * r0, c + Math.sin(a) * r0); ctx.lineTo(c + Math.cos(a) * (r0 + len), c + Math.sin(a) * (r0 + len));
    ctx.strokeStyle = rgba(big ? A.hi : A.ring, big ? .75 : .32); ctx.lineWidth = u * (big ? 1.6 : 1); ctx.stroke();
  }

  if (ph === "approval") arc(R * .86, -Math.PI / 2, -Math.PI / 2 + TAU * A.cd, A.hi, .95, u * 3);

  const nSeg = small ? 18 : 30;
  for (let i = 0; i < nSeg; i++) {
    const a = i / nSeg * TAU - spin * .7, gap = small ? .09 : .05;
    arc(R * .79, a + gap, a + TAU / nSeg - gap, i % 3 === 0 ? A.hi : A.ring, i % 3 === 0 ? .85 : .42, u * (small ? 8 : 9));
  }

  if (A.spd > .02) for (let i = 0; i < 22; i++) arc(R * .705, A.ang * 1.4 - (i + 1) * .05, A.ang * 1.4 - i * .05, A.hi, .7 * (1 - i / 22), u * 3);
  arc(R * .705, 0, TAU, A.ring, .18, u);

  const nB = small ? 64 : 96, r0 = R * .55;
  for (let i = 0; i < nB; i++) {
    const a = i / nB * TAU - Math.PI / 2, q = i / nB;
    let amp: number;
    if (ph === "listening") amp = .25 + .75 * A.lv * Math.abs(Math.sin(i * .55 + t * 11) * .7 + .3 * Math.sin(i * 1.7 - t * 6));
    else if (ph === "speaking") amp = .25 + .75 * (.35 + A.env) * Math.abs(Math.sin(i * .42 + t * 8.5) * Math.cos(i * .19 - t * 2.2));
    else if (ph === "thinking" || ph === "transcribing") { let d = Math.abs(q - (((A.ang / TAU) % 1) + 1) % 1); d = Math.min(d, 1 - d); amp = .2 + .8 * Math.exp(-Math.pow(d * 9, 2)); }
    else if (ph === "executing") amp = .25 + .35 * (Math.sin(i * .8 - t * 6) > 0 ? 1 : .2);
    else amp = .2 + .14 * Math.abs(Math.sin(t * 1.3 + i * .28));
    const len = R * .12 * amp, hot = amp > .55;
    ctx.beginPath(); ctx.moveTo(c + Math.cos(a) * r0, c + Math.sin(a) * r0); ctx.lineTo(c + Math.cos(a) * (r0 + len), c + Math.sin(a) * (r0 + len));
    ctx.strokeStyle = rgba(hot ? A.hi : A.ring, hot ? .95 : .6); ctx.lineWidth = u * (small ? 2.4 : 2.6); ctx.stroke();
  }

  ctx.save(); ctx.shadowColor = rgba(A.hi, .9); ctx.shadowBlur = u * (14 + 18 * Math.max(A.lv, A.env));
  arc(R * .5 * A.br, 0, TAU, A.hi, .9, u * 2.4); ctx.restore();
  arc(R * .46, 0, TAU, A.ring, .35, u);
  for (let k = 0; k < 3; k++) { const a = -spin * 1.6 + k * TAU / 3; arc(R * .475, a, a + .7, A.hi, .7, u * 2); }

  const cg = ctx.createRadialGradient(c, c, 0, c, c, R * .44);
  cg.addColorStop(0, rgba(A.core, .28)); cg.addColorStop(.7, "rgba(2,10,20,.55)"); cg.addColorStop(1, "rgba(2,10,20,.2)");
  ctx.beginPath(); ctx.arc(c, c, R * .44, 0, TAU); ctx.fillStyle = cg; ctx.fill();

  if (A.env > .01 || ph === "speaking") {
    for (let k = 0; k < 3; k++) {
      const p = ((t * .8) + k / 3) % 1;
      ctx.beginPath(); ctx.arc(c, c, S * (.12 + p * .3), 0, TAU); ctx.strokeStyle = rgba(A.hi, (1 - p) * .6 * Math.max(A.env, .25)); ctx.lineWidth = u * 1.5; ctx.stroke();
    }
  }

  const word = "JARVIS";
  const fs = Math.round(R * .115);
  ctx.save();
  ctx.font = `600 ${fs}px ${font}`;
  const spacing = Math.round(fs * .32);
  if ("letterSpacing" in ctx) (ctx as CanvasRenderingContext2D & { letterSpacing: string }).letterSpacing = `${spacing}px`;
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.shadowColor = rgba(A.hi, .95); ctx.shadowBlur = u * (10 + 22 * A.env + 12 * A.lv);
  ctx.fillStyle = rgba(C.paper, .92 + .08 * Math.sin(t * 2));
  ctx.fillText(word, c + ("letterSpacing" in ctx ? spacing / 2 : 0), c);
  ctx.restore();
}

export function HudOrb({ getState, className, label }: { getState: () => OrbState; className?: string; label: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef(getState);

  useEffect(() => {
    stateRef.current = getState;
  }, [getState]);

  useEffect(() => {
    const cv = canvas.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const font = getComputedStyle(document.documentElement).getPropertyValue("--font-barlow-condensed").trim() || "sans-serif";
    const start = PALETTE.idle;
    const A: Anim = { ring: [...start[0]], core: [...start[1]], hi: [...start[2]], glow: start[3], spd: start[5], ang: -Math.PI / 2, lv: 0, env: 0, cd: 1, br: 1 };
    let raf = 0, visible = true, last: number | undefined, size = 0;
    const t0 = performance.now();

    const resize = () => {
      const css = cv.getBoundingClientRect().width;
      const px = Math.max(1, Math.round(css * Math.min(window.devicePixelRatio || 1, 2)));
      if (px !== size) { size = px; cv.width = px; cv.height = px; }
    };

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      if (!visible || document.hidden) return;
      const t = (now - t0) / 1000;
      const dt = Math.min(.05, last === undefined ? 0 : t - last);
      last = t;
      const st = stateRef.current();
      const p = PALETTE[st.phase];
      const k = 1 - Math.pow(.002, dt);
      const L = (a: number, b: number, kk: number) => a + (b - a) * kk;
      const LC = (a: RGB, b: RGB): RGB => [L(a[0], b[0], k), L(a[1], b[1], k), L(a[2], b[2], k)];
      A.ring = LC(A.ring, p[0]); A.core = LC(A.core, p[1]); A.hi = LC(A.hi, p[2]);
      A.glow = L(A.glow, p[3], k); A.spd = L(A.spd, p[5], k); A.ang += A.spd * dt;
      const lvT = st.phase === "listening" ? Math.max(.12, Math.min(1, st.level)) : 0;
      A.lv = L(A.lv, lvT, Math.min(1, dt * 14));
      const envT = st.phase === "speaking" ? Math.abs(Math.sin(t * 9.1) * Math.sin(t * 3.3)) : 0;
      A.env = L(A.env, envT, Math.min(1, dt * 16));
      A.cd = st.phase === "approval" ? Math.max(0, Math.min(1, st.approvalFrac)) : 1;
      A.br = 1 + .05 * Math.sin(t * Math.PI * 2 * p[4]);
      draw(ctx, size, t, st.phase, A, font);
    };

    resize();
    const ro = new ResizeObserver(() => { resize(); if (reduce) draw(ctx, size, 2, "idle", A, font); });
    ro.observe(cv);
    if (reduce) {
      document.fonts?.ready.then(() => draw(ctx, size, 2, "idle", A, font));
      draw(ctx, size, 2, "idle", A, font);
      return () => ro.disconnect();
    }
    const io = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; }, { threshold: 0 });
    io.observe(cv);
    raf = requestAnimationFrame(frame);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); io.disconnect(); };
  }, []);

  return <canvas ref={canvas} className={className} role="img" aria-label={label} />;
}
