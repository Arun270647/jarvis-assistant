// App state from server messages. Pure, so it is unit-tested (src/state/__tests__).
import type { LinkState } from '../api/connection';
import type { ApprovalRequest, JarvisEvent, Phase, Status, Task, Welcome } from '../api/protocol';

export type Captions = { you?: string; fromPhone?: boolean; jarvis?: string; error?: string; at: number };
export type LiveRun = { callId: string; tool: string; purpose?: string; status: 'running' | 'ok' | 'failed'; ms?: number; at: number };

export type State = {
  link: LinkState;
  linkError: string;
  pc: string;
  status: Status | null;
  phase: Phase;
  purpose: string;
  muted: boolean;
  captions: Captions | null;
  approval: ApprovalRequest | null;
  tasks: Record<number, Task>;
  live: LiveRun[];
  info: string;
};

export const initialState: State = {
  link: 'offline', linkError: '', pc: '', status: null, phase: 'stopped', purpose: '', muted: false,
  captions: null, approval: null, tasks: {}, live: [], info: '',
};

export type Action =
  | { type: 'link'; state: LinkState; error?: string }
  | { type: 'welcome'; welcome: Welcome }
  | { type: 'event'; event: JarvisEvent; now?: number }
  | { type: 'tasks'; tasks: Task[] }
  | { type: 'reset' };

const MAX_LIVE = 40;

export function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'reset':
      return initialState;
    case 'link':
      return { ...state, link: action.state, linkError: action.error ?? (action.state === 'online' ? '' : state.linkError) };
    case 'welcome': {
      const w = action.welcome;
      const tasks = { ...state.tasks };
      if (w.active_task) tasks[w.active_task.id] = w.active_task;
      return {
        ...state, status: w.status, pc: w.status.pc, phase: w.status.running ? w.status.phase : 'stopped',
        muted: w.status.muted, approval: w.pending_approval, tasks,
      };
    }
    case 'tasks': {
      const tasks: Record<number, Task> = {};
      action.tasks.forEach((t) => { tasks[t.id] = t; });
      return { ...state, tasks };
    }
    case 'event':
      return applyEvent(state, action.event, action.now ?? Date.now());
  }
}

function applyEvent(state: State, ev: JarvisEvent, now: number): State {
  switch (ev.type) {
    case 'phase': {
      const next: State = { ...state, phase: ev.phase };
      if (ev.phase === 'executing') next.purpose = ev.purpose || ev.tool || state.purpose;
      if (ev.phase === 'idle' || ev.phase === 'stopped') next.purpose = '';
      if (ev.phase === 'listening') next.captions = null;
      if (ev.phase === 'stopped') {
        next.approval = null;
        next.status = state.status && { ...state.status, running: false };
      }
      return next;
    }
    case 'transcript':
      return { ...state, captions: { you: ev.text, fromPhone: ev.source === 'phone', at: now } };
    case 'reply':
      if (ev.greeting) return { ...state, captions: { jarvis: ev.text, at: now } };
      return { ...state, captions: { ...(state.captions ?? { at: now }), jarvis: ev.text, at: now } };
    case 'error':
      return { ...state, captions: { ...(state.captions ?? { at: now }), error: ev.detail, at: now } };
    case 'cancelled':
      return { ...state, captions: { ...(state.captions ?? { at: now }), error: 'Cancelled', at: now } };
    case 'info':
      return { ...state, info: ev.detail };
    case 'approval_request':
      return { ...state, approval: ev };
    case 'approval_resolved':
      return state.approval && state.approval.id === ev.id ? { ...state, approval: null } : state;
    case 'tool_call': {
      const run: LiveRun = { callId: ev.call_id, tool: ev.tool, purpose: ev.purpose, status: 'running', at: now };
      return { ...state, live: [run, ...state.live].slice(0, MAX_LIVE) };
    }
    case 'tool_result':
      return {
        ...state,
        live: state.live.map((r) => (r.callId === ev.call_id ? { ...r, status: ev.ok ? 'ok' : 'failed', ms: ev.ms } : r)),
      };
    case 'task_update':
      return ev.task ? { ...state, tasks: { ...state.tasks, [ev.task.id]: ev.task } } : state;
    case 'ready':
      return {
        ...state, phase: 'idle',
        status: state.status ? { ...state.status, running: true, model: ev.model ?? state.status.model } : state.status,
      };
    case 'stopped':
      return { ...state, phase: 'stopped', approval: null };
    case 'mute':
      return { ...state, muted: ev.muted };
    default:
      return state;
  }
}

export const PHASE_LABEL: Record<Phase, string> = {
  stopped: 'Offline', loading: 'Starting up', idle: 'Awaiting command', paused: 'Microphone paused',
  listening: 'Listening', transcribing: 'Transcribing', thinking: 'Thinking', executing: 'Executing',
  approval: 'Awaiting your approval', speaking: 'Speaking', error: 'Error',
};

export const isBusy = (p: Phase) => ['listening', 'transcribing', 'thinking', 'executing', 'approval', 'speaking'].includes(p);
