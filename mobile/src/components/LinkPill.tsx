import { Pressable, StyleSheet, Text, View } from 'react-native';

import { useJarvis } from '../state/JarvisProvider';
import { colors } from '../theme';

/** Connected · PC name / Connecting / PC offline (tap to retry). */
export function LinkPill() {
  const { state, pairing, reconnect } = useJarvis();
  const map = {
    online: { dot: colors.ok, text: `Connected · ${state.pc || pairing?.pc || 'PC'}` },
    connecting: { dot: colors.warn, text: 'Connecting…' },
    offline: { dot: colors.danger, text: 'PC offline · tap to retry' },
    unauthorized: { dot: colors.danger, text: state.linkError || 'Not paired' },
  }[state.link];
  return (
    <Pressable onPress={reconnect} style={styles.pill} accessibilityRole="button">
      <View style={[styles.dot, { backgroundColor: map.dot }]} />
      <Text style={styles.text} numberOfLines={1}>{map.text}</Text>
      {state.status?.dry_run && <Text style={styles.dry}>DRY RUN</Text>}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  pill: {
    flexDirection: 'row', alignItems: 'center', gap: 8, alignSelf: 'center', paddingHorizontal: 14,
    paddingVertical: 7, borderRadius: 20, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.panel,
  },
  dot: { width: 8, height: 8, borderRadius: 4 },
  text: { color: colors.text, fontSize: 13, maxWidth: 260 },
  dry: { color: colors.warn, fontSize: 11, fontWeight: '700' },
});
