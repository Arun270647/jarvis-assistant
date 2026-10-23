// Owns the pairing, the connection to the PC and the app state; screens use useJarvis().
import * as Notifications from 'expo-notifications';
import * as Speech from 'expo-speech';
import React, { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { AppState } from 'react-native';

import { JarvisConnection } from '../api/connection';
import type { Pairing, Request, ServerMessage, Task } from '../api/protocol';
import { clearPairing, DEFAULT_PREFS, loadPairing, loadPrefs, Prefs, savePairing, savePrefs } from '../storage/pairing';
import { initialState, reducer, State } from './reducer';

type Ctx = {
  ready: boolean;                       // pairing loaded from storage
  pairing: Pairing | null;
  state: State;
  prefs: Prefs;
  setPrefs: (p: Prefs) => void;
  request: <T = unknown>(req: Request, timeoutMs?: number) => Promise<T>;
  sendCommand: (text: string) => Promise<string>;
  sendVoice: (base64: string, mime?: string) => Promise<string>;
  approve: (id: number, allowed: boolean) => Promise<void>;
  refreshTasks: () => Promise<void>;
  setPaired: (p: Pairing) => Promise<void>;
  unpair: () => Promise<void>;
  reconnect: () => void;
};

const JarvisContext = createContext<Ctx | null>(null);

Notifications.setNotificationHandler({
  handleNotification: async () => ({ shouldShowBanner: true, shouldShowList: true, shouldPlaySound: true, shouldSetBadge: false }),
});

async function notify(title: string, body: string, screen: string) {
  await Notifications.scheduleNotificationAsync({ content: { title, body, data: { screen } }, trigger: null });
}

const TASK_DONE: Record<string, string> = {
  COMPLETED: 'Task completed', FAILED: 'Task failed', BLOCKED: 'Task blocked', PAUSED: 'Task paused',
};

export function JarvisProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [prefs, setPrefsState] = useState<Prefs>(DEFAULT_PREFS);
  const [state, dispatch] = useReducer(reducer, initialState);
  const conn = useRef<JarvisConnection | null>(null);
  const myRefs = useRef(new Set<string>());
  const prefsRef = useRef(prefs);
  prefsRef.current = prefs;
  const background = useRef(AppState.currentState !== 'active');
  const notified = useRef(new Set<string>());

  useEffect(() => {
    (async () => {
      const [p, pr] = await Promise.all([loadPairing(), loadPrefs()]);
      setPairing(p);
      setPrefsState(pr);
      setReady(true);
      await Notifications.setNotificationChannelAsync('jarvis', {
        name: 'Jarvis', importance: Notifications.AndroidImportance.HIGH,
      });
    })();
  }, []);

  const onMessage = useCallback((msg: ServerMessage) => {
    if (msg.type === 'welcome') { dispatch({ type: 'welcome', welcome: msg }); return; }
    if (msg.type !== 'event') return;
    const ev = msg.event;
    dispatch({ type: 'event', event: ev });
    const mine = 'ref' in ev && !!ev.ref && myRefs.current.has(ev.ref);
    if (ev.type === 'reply' && mine && prefsRef.current.readAloud) Speech.speak(ev.text);
    if (ev.type === 'timing' && ev.ref) myRefs.current.delete(ev.ref);
    if (!background.current || !prefsRef.current.notifications) return;
    // The app is in the background but still connected: tell the user about what needs them.
    if (ev.type === 'approval_request') notify('Jarvis needs your approval', ev.title, 'approval');
    else if ((ev.type === 'reply' || ev.type === 'error') && mine) {
      notify('Jarvis', ev.type === 'reply' ? ev.text : ev.detail, 'home');
    } else if (ev.type === 'task_update' && TASK_DONE[ev.task.status]) {
      const key = `${ev.task.id}:${ev.task.status}`;
      if (!notified.current.has(key)) {
        notified.current.add(key);
        notify(`${TASK_DONE[ev.task.status]} Â· #${ev.task.id}`, ev.task.objective, 'activity');
      }
    }
  }, []);

  useEffect(() => {
    if (!pairing) return;
    const c = new JarvisConnection(pairing);
    conn.current = c;
    const offMsg = c.onMessage(onMessage);
    const offState = c.onState((s) => dispatch({ type: 'link', state: s, error: c.lastError }));
    c.connect();
    const appState = AppState.addEventListener('change', (s) => {
      background.current = s !== 'active';
      if (s === 'active') c.wake();
    });
    return () => { offMsg(); offState(); appState.remove(); c.close(); conn.current = null; };
  }, [pairing, onMessage]);

  const request = useCallback(<T,>(req: Request, timeoutMs?: number) => {
    if (!conn.current) return Promise.reject(new Error('Not paired'));
    return conn.current.request<T>(req, timeoutMs);
  }, []);

  const sendCommand = useCallback(async (text: string) => {
    const r = await request<{ ref: string; result: string }>({ type: 'command', text });
    myRefs.current.add(r.ref);
    return r.result;
  }, [request]);

  /** A recorded voice command (base64 m4a); the pipeline's Whisper on the PC transcribes it. */
  const sendVoice = useCallback(async (base64: string, mime = 'audio/mp4') => {
    const r = await request<{ ref: string; result: string }>({ type: 'voice', mime, data: base64 }, 60000);
    myRefs.current.add(r.ref);
    return r.result;
  }, [request]);

  const approve = useCallback(async (id: number, allowed: boolean) => {
    await request({ type: 'approve', request_id: id, allowed });
  }, [request]);

  const refreshTasks = useCallback(async () => {
    dispatch({ type: 'tasks', tasks: await request<Task[]>({ type: 'tasks' }) });
  }, [request]);

  const setPaired = useCallback(async (p: Pairing) => {
    await savePairing(p);
    dispatch({ type: 'reset' });
    setPairing(p);
  }, []);

  const unpair = useCallback(async () => {
    await clearPairing();
    dispatch({ type: 'reset' });
    setPairing(null);
  }, []);

  const setPrefs = useCallback((p: Prefs) => { setPrefsState(p); savePrefs(p); }, []);
  const reconnect = useCallback(() => conn.current?.wake(), []);

  const value = useMemo<Ctx>(() => ({
    ready, pairing, state, prefs, setPrefs, request, sendCommand, sendVoice, approve, refreshTasks, setPaired, unpair, reconnect,
  }), [ready, pairing, state, prefs, setPrefs, request, sendCommand, sendVoice, approve, refreshTasks, setPaired, unpair, reconnect]);

  return <JarvisContext.Provider value={value}>{children}</JarvisContext.Provider>;
}

export function useJarvis(): Ctx {
  const ctx = useContext(JarvisContext);
  if (!ctx) throw new Error('useJarvis outside JarvisProvider');
  return ctx;
}
