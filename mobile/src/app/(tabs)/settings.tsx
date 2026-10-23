// Connection info, phone-side preferences, starting/stopping Jarvis, unpairing.
import * as Notifications from 'expo-notifications';
import { router } from 'expo-router';
import { Alert, Pressable, ScrollView, StyleSheet, Switch, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { LinkPill } from '@/components/LinkPill';
import { useJarvis } from '@/state/JarvisProvider';
import { colors, font } from '@/theme';

export default function Settings() {
  const { state, pairing, prefs, setPrefs, request, unpair } = useJarvis();
  const online = state.link === 'online';
  const running = !!state.status?.running && state.phase !== 'stopped';

  async function toggleNotifications(on: boolean) {
    if (on) {
      const { status } = await Notifications.requestPermissionsAsync();
      if (status !== 'granted') { Alert.alert('Notifications are off', 'Allow notifications for Jarvis in Android settings.'); return; }
    }
    setPrefs({ ...prefs, notifications: on });
  }

  function confirmUnpair() {
    Alert.alert('Unpair this phone?', 'You will need a new code from the PC to connect again. Remove the phone in Jarvis’s Settings › Phone app too.', [
      { text: 'Keep' },
      { text: 'Unpair', style: 'destructive', onPress: async () => { await unpair(); router.replace('/pair'); } },
    ]);
  }

  function powerJarvis() {
    if (!running) { request({ type: 'start' }).catch(() => {}); return; }
    Alert.alert('Stop Jarvis on the PC?', 'It stops listening and releases the microphone. You can start it again from here.', [
      { text: 'Keep running' },
      { text: 'Stop', style: 'destructive', onPress: () => request({ type: 'stop' }, 30000).catch(() => {}) },
    ]);
  }

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <ScrollView contentContainerStyle={{ padding: 16, gap: 16 }}>
        <LinkPill />
        <Section title="This PC">
          <Row k="Name" v={state.pc || pairing?.pc || '—'} />
          <Row k="Address" v={pairing ? `${pairing.host}:${pairing.port}` : '—'} />
          <Row k="Model" v={state.status?.model || '—'} />
          <Row k="Mode" v={state.status?.dry_run ? 'Dry run (actions are simulated)' : 'Live'} />
          {state.link === 'unauthorized' && <Text style={styles.error}>{state.linkError} Pair again.</Text>}
          <Pressable style={styles.btn} disabled={!online} onPress={powerJarvis}>
            <Text style={styles.btnText}>{running ? 'Stop Jarvis' : 'Start Jarvis'}</Text>
          </Pressable>
        </Section>
        <Section title="On this phone">
          <Toggle label="Read Jarvis’s replies aloud" value={prefs.readAloud} onChange={(v) => setPrefs({ ...prefs, readAloud: v })} />
          <Toggle label="Notify me while the app is in the background" value={prefs.notifications} onChange={toggleNotifications} />
          <Text style={styles.help}>
            Notifications work while the app is open or recently in the background. When Android closes the app,
            nothing arrives until you open it again.
          </Text>
        </Section>
        <Pressable style={[styles.btn, styles.danger]} onPress={confirmUnpair}>
          <Text style={styles.dangerText}>Unpair this phone</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {children}
    </View>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <View style={styles.row}>
      <Text style={styles.k}>{k}</Text>
      <Text style={styles.v} selectable>{v}</Text>
    </View>
  );
}

function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <View style={styles.row}>
      <Text style={[styles.v, { flex: 1 }]}>{label}</Text>
      <Switch value={value} onValueChange={onChange} trackColor={{ true: colors.accent500, false: colors.bgRaised }}
              thumbColor={value ? colors.accent200 : colors.muted} />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  section: { padding: 16, borderRadius: 14, backgroundColor: colors.panel, borderWidth: 1, borderColor: colors.line, gap: 4 },
  sectionTitle: { color: colors.accent300, fontSize: 13, marginBottom: 6, ...font.heading },
  row: { flexDirection: 'row', alignItems: 'center', paddingVertical: 8, gap: 12 },
  k: { color: colors.muted, width: 80 },
  v: { color: colors.text, fontSize: 15, flexShrink: 1 },
  help: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  btn: { height: 48, borderRadius: 12, borderWidth: 1, borderColor: colors.accent500, alignItems: 'center', justifyContent: 'center', marginTop: 8 },
  btnText: { color: colors.text, fontWeight: '600', fontSize: 15 },
  danger: { borderColor: colors.danger },
  dangerText: { color: colors.danger, fontWeight: '600', fontSize: 15 },
  error: { color: colors.danger, marginTop: 6 },
});
