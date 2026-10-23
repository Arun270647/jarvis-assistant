// Jarvis asks before risky actions; this sheet shows the request with Allow / Deny and the time left.
import * as Haptics from 'expo-haptics';
import { useEffect, useState } from 'react';
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { useJarvis } from '../state/JarvisProvider';
import { colors, font } from '../theme';
import { Icon } from './Icon';

export function ApprovalSheet() {
  const { state, approve } = useJarvis();
  const req = state.approval;
  const [now, setNow] = useState(Date.now());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!req) return;
    setError('');
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, [req?.id]);

  if (!req) return null;
  const left = req.deadline ? Math.max(0, Math.round(req.deadline - now / 1000)) : null;

  async function answer(allowed: boolean) {
    if (!req) return;
    setBusy(true);
    try {
      await approve(req.id, allowed);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal transparent animationType="slide" visible onRequestClose={() => answer(false)}>
      <View style={styles.backdrop}>
        <View style={styles.sheet}>
          <View style={styles.kicker}>
            <Icon name="shield" size={18} color={colors.accent300} />
            <Text style={styles.kickerText}>Permission required{req.kind_label ? ` · ${req.kind_label}` : ''}</Text>
            {left !== null && <Text style={styles.timer}>{left} s</Text>}
          </View>
          <Text style={styles.title}>{req.title}</Text>
          <ScrollView style={styles.rows}>
            {req.rows.map((r, i) => (
              <View key={i} style={styles.row}>
                <Text style={styles.k}>{r.k}</Text>
                <Text style={styles.v}>{String(r.v)}{r.sub ? <Text style={styles.sub}>  {r.sub}</Text> : null}</Text>
              </View>
            ))}
          </ScrollView>
          {!!error && <Text style={styles.error}>{error}</Text>}
          <Text style={styles.note}>Nothing runs until you allow it. No answer means deny.</Text>
          <View style={styles.actions}>
            <Pressable disabled={busy} onPress={() => answer(false)} style={[styles.btn, styles.deny]}>
              <Text style={styles.denyText}>Deny</Text>
            </Pressable>
            <Pressable disabled={busy} onPress={() => answer(true)} style={[styles.btn, styles.allow]}>
              <Text style={styles.allowText}>Allow</Text>
            </Pressable>
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(0,0,0,0.6)' },
  sheet: {
    backgroundColor: colors.panel, borderTopWidth: 1, borderColor: colors.accent500, padding: 20, paddingBottom: 32,
    borderTopLeftRadius: 18, borderTopRightRadius: 18, maxHeight: '80%',
  },
  kicker: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  kickerText: { color: colors.accent300, fontSize: 12, flex: 1, ...font.heading },
  timer: { color: colors.warn, fontSize: 14, fontWeight: '700' },
  title: { color: colors.text, fontSize: 20, fontWeight: '600', marginTop: 12 },
  rows: { marginTop: 12, maxHeight: 220 },
  row: { flexDirection: 'row', paddingVertical: 6, borderBottomWidth: 1, borderColor: colors.line, gap: 12 },
  k: { color: colors.muted, width: 90, fontSize: 13 },
  v: { color: colors.text, flex: 1, fontSize: 13 },
  sub: { color: colors.muted },
  error: { color: colors.danger, marginTop: 10 },
  note: { color: colors.muted, fontSize: 12, marginTop: 12 },
  actions: { flexDirection: 'row', gap: 12, marginTop: 16 },
  btn: { flex: 1, height: 56, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
  deny: { borderWidth: 1, borderColor: colors.accent500 },
  allow: { backgroundColor: colors.accent400 },
  denyText: { color: colors.text, fontSize: 17, fontWeight: '600' },
  allowText: { color: colors.bg, fontSize: 17, fontWeight: '700' },
});
