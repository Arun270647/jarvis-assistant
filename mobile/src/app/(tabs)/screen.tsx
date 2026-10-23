// A view of the PC's screen (all monitors), taken on request.
import { useState } from 'react';
import { ActivityIndicator, Image, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Icon } from '@/components/Icon';
import { useJarvis } from '@/state/JarvisProvider';
import { colors } from '@/theme';

type Shot = { mime: string; data: string; taken_at: number };

export default function Screen() {
  const { state, request } = useJarvis();
  const [shot, setShot] = useState<Shot | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [ratio, setRatio] = useState(16 / 9);

  async function take() {
    setBusy(true);
    setError('');
    try {
      const s = await request<Shot>({ type: 'screenshot' }, 30000);
      setShot(s);
      Image.getSize(`data:${s.mime};base64,${s.data}`, (w, h) => setRatio(w / h));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <View style={styles.bar}>
        <Text style={styles.title}>{shot ? `Taken at ${new Date(shot.taken_at * 1000).toLocaleTimeString()}` : 'PC screen'}</Text>
        <Pressable style={styles.btn} onPress={take} disabled={busy || state.link !== 'online'}>
          {busy ? <ActivityIndicator color={colors.bg} /> : <Icon name="refresh" color={colors.bg} size={18} />}
          <Text style={styles.btnText}>{shot ? 'Refresh' : 'Take screenshot'}</Text>
        </Pressable>
      </View>
      {!!error && <Text style={styles.error}>{error}</Text>}
      {shot ? (
        <ScrollView maximumZoomScale={5} minimumZoomScale={1} centerContent contentContainerStyle={styles.zoom}>
          <Image source={{ uri: `data:${shot.mime};base64,${shot.data}` }} style={[styles.img, { aspectRatio: ratio }]} resizeMode="contain" />
        </ScrollView>
      ) : (
        <Text style={styles.empty}>See what is on the PC right now. Pinch to zoom.</Text>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  bar: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', padding: 12 },
  title: { color: colors.muted, fontSize: 13 },
  btn: { flexDirection: 'row', gap: 6, alignItems: 'center', backgroundColor: colors.accent400, paddingHorizontal: 14, height: 40, borderRadius: 10 },
  btnText: { color: colors.bg, fontWeight: '700' },
  zoom: { flexGrow: 1, justifyContent: 'center' },
  img: { width: '100%' },
  error: { color: colors.danger, marginHorizontal: 12 },
  empty: { color: colors.muted, textAlign: 'center', marginTop: 60, paddingHorizontal: 30 },
});
