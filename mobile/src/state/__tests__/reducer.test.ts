import type { JarvisEvent, Welcome } from '../../api/protocol';
import { Action, initialState, reducer, State } from '../reducer';

const run = (events: JarvisEvent[], start: State = initialState) =>
  events.reduce((s, event) => reducer(s, { type: 'event', event, now: 1 } as Action), start);

const welcome: Welcome = {
  type: 'welcome', version: 1, device: { id: 'd1', name: 'Pixel' },
  status: { running: true, phase: 'idle', busy: false, muted: true, active_task_id: 4, queued: 0,
            pending_approval: 'Allow?', model: 'm', voice: true, dry_run: false, pc: 'DESKTOP' },
  active_task: { id: 4, objective: 'Fix build', status: 'RUNNING' },
  pending_approval: { type: 'approval_request', id: 9, tool: 'forget', title: 'Allow?', rows: [], timeout_s: 180 },
};

describe('reducer', () => {
  it('takes the snapshot from welcome', () => {
    const s = reducer(initialState, { type: 'welcome', welcome });
    expect(s.pc).toBe('DESKTOP');
    expect(s.phase).toBe('idle');
    expect(s.muted).toBe(true);
    expect(s.approval?.id).toBe(9);
    expect(s.tasks[4].objective).toBe('Fix build');
  });

  it('shows one exchange: what was said, then the reply', () => {
    const s = run([
      { type: 'phase', phase: 'listening' },
      { type: 'transcript', text: 'open notepad', source: 'phone', ref: 'ph:d1:r1' },
      { type: 'phase', phase: 'executing', tool: 'open_app', purpose: 'Open notepad' },
      { type: 'reply', text: 'Opened Notepad.', source: 'phone', ref: 'ph:d1:r1' },
    ]);
    expect(s.captions).toMatchObject({ you: 'open notepad', fromPhone: true, jarvis: 'Opened Notepad.' });
    expect(s.purpose).toBe('Open notepad');
    expect(run([{ type: 'phase', phase: 'idle' }], s).purpose).toBe('');
    expect(run([{ type: 'phase', phase: 'listening' }], s).captions).toBeNull();
  });

  it('a greeting replaces the captions', () => {
    const s = run([{ type: 'transcript', text: 'hi' }, { type: 'reply', text: 'Hey Alex', greeting: true }]);
    expect(s.captions?.you).toBeUndefined();
    expect(s.captions?.jarvis).toBe('Hey Alex');
  });

  it('opens and closes the approval sheet only for the matching request', () => {
    const req = { type: 'approval_request' as const, id: 3, tool: 'forget', title: 'Allow: Delete memory?', rows: [], timeout_s: 180 };
    let s = run([req]);
    expect(s.approval?.id).toBe(3);
    s = run([{ type: 'approval_resolved', id: 2, decision: 'allow' }], s);
    expect(s.approval?.id).toBe(3);
    s = run([{ type: 'approval_resolved', id: 3, decision: 'deny' }], s);
    expect(s.approval).toBeNull();
  });

  it('tracks live tool runs and their results', () => {
    const s = run([
      { type: 'tool_call', call_id: 'c1', tool: 'run_command', purpose: 'Run npm test' },
      { type: 'tool_result', call_id: 'c1', tool: 'run_command', ok: false, status: 'failed', ms: 1200 },
    ]);
    expect(s.live).toEqual([{ callId: 'c1', tool: 'run_command', purpose: 'Run npm test', status: 'failed', ms: 1200, at: 1 }]);
  });

  it('keeps tasks, mute and stop state', () => {
    let s = run([
      { type: 'task_update', task: { id: 5, objective: 'Deploy', status: 'COMPLETED' } },
      { type: 'mute', muted: true },
      { type: 'approval_request', id: 1, tool: 't', title: 'x', rows: [], timeout_s: 10 },
    ]);
    expect(s.tasks[5].status).toBe('COMPLETED');
    expect(s.muted).toBe(true);
    s = run([{ type: 'stopped' }], s);
    expect(s.phase).toBe('stopped');
    expect(s.approval).toBeNull();
  });

  it('link state and reset', () => {
    let s = reducer(initialState, { type: 'link', state: 'unauthorized', error: 'removed' });
    expect(s).toMatchObject({ link: 'unauthorized', linkError: 'removed' });
    s = reducer(s, { type: 'link', state: 'online' });
    expect(s.linkError).toBe('');
    expect(reducer(s, { type: 'reset' })).toBe(initialState);
  });
});
