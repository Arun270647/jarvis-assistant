import { JarvisConnection, pairDevice, SocketFactory } from '../connection';
import { CLOSE_REVOKED, parseQr } from '../protocol';

class FakeSocket {
  static all: FakeSocket[] = [];
  readyState = 0;
  sent: any[] = [];
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: { code: number; reason?: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  constructor(public url: string) { FakeSocket.all.push(this); }
  send(data: string) { this.sent.push(JSON.parse(data)); }
  close(code = 1000) { this.readyState = 3; this.onclose?.({ code }); }
  open() { this.readyState = 1; this.onopen?.({}); }
  receive(msg: object) { this.onmessage?.({ data: JSON.stringify(msg) }); }
  drop(code = 1006) { this.readyState = 3; this.onclose?.({ code }); }
}

const factory: SocketFactory = (url) => new FakeSocket(url) as any;
const pairing = { host: '100.76.187.123', port: 8765, deviceId: 'd1', token: 'tok', pc: 'DESKTOP' };
const welcome = { type: 'welcome', version: 1, device: { id: 'd1', name: 'Pixel' }, status: {}, active_task: null, pending_approval: null };

beforeEach(() => { FakeSocket.all = []; jest.useFakeTimers(); });
afterEach(() => jest.useRealTimers());

describe('JarvisConnection', () => {
  it('authenticates with hello and goes online on welcome', () => {
    const c = new JarvisConnection(pairing, factory);
    const states: string[] = [];
    c.onState((s) => states.push(s));
    c.connect();
    const ws = FakeSocket.all[0];
    expect(ws.url).toBe('ws://100.76.187.123:8765/ws');
    ws.open();
    expect(ws.sent[0]).toEqual({ type: 'hello', device_id: 'd1', token: 'tok' });
    ws.receive(welcome);
    expect(states).toEqual(['connecting', 'online']);
  });

  it('resolves and rejects requests by ack id, and times out', async () => {
    const c = new JarvisConnection(pairing, factory);
    c.connect();
    const ws = FakeSocket.all[0];
    ws.open(); ws.receive(welcome);
    const ok = c.request({ type: 'command', text: 'open notepad' });
    const id = ws.sent[1].id;
    expect(ws.sent[1]).toMatchObject({ type: 'command', text: 'open notepad' });
    ws.receive({ type: 'ack', id, ok: true, result: { ref: 'ph:d1:r1', result: 'sent' } });
    await expect(ok).resolves.toEqual({ ref: 'ph:d1:r1', result: 'sent' });
    const bad = c.request({ type: 'screenshot' });
    ws.receive({ type: 'ack', id: ws.sent[2].id, ok: false, error: 'Screenshots are turned off' });
    await expect(bad).rejects.toThrow('Screenshots are turned off');
    const slow = c.request({ type: 'status' }, 1000);
    jest.advanceTimersByTime(1001);
    await expect(slow).rejects.toThrow('did not answer');
  });

  it('refuses requests while offline', async () => {
    const c = new JarvisConnection(pairing, factory);
    await expect(c.request({ type: 'status' })).rejects.toThrow('Not connected');
  });

  it('reconnects with backoff after a drop and fails pending requests', async () => {
    const c = new JarvisConnection(pairing, factory, 4000);
    c.connect();
    FakeSocket.all[0].open(); FakeSocket.all[0].receive(welcome);
    const pending = c.request({ type: 'status' });
    FakeSocket.all[0].drop();
    await expect(pending).rejects.toThrow('Connection lost');
    expect(c.state).toBe('offline');
    jest.advanceTimersByTime(1000);
    expect(FakeSocket.all).toHaveLength(2);
    FakeSocket.all[1].drop();
    jest.advanceTimersByTime(1999);
    expect(FakeSocket.all).toHaveLength(2);
    jest.advanceTimersByTime(1);
    expect(FakeSocket.all).toHaveLength(3);
  });

  it('stops reconnecting when the phone was removed', () => {
    const c = new JarvisConnection(pairing, factory);
    c.connect();
    FakeSocket.all[0].open();
    FakeSocket.all[0].drop(CLOSE_REVOKED);
    expect(c.state).toBe('unauthorized');
    expect(c.lastError).toMatch(/removed/);
    jest.advanceTimersByTime(60000);
    expect(FakeSocket.all).toHaveLength(1);
  });

  it('close() stops for good', () => {
    const c = new JarvisConnection(pairing, factory);
    c.connect();
    FakeSocket.all[0].open();
    c.close();
    jest.advanceTimersByTime(60000);
    expect(FakeSocket.all).toHaveLength(1);
  });
});

describe('pairing', () => {
  it('sends the code and returns the stored pairing', async () => {
    const p = pairDevice('100.76.187.123', 8765, '123456', 'Pixel 8', factory);
    const ws = FakeSocket.all[0];
    ws.open();
    expect(ws.sent[0]).toEqual({ type: 'pair', id: 'pair', code: '123456', device_name: 'Pixel 8' });
    ws.receive({ type: 'ack', id: 'pair', ok: true, result: { device_id: 'd9', token: 'secret', pc: 'DESKTOP' } });
    await expect(p).resolves.toEqual({ host: '100.76.187.123', port: 8765, deviceId: 'd9', token: 'secret', pc: 'DESKTOP' });
  });

  it('reports a wrong code', async () => {
    const p = pairDevice('h', 1, '000000', 'x', factory);
    FakeSocket.all[0].open();
    FakeSocket.all[0].receive({ type: 'ack', id: 'pair', ok: false, error: 'That code is not valid or has expired.' });
    await expect(p).rejects.toThrow('not valid');
  });

  it('parses only Jarvis QR codes', () => {
    expect(parseQr('{"v":1,"app":"jarvis","host":"100.1.2.3","port":8765,"code":"012345","pc":"PC"}')?.code).toBe('012345');
    expect(parseQr('https://example.com')).toBeNull();
    expect(parseQr('{"app":"jarvis","host":"h","port":1,"code":"12"}')).toBeNull();
  });
});
