// The pairing (address + device token) lives in the Android keystore via expo-secure-store.
import * as SecureStore from 'expo-secure-store';

import { Pairing } from '../api/protocol';

const KEY = 'jarvis.pairing.v1';
const PREFS = 'jarvis.prefs.v1';

export async function loadPairing(): Promise<Pairing | null> {
  const raw = await SecureStore.getItemAsync(KEY);
  if (!raw) return null;
  try {
    const p = JSON.parse(raw);
    return p && p.host && p.token && p.deviceId ? (p as Pairing) : null;
  } catch {
    return null;
  }
}

export const savePairing = (p: Pairing) => SecureStore.setItemAsync(KEY, JSON.stringify(p));
export const clearPairing = () => SecureStore.deleteItemAsync(KEY);

export type Prefs = { readAloud: boolean; notifications: boolean };
export const DEFAULT_PREFS: Prefs = { readAloud: false, notifications: true };

export async function loadPrefs(): Promise<Prefs> {
  try {
    return { ...DEFAULT_PREFS, ...JSON.parse((await SecureStore.getItemAsync(PREFS)) || '{}') };
  } catch {
    return DEFAULT_PREFS;
  }
}

export const savePrefs = (p: Prefs) => SecureStore.setItemAsync(PREFS, JSON.stringify(p));
