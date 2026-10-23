// The app's WebSocket link to Jarvis: hello/auth, auto-reconnect with backoff, and request/ack promises.
import {
  Ack, CLOSE_REVOKED, CLOSE_UNAUTHORIZED, Pairing, Request, ServerMessage,
} from './protocol';

export type LinkState = 'connecting' | 'online' | 'offline' | 'unauthorized';

type SocketLike = {
  readyState: number;
  send(data: string): void;
  close(code?: number, reason?: string): void;
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: { code: number; reason?: string }) => void) | null;
  onerror: ((ev: unknown) => void) | null;
};
export type SocketFactory = (url: string) => SocketLike;

const OPEN = 1;
const defaultFactory: SocketFactory = (url) => new WebSocket(url) as unknown as SocketLike;

export const wsUrl = (host: string, port: number) => `ws://${host}:${port}/ws`;

type Pending = { resolve: (v: unknown) => void; reject: (e: Error) => void; timer: ReturnType<typeof setTimeout> };

export class JarvisConnection {
  state: LinkState = 'offline';
  lastError = '';
  private ws: SocketLike | null = null;
  private pending = new Map<string, Pending>();
  private seq = 0;
  private backoff = 1000;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private closed = false;
  private messageListeners = new Set<(m: ServerMessage) => void>();
  private stateListeners = new Set<(s: LinkState) => void>();

  constructor(private pairing: Pairing, private factory: SocketFactory = defaultFactory,
              private maxBackoff = 30000) {}

  onMessage(fn: (m: ServerMessage) => void) { this.messageListeners.add(fn); return () => { this.messageListeners.delete(fn); }; }
  onState(fn: (s: LinkState) => void) { this.stateListeners.add(fn); return () => { this.stateListeners.delete(fn); }; }

  connect() {
    this.closed = false;
    if (this.ws && (this.state === 'connecting' || this.state === 'online')) return;
    this.clearRetry();
    this.setState('connecting');
    const ws = this.factory(wsUrl(this.pairing.host, this.pairing.port));
    this.ws = ws;
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'hello', device_id: this.pairing.deviceId, token: this.pairing.token }));
    };
    ws.onmessage = (ev) => this.handle(String(ev.data));
    ws.onerror = () => { this.lastError = 'Cannot reach the PC'; };
    ws.onclose = (ev) => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.failPending('Connection lost');
      if (ev.code === CLOSE_UNAUTHORIZED || ev.code === CLOSE_REVOKED) {
        this.lastError = ev.code === CLOSE_REVOKED ? 'This phone was removed in the PC’s settings.' : 'This phone is not paired any more.';
        this.setState('unauthorized');
        return;
      }
      this.setState('offline');
      if (!this.closed) this.scheduleRetry();
    };
  }

  /** Reconnect now (app came to the foreground, network changed). */
  wake() {
    if (this.closed || this.state === 'unauthorized') return;
    if (this.state === 'offline') { this.backoff = 1000; this.connect(); }
  }

  close() {
    this.closed = true;
    this.clearRetry();
    this.failPending('Disconnected');
    const ws = this.ws;
    this.ws = null;
    ws?.close(1000, 'bye');
    this.setState('offline');
  }

  request<T = unknown>(req: Request, timeoutMs = 15000): Promise<T> {
    const ws = this.ws;
    if (!ws || this.state !== 'online' || ws.readyState !== OPEN) {
      return Promise.reject(new Error('Not connected to the PC'));
    }
    const id = `r${++this.seq}`;
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error('The PC did not answer in time'));
      }, timeoutMs);
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject, timer });
      ws.send(JSON.stringify({ ...req, id }));
    });
  }

  private handle(raw: string) {
    let msg: ServerMessage;
    try { msg = JSON.parse(raw); } catch { return; }
    if (msg.type === 'welcome') {
      this.backoff = 1000;
      this.lastError = '';
      this.setState('online');
    } else if (msg.type === 'ack' && msg.id && this.pending.has(msg.id)) {
      const p = this.pending.get(msg.id)!;
      this.pending.delete(msg.id);
      clearTimeout(p.timer);
      if ((msg as Ack).ok) p.resolve((msg as Ack).result);
      else p.reject(new Error((msg as Ack).error || 'Failed'));
      return;
    }
    this.messageListeners.forEach((fn) => fn(msg));
  }

  private scheduleRetry() {
    this.clearRetry();
    const delay = this.backoff;
    this.backoff = Math.min(this.maxBackoff, this.backoff * 2);
    this.retryTimer = setTimeout(() => this.connect(), delay);
  }

  private clearRetry() {
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.retryTimer = null;
  }

  private failPending(why: string) {
    this.pending.forEach((p) => { clearTimeout(p.timer); p.reject(new Error(why)); });
    this.pending.clear();
  }

  private setState(s: LinkState) {
    if (this.state === s) return;
    this.state = s;
    this.stateListeners.forEach((fn) => fn(s));
  }
}

/** One-time pairing with the code from Jarvis's settings. Resolves to the stored pairing. */
export function pairDevice(host: string, port: number, code: string, deviceName: string,
                           factory: SocketFactory = defaultFactory, timeoutMs = 10000): Promise<Pairing> {
  return new Promise((resolve, reject) => {
    const ws = factory(wsUrl(host, port));
    let done = false;
    const finish = (err: Error | null, value?: Pairing) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      ws.onclose = null;
      ws.close(1000, 'paired');
      if (err) reject(err); else resolve(value!);
    };
    const timer = setTimeout(() => finish(new Error('No answer from the PC. Is Tailscale on, on both devices?')), timeoutMs);
    ws.onopen = () => ws.send(JSON.stringify({ type: 'pair', id: 'pair', code, device_name: deviceName }));
    ws.onerror = () => finish(new Error('Cannot reach the PC. Check that Tailscale is on, on both devices.'));
    ws.onclose = () => finish(new Error('The PC closed the connection.'));
    ws.onmessage = (ev) => {
      let msg: Ack;
      try { msg = JSON.parse(String(ev.data)); } catch { return; }
      if (msg.type !== 'ack' || msg.id !== 'pair') return;
      if (!msg.ok) return finish(new Error(msg.error || 'Pairing failed'));
      const r = msg.result as { device_id: string; token: string; pc: string };
      finish(null, { host, port, deviceId: r.device_id, token: r.token, pc: r.pc });
    };
  });
}
