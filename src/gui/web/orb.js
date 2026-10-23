/* Jarvis orb renderer — canvas visualizer (hud rings / reticle / waveform / particles),
   driven by real pipeline state: phase, live mic level while listening, approval countdown. */
(function () {
  "use strict";

  function hex(h) {
    h = String(h || "").trim().replace("#", "");
    if (h.length === 3) h = h.split("").map(c => c + c).join("");
    const n = parseInt(h, 16);
    if (isNaN(n)) return [148, 188, 227];
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function rgba(c, a) { return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + Math.max(0, Math.min(1, a)) + ")"; }

  function readTokens() {
    const cs = getComputedStyle(document.documentElement);
    const g = (n, f) => hex((cs.getPropertyValue(n) || "").trim() || f);
    return {
      a100: g("--color-accent-100", "#eef6ff"), a200: g("--color-accent-200", "#d6ebff"), a300: g("--color-accent-300", "#b5d9fd"),
      a400: g("--color-accent-400", "#94bce3"), a500: g("--color-accent-500", "#749dc4"), a600: g("--color-accent-600", "#597ea3"),
      a700: g("--color-accent-700", "#416180"), n300: g("--color-neutral-300", "#d4d4d7"), n400: g("--color-neutral-400", "#b7b7ba"),
      n500: g("--color-neutral-500", "#98989b"), n600: g("--color-neutral-600", "#7a7a7d"), n700: g("--color-neutral-700", "#5d5d60"),
      paper: g("--color-neutral-100", "#f5f5f8"),
    };
  }

  class JarvisOrb {
    constructor(canvas, getState) {
      this.cv = canvas;
      this.S = canvas.width;
      this.getState = getState;     // () => ({ phase, style, level, approvalFrac })
      this.C = readTokens();
      this.t0 = performance.now();
      this.A = { ring: this.C.n700, core: this.C.n700, hi: this.C.n600, glow: 0, spd: 0, ang: -Math.PI / 2, lv: 0, env: 0, cd: 1, br: 1 };
      this.initParticles();
      this.loop = this.loop.bind(this);
      this.raf = requestAnimationFrame(this.loop);
    }

    pal(ph) {
      const C = this.C;
      const T = {
        stopped: [C.n700, C.n700, C.n600, 0, 0, 0], loading: [C.a700, C.a700, C.a600, .25, .35, .25],
        idle: [C.a500, C.a500, C.a300, .45, .5, .35], text: [C.a500, C.a500, C.a300, .4, .5, .3],
        paused: [C.n600, C.n600, C.n400, .15, .3, 0],
        listening: [C.a300, C.a300, C.a100, .85, 0, .6], transcribing: [C.a400, C.a400, C.a200, .6, 0, 1.6],
        thinking: [C.a400, C.a500, C.a200, .6, 0, 3.4], executing: [C.a300, C.a300, C.a100, .75, 0, 1.2],
        approval: [C.a200, C.a300, C.a100, .7, 1.2, .2], speaking: [C.a200, C.a100, C.paper, 1, 0, .5],
        error: [C.n400, C.n600, C.n300, .1, 0, 0],
      };
      const p = T[ph] || T.stopped;
      return { ring: p[0], core: p[1], hi: p[2], glow: p[3], breathe: p[4], spd: p[5] };
    }

    initParticles() {
      let seed = 7;
      const rnd = () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };
      this.P = Array.from({ length: 90 }, () => ({ a: rnd() * Math.PI * 2, r0: .27 + rnd() * .19, sp: (.08 + rnd() * .22) * (rnd() < .5 ? 1 : -1), ph: rnd() * Math.PI * 2, r: .4, sz: .8 + rnd() * 1.2, b: rnd() }));
    }

    loop() {
      this.raf = requestAnimationFrame(this.loop);
      if (document.hidden) return;
      const st = this.getState();
      const t = (performance.now() - this.t0) / 1000;
      const dt = Math.min(.05, t - (this.lastT === undefined ? t : this.lastT));
      this.lastT = t;
      const ph = st.phase, p = this.pal(ph), A = this.A, k = 1 - Math.pow(.002, dt);
      const L = (a, b, kk) => a + (b - a) * kk;
      const LC = (a, b) => [L(a[0], b[0], k), L(a[1], b[1], k), L(a[2], b[2], k)];
      A.ring = LC(A.ring, p.ring); A.core = LC(A.core, p.core); A.hi = LC(A.hi, p.hi);
      A.glow = L(A.glow, p.glow, k); A.spd = L(A.spd, p.spd, k); A.ang += A.spd * dt;
      // Real mic level while listening (falls back to a gentle pulse until the first level event).
      const lvT = ph === "listening" ? Math.max(.12, Math.min(1, st.level || 0)) : 0;
      A.lv = L(A.lv, lvT, Math.min(1, dt * 14));
      const envT = ph === "speaking" ? Math.abs(Math.sin(t * 9.1) * Math.sin(t * 3.3)) : 0;
      A.env = L(A.env, envT, Math.min(1, dt * 16));
      A.cd = ph === "approval" ? Math.max(0, Math.min(1, st.approvalFrac == null ? 1 : st.approvalFrac)) : 1;
      A.br = 1 + .05 * Math.sin(t * Math.PI * 2 * p.breathe);
      const style = st.style || "hud";
      if (style === "particles") this.stepParticles(dt, ph, t, A);
      const ctx = this.cv.getContext("2d"), S = this.S;
      ctx.clearRect(0, 0, S, S);
      if (style === "hud") this.drawHud(ctx, S, t, ph, A);
      else if (style === "waveform") this.drawWave(ctx, S, t, ph, A);
      else if (style === "particles") this.drawParticles(ctx, S, t, ph, A);
      else this.drawReticle(ctx, S, t, ph, A);
    }

    glow(ctx, S, A, m) {
      const c = S / 2; const g = ctx.createRadialGradient(c, c, 0, c, c, S * .46);
      g.addColorStop(0, rgba(A.core, .34 * A.glow * m)); g.addColorStop(.45, rgba(A.core, .09 * A.glow * m)); g.addColorStop(1, rgba(A.core, 0));
      ctx.fillStyle = g; ctx.fillRect(0, 0, S, S);
    }
    core(ctx, S, cx, cy, cr, A, ph) {
      const u = S / 520; const cg = ctx.createRadialGradient(cx - cr * .3, cy - cr * .3, 0, cx, cy, cr);
      cg.addColorStop(0, rgba(A.hi, .92)); cg.addColorStop(.55, rgba(A.core, .6)); cg.addColorStop(1, rgba(A.core, .18));
      ctx.beginPath(); ctx.arc(cx, cy, cr, 0, Math.PI * 2); ctx.fillStyle = cg; ctx.fill(); ctx.strokeStyle = rgba(A.hi, .8); ctx.lineWidth = u; ctx.stroke();
      ctx.beginPath(); ctx.arc(cx, cy, S * .014, 0, Math.PI * 2); ctx.fillStyle = rgba(A.hi, 1); ctx.fill();
      if (ph === "paused") { ctx.beginPath(); ctx.moveTo(cx - cr * .85, cy + cr * .85); ctx.lineTo(cx + cr * .85, cy - cr * .85); ctx.strokeStyle = rgba(this.C.paper, .9); ctx.lineWidth = u * 1.6; ctx.stroke(); }
    }
    pulses(ctx, S, cx, cy, A, t, ph, from, to) {
      if (A.env < .01 && ph !== "speaking") return; const u = S / 520;
      for (let k = 0; k < 3; k++) { const p = ((t * .8) + k / 3) % 1; ctx.beginPath(); ctx.arc(cx, cy, S * (from + p * to), 0, Math.PI * 2); ctx.strokeStyle = rgba(A.hi, (1 - p) * .6 * Math.max(A.env, .25)); ctx.lineWidth = u * 1.5; ctx.stroke(); }
    }
    plus(ctx, S, x, y, a) {
      const u = S / 520, l = S * .012; ctx.strokeStyle = rgba(this.C.paper, a); ctx.lineWidth = u;
      ctx.beginPath(); ctx.moveTo(x - l, y); ctx.lineTo(x + l, y); ctx.moveTo(x, y - l); ctx.lineTo(x, y + l); ctx.stroke();
    }
    // HUD: layered rotating rings, a radial bar ring that follows the mic level (listening)
    // and the speech envelope (speaking), and the wordmark in the core (the plain name, never a dotted acronym).
    drawHud(ctx, S, t, ph, A) {
      const c = S / 2, R = S / 2, u = S / 520, small = S < 200, TAU = Math.PI * 2;
      const off = ph === "stopped" || ph === "error" || ph === "paused";
      const dim = off ? .45 : 1;
      const arc = (r, a0, a1, col, al, w) => { ctx.beginPath(); ctx.arc(c, c, r, a0, a1); ctx.strokeStyle = rgba(col, al * dim); ctx.lineWidth = w; ctx.stroke(); };
      ctx.lineCap = "butt";

      // Ambient glow behind everything.
      const g = ctx.createRadialGradient(c, c, R * .2, c, c, R);
      g.addColorStop(0, rgba(A.core, .30 * (.4 + A.glow) * dim)); g.addColorStop(.55, rgba(A.core, .10 * (.4 + A.glow) * dim)); g.addColorStop(1, rgba(A.core, 0));
      ctx.fillStyle = g; ctx.fillRect(0, 0, S, S);

      const spin = t * .12 + A.ang * .25;

      // 1. Outer bracket arcs (four segments, slow spin).
      for (let k = 0; k < 4; k++) { const a = spin * .5 + k * TAU / 4; arc(R * .965, a + .12, a + TAU / 4 - .12, A.ring, .55, u * 1.4); }

      // 2. Fine tick ring.
      const nT = small ? 36 : 120;
      for (let i = 0; i < nT; i++) {
        const a = i / nT * TAU - spin * .2, big = i % 10 === 0, r0 = R * .885, len = big ? R * .045 : R * .02;
        ctx.beginPath(); ctx.moveTo(c + Math.cos(a) * r0, c + Math.sin(a) * r0); ctx.lineTo(c + Math.cos(a) * (r0 + len), c + Math.sin(a) * (r0 + len));
        ctx.strokeStyle = rgba(big ? A.hi : A.ring, (big ? .75 : .32) * dim); ctx.lineWidth = u * (big ? 1.6 : 1); ctx.stroke();
      }

      // 3. Approval countdown on the tick ring.
      if (ph === "approval") arc(R * .86, -Math.PI / 2, -Math.PI / 2 + TAU * A.cd, A.hi, .95, u * 3);

      // 4. Thick segmented ring, counter-rotating.
      const nSeg = small ? 12 : 30;
      for (let i = 0; i < nSeg; i++) {
        const a = i / nSeg * TAU - spin * .7, gap = small ? .12 : .05;
        arc(R * .79, a + gap, a + TAU / nSeg - gap, i % 3 === 0 ? A.hi : A.ring, i % 3 === 0 ? .85 : .42, u * (small ? 7 : 9));
      }

      // 5. Sweep while working (thinking / transcribing / executing): a bright comet on its own track.
      if (A.spd > .02) {
        for (let i = 0; i < 22; i++) arc(R * .705, A.ang * 1.4 - (i + 1) * .05, A.ang * 1.4 - i * .05, A.hi, .7 * (1 - i / 22), u * 3);
      }
      arc(R * .705, 0, TAU, A.ring, .18, u);

      // 6. Radial bar ring: the voice visualizer.
      const nB = small ? 32 : 96, r0 = R * .55;
      for (let i = 0; i < nB; i++) {
        const a = i / nB * TAU - Math.PI / 2, q = i / nB;
        let amp;
        if (ph === "listening") amp = .25 + .75 * A.lv * Math.abs(Math.sin(i * .55 + t * 11) * .7 + .3 * Math.sin(i * 1.7 - t * 6));
        else if (ph === "speaking") amp = .25 + .75 * (.35 + A.env) * Math.abs(Math.sin(i * .42 + t * 8.5) * Math.cos(i * .19 - t * 2.2));
        else if (ph === "thinking" || ph === "transcribing") { let d = Math.abs(q - ((A.ang / TAU) % 1 + 1) % 1); d = Math.min(d, 1 - d); amp = .2 + .8 * Math.exp(-Math.pow(d * 9, 2)); }
        else if (ph === "executing") amp = .25 + .35 * (Math.sin(i * .8 - t * 6) > 0 ? 1 : .2);
        else if (off) amp = .12;
        else amp = .2 + .14 * Math.abs(Math.sin(t * 1.3 + i * .28));
        const len = R * .12 * amp, hot = amp > .55;
        ctx.beginPath(); ctx.moveTo(c + Math.cos(a) * r0, c + Math.sin(a) * r0); ctx.lineTo(c + Math.cos(a) * (r0 + len), c + Math.sin(a) * (r0 + len));
        ctx.strokeStyle = rgba(hot ? A.hi : A.ring, (hot ? .95 : .6) * dim); ctx.lineWidth = u * (small ? 2.2 : 2.6); ctx.stroke();
      }

      // 7. Inner bright ring with glow.
      ctx.save(); ctx.shadowColor = rgba(A.hi, .9 * dim); ctx.shadowBlur = u * (14 + 18 * Math.max(A.lv, A.env));
      arc(R * .5 * A.br, 0, TAU, A.hi, .9, u * 2.4); ctx.restore();
      arc(R * .46, 0, TAU, A.ring, .35, u);
      for (let k = 0; k < 3; k++) { const a = -spin * 1.6 + k * TAU / 3; arc(R * .475, a, a + .7, A.hi, .7, u * 2); }

      // 8. Core: dark lens, speech pulses, wordmark.
      const cg = ctx.createRadialGradient(c, c, 0, c, c, R * .44);
      cg.addColorStop(0, rgba(A.core, .28 * dim)); cg.addColorStop(.7, "rgba(2,10,20,.55)"); cg.addColorStop(1, "rgba(2,10,20,.2)");
      ctx.beginPath(); ctx.arc(c, c, R * .44, 0, TAU); ctx.fillStyle = cg; ctx.fill();
      this.pulses(ctx, S, c, c, A, t, ph, .12, .3);
      if (small) {
        const cr = R * .16 * A.br + R * .05 * Math.max(A.lv, A.env);
        const dg = ctx.createRadialGradient(c, c, 0, c, c, cr);
        dg.addColorStop(0, rgba(this.C.paper, .95 * dim)); dg.addColorStop(.4, rgba(A.hi, .8 * dim)); dg.addColorStop(1, rgba(A.core, 0));
        ctx.beginPath(); ctx.arc(c, c, cr, 0, TAU); ctx.fillStyle = dg; ctx.fill();
      } else {
        const word = (window.JarvisStrings ? window.JarvisStrings.name : "").toUpperCase();     // the assistant's name
        const fs = Math.round(R * .115 * Math.min(1, 11 / word.length));     // sized to fit the core like the old 11-character mark
        ctx.save();
        ctx.font = `600 ${fs}px "Barlow Condensed", "Barlow", sans-serif`;
        if ("letterSpacing" in ctx) ctx.letterSpacing = Math.round(fs * .32) + "px";
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.shadowColor = rgba(A.hi, .95 * dim); ctx.shadowBlur = u * (10 + 22 * A.env + 12 * A.lv);
        ctx.fillStyle = rgba(this.C.paper, (off ? .45 : .92 + .08 * Math.sin(t * 2)));
        ctx.fillText(word, c + (("letterSpacing" in ctx) ? Math.round(fs * .16) : 0), c);
        ctx.restore();
        if (ph === "paused") { ctx.beginPath(); ctx.moveTo(c - R * .3, c + R * .3); ctx.lineTo(c + R * .3, c - R * .3); ctx.strokeStyle = rgba(this.C.paper, .8); ctx.lineWidth = u * 2; ctx.stroke(); }
      }
    }
    drawReticle(ctx, S, t, ph, A) {
      const c = S / 2, R = S / 2, u = S / 520, small = S < 200;
      this.glow(ctx, S, A, 1); ctx.lineCap = "round";
      ctx.beginPath(); ctx.arc(c, c, R * .46, 0, Math.PI * 2); ctx.strokeStyle = rgba(this.C.paper, ph === "error" ? .3 : .13); ctx.lineWidth = u; if (ph === "error") ctx.setLineDash([S * .008, S * .014]); ctx.stroke(); ctx.setLineDash([]);
      const n = small ? 12 : 60;
      for (let i = 0; i < n; i++) { const a = i / n * Math.PI * 2 - Math.PI / 2; const big = small ? i % 3 === 0 : i % 15 === 0, mid = small ? true : i % 5 === 0; const len = big ? R * .05 : mid ? R * .03 : R * .016; const r0 = R * .47; ctx.beginPath(); ctx.moveTo(c + Math.cos(a) * r0, c + Math.sin(a) * r0); ctx.lineTo(c + Math.cos(a) * (r0 + len), c + Math.sin(a) * (r0 + len)); ctx.strokeStyle = rgba(this.C.paper, big ? .6 : mid ? .35 : .18); ctx.lineWidth = u * (big ? 1.4 : 1); ctx.stroke(); }
      if (ph === "approval") { ctx.beginPath(); ctx.arc(c, c, R * .46, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * A.cd); ctx.strokeStyle = rgba(A.hi, .95); ctx.lineWidth = u * 2.5; ctx.stroke(); }
      ctx.save(); ctx.translate(c, c); ctx.rotate(A.ang * .6); ctx.beginPath(); ctx.arc(0, 0, R * .34, 0, Math.PI * 2); ctx.setLineDash([S * .006, S * .012]); ctx.strokeStyle = rgba(A.ring, .55); ctx.lineWidth = u; ctx.stroke(); ctx.restore(); ctx.setLineDash([]);
      if (A.spd > .02) { for (let i = 0; i < 18; i++) { ctx.beginPath(); ctx.arc(c, c, R * .44, A.ang - (i + 1) * .06, A.ang - i * .06); ctx.strokeStyle = rgba(A.ring, .5 * (1 - i / 18)); ctx.lineWidth = u * 6; ctx.stroke(); } ctx.beginPath(); ctx.moveTo(c, c); ctx.lineTo(c + Math.cos(A.ang) * R * .46, c + Math.sin(A.ang) * R * .46); ctx.strokeStyle = rgba(A.hi, .55); ctx.lineWidth = u; ctx.stroke(); }
      this.pulses(ctx, S, c, c, A, t, ph, .085, .135);
      if (A.lv > .01) { ctx.beginPath(); ctx.arc(c, c, R * (.22 + A.lv * .12), 0, Math.PI * 2); ctx.strokeStyle = rgba(A.hi, .9); ctx.lineWidth = u * 1.5; ctx.stroke(); }
      const cr = R * .15 * A.br + R * .03 * A.lv + R * .02 * A.env; this.core(ctx, S, c, c, cr, A, ph);
      if (!small) [[c, c - R * .54], [c + R * .54, c], [c, c + R * .54], [c - R * .54, c]].forEach(([x, y]) => this.plus(ctx, S, x, y, .5));
    }
    drawWave(ctx, S, t, ph, A) {
      const c = S / 2, R = S / 2, u = S / 520, small = S < 200;
      this.glow(ctx, S, A, .8); ctx.lineCap = "round";
      const f = R * .86; ctx.strokeStyle = rgba(this.C.paper, .1); ctx.lineWidth = u; ctx.strokeRect(c - f / 2, c - f / 2, f, f);
      if (!small) [[c - f / 2, c - f / 2], [c + f / 2, c - f / 2], [c - f / 2, c + f / 2], [c + f / 2, c + f / 2]].forEach(([x, y]) => this.plus(ctx, S, x, y, .55));
      const cy = c - R * .16; this.pulses(ctx, S, c, cy, A, t, ph, .07, .2);
      const x0 = c - f / 2 + R * .06, x1 = c + f / 2 - R * .06, by = c + R * .3, n = small ? 24 : 56, dx = (x1 - x0) / (n - 1);
      ctx.beginPath(); ctx.moveTo(x0, by); ctx.lineTo(x1, by); ctx.strokeStyle = rgba(this.C.paper, .15); ctx.lineWidth = u; ctx.stroke();
      for (let i = 0; i < n; i++) {
        const x = x0 + i * dx; const g = Math.exp(-Math.pow((i - (n - 1) / 2) / (n * .28), 2)); let amp; const q = i / n;
        if (ph === "listening") amp = R * .17 * A.lv * g * Math.abs(Math.sin(i * .55 + t * 11) + .4 * Math.sin(i * 1.3 - t * 7));
        else if (ph === "speaking") amp = R * .2 * (.3 + A.env) * g * Math.abs(Math.sin(i * .42 + t * 8.5) * Math.cos(i * .17 - t * 2));
        else if (ph === "thinking") { const d = q - (t * .9 % 1); amp = R * .1 * Math.exp(-Math.pow(d * 7, 2)) + R * .02 * g; }
        else if (ph === "transcribing") { const d = q - (t * 1.6 % 1); amp = R * .07 * Math.exp(-Math.pow(d * 5, 2)); }
        else if (ph === "executing") amp = R * .05 * g * (Math.sin(i * .9 - t * 6) > 0 ? 1 : .3);
        else if (ph === "approval") amp = R * .04 * g * (q < A.cd ? 1 : .15) * (.7 + .3 * Math.sin(t * 4));
        else if (ph === "idle" || ph === "loading" || ph === "text") amp = R * .045 * g * Math.abs(Math.sin(t * 1.4 + i * .3));
        else if (ph === "error") amp = R * .03 * g * ((i * 7919) % 13 / 13);
        else amp = 0;
        amp += u * 1.5; const hot = amp > R * .05;
        ctx.beginPath(); ctx.moveTo(x, by - amp); ctx.lineTo(x, by + amp); ctx.strokeStyle = rgba(hot ? A.hi : A.ring, hot ? .95 : .6); ctx.lineWidth = u * 1.6; ctx.stroke();
      }
      if (ph === "approval") { const w = (x1 - x0) * A.cd; ctx.beginPath(); ctx.moveTo(c - w / 2, by + R * .1); ctx.lineTo(c + w / 2, by + R * .1); ctx.strokeStyle = rgba(A.hi, .9); ctx.lineWidth = u * 2.5; ctx.stroke(); }
      const cr = R * .13 * A.br + R * .03 * A.lv + R * .02 * A.env; this.core(ctx, S, c, cy, cr, A, ph);
    }
    stepParticles(dt, ph, t, A) {
      const mul = ph === "thinking" ? 5 : ph === "transcribing" ? 2.5 : ph === "executing" ? 1.8 : (ph === "stopped" || ph === "error" || ph === "paused") ? 0 : 1;
      this.P.forEach(p => {
        p.a += p.sp * mul * dt; let tr;
        if (ph === "listening") tr = p.r0 * (1 - .4 * A.lv); else if (ph === "speaking") tr = p.r0 + .12 * A.env * Math.sin(t * 5 + p.ph); else if (ph === "executing") tr = .42 + .01 * Math.sin(t * 3 + p.ph); else if (ph === "approval") tr = .46; else if (ph === "thinking") tr = p.r0 * (1 + .08 * Math.sin(t * 4 + p.ph)); else tr = p.r0 * (1 + .02 * Math.sin(t * 1.2 + p.ph));
        p.r += (tr - p.r) * Math.min(1, dt * 6);
      });
    }
    drawParticles(ctx, S, t, ph, A) {
      const c = S / 2, R = S / 2, u = S / 520;
      this.glow(ctx, S, A, 1.2);
      ctx.beginPath(); ctx.arc(c, c, R * .49, 0, Math.PI * 2); ctx.strokeStyle = rgba(this.C.paper, .09); ctx.lineWidth = u; ctx.stroke();
      if (ph === "approval") { ctx.beginPath(); ctx.arc(c, c, R * .49, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * A.cd); ctx.strokeStyle = rgba(A.hi, .9); ctx.lineWidth = u * 2.5; ctx.stroke(); }
      this.P.forEach(p => {
        const x = c + Math.cos(p.a) * R * p.r, y = c + Math.sin(p.a) * R * p.r; let al = .35 + .5 * p.b; if (ph === "stopped" || ph === "error") al *= .5;
        if (ph === "approval") { const fr = (((p.a + Math.PI / 2) / (Math.PI * 2)) % 1 + 1) % 1; al *= fr < A.cd ? 1 : .15; }
        if (ph === "thinking") { ctx.beginPath(); if (p.sp > 0) ctx.arc(c, c, R * p.r, p.a - .25, p.a); else ctx.arc(c, c, R * p.r, p.a, p.a + .25); ctx.strokeStyle = rgba(A.ring, al * .35); ctx.lineWidth = u * p.sz; ctx.stroke(); }
        ctx.beginPath(); ctx.arc(x, y, u * p.sz * (1 + .6 * A.lv + .5 * A.env), 0, Math.PI * 2); ctx.fillStyle = rgba(p.b > .7 ? A.hi : A.ring, al); ctx.fill();
      });
      this.pulses(ctx, S, c, c, A, t, ph, .07, .22);
      const cr = R * .12 * A.br + R * .03 * A.lv + R * .025 * A.env; this.core(ctx, S, c, c, cr, A, ph);
    }
  }

  // Shared state vocabulary for both windows.
  const LABEL = {
    stopped: "Offline", loading: "Starting up", idle: "Awaiting command", text: "Text-only mode", paused: "Microphone paused",
    listening: "Listening", transcribing: "Transcribing", thinking: "Thinking", executing: "Executing",
    approval: "Awaiting your approval", speaking: "Speaking", error: "Error",
  };
  const COLOR = {
    stopped: "var(--color-neutral-500)", loading: "var(--color-accent-500)", idle: "var(--color-accent-400)", text: "var(--color-accent-400)",
    paused: "var(--color-neutral-400)", listening: "var(--color-accent-200)", transcribing: "var(--color-accent-300)",
    thinking: "var(--color-accent-300)", executing: "var(--color-accent-200)", approval: "var(--color-accent-200)", speaking: "var(--color-accent-100)",
    error: "var(--color-neutral-300)",
  };
  const BUSY = ["listening", "transcribing", "thinking", "executing", "approval", "speaking"];

  function connectBridge(onReady) {
    if (!(window.qt && window.qt.webChannelTransport)) {
      const d = document.createElement("div");
      d.className = "nobridge";
      d.innerHTML = window.JarvisStrings ? window.JarvisStrings.t("standalone") : "Start it with <b>pythonw jarvis_gui.pyw</b>.";
      document.body.appendChild(d);
      return;
    }
    new QWebChannel(qt.webChannelTransport, ch => onReady(ch.objects.jarvis));
  }

  window.JarvisUI = { JarvisOrb, LABEL, COLOR, BUSY, connectBridge };
})();
