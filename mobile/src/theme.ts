// The desktop app's HUD palette (src/gui/web/app.css :root) so the phone looks like Jarvis.
import type { Phase } from './api/protocol';

export const colors = {
  bg: '#03111e',
  bgRaised: '#06314d',
  panel: '#041a2c',
  line: 'rgba(116, 225, 255, 0.18)',
  text: '#e4fbff',
  muted: '#8fb4c7',
  accent100: '#e4fbff',
  accent200: '#b3f0ff',
  accent300: '#74e1ff',
  accent400: '#35cdfa',
  accent500: '#14acdf',
  accent600: '#0b86b4',
  danger: '#ff6b6b',
  ok: '#4ade80',
  warn: '#fbbf24',
};

export const PHASE_COLOR: Record<Phase, string> = {
  stopped: '#7a7a7d', loading: '#749dc4', idle: colors.accent400, paused: '#b7b7ba', listening: '#4ade80',
  transcribing: colors.accent300, thinking: '#a78bfa', executing: '#fbbf24', approval: '#fb923c',
  speaking: colors.accent200, error: colors.danger,
};

export const font = {
  heading: { fontWeight: '700' as const, letterSpacing: 2, textTransform: 'uppercase' as const },
};
