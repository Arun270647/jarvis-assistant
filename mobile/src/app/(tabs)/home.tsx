// The Assistant screen: the orb, what you said and Jarvis's answer, a text box and hold-to-talk.
import { router } from 'expo-router';
import { useEffect, useState } from 'react';
import {
  KeyboardAvoidingView, Pressable, ScrollView, StyleSheet, Text, TextInput, View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Icon } from '@/components/Icon';
import { LinkPill } from '@/components/LinkPill';
import { MicButton } from '@/components/MicButton';
import { Orb } from '@/components/Orb';
import { useJarvis } from '@/state/JarvisProvider';
import { isBusy, PHASE_LABEL } from '@/state/reducer';
import { colors, font } from '@/theme';

export default function Home() {
  const { state, sendCommand, sendVoice, request } = useJarvis();
  const [text, setText] = useState('');
  const [note, setNote] = useState('');
  const online = state.link === 'online';
  const running = !!state.status?.running && state.phase !== 'stopped';
  const busy = isBusy(state.phase);

  useEffect(() => {
    if (state.link === 'unauthorized') router.replace('/settings');
  }, [state.link]);

  async function run(fn: () => Promise<unknown>, okNote = '') {
    setNote('');
    try {
      const r = await fn();
      if (r === 'queued') setNote('Queued behind the current command.');
      else if (r === 'stopped') setNote('Jarvis is stopped on the PC. Start it below.');
      else if (okNote) setNote(okNote);
    } catch (e) {
      setNote((e as Error).message);
    }
  }

  function send() {
    const t = text.trim();
    if (!t) return;
    setText('');
    run(() => sendCommand(t));
  }

  const c = state.captions;
  const label = state.phase === 'executing' && state.purpose ? state.purpose : PHASE_LABEL[state.phase];

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
        <LinkPill />
        <ScrollView contentContainerStyle={styles.center} keyboardShouldPersistTaps="handled">
          <Orb phase={online ? state.phase : 'stopped'} size={220} />
          <Text style={styles.state}>{online ? label : 'Not connected'}</Text>
          {c?.you ? <Text style={styles.you}>“{c.you}”</Text> : null}
          {c?.jarvis ? <Text style={styles.reply} selectable>{c.jarvis}</Text> : null}
          {c?.error ? <Text style={styles.error}>{c.error}</Text> : null}
          {!!note && <Text style={styles.note}>{note}</Text>}
          {online && !running && (
            <Pressable style={styles.start} onPress={() => run(() => request({ type: 'start' }), 'Starting Jarvis…')}>
              <Icon name="power" color={colors.bg} />
              <Text style={styles.startText}>Start Jarvis</Text>
            </Pressable>
          )}
        </ScrollView>
        <View style={styles.controls}>
          <Pressable
            style={[styles.round, !busy && styles.dim]}
            disabled={!online || !busy}
            onPress={() => run(() => request({ type: 'cancel' }))}
            accessibilityLabel="Cancel">
            <Icon name="stop" color={colors.text} />
          </Pressable>
          <MicButton
            disabled={!online || !running}
            onRecorded={(b64) => run(() => sendVoice(b64))}
            onError={setNote}
          />
          <Pressable
            style={styles.round}
            disabled={!online}
            onPress={() => run(() => request({ type: 'mute', muted: !state.muted }))}
            accessibilityLabel={state.muted ? 'Unmute the PC microphone' : 'Mute the PC microphone'}>
            <Icon name={state.muted ? 'micOff' : 'mic'} color={state.muted ? colors.danger : colors.text} />
          </Pressable>
        </View>
        <View style={styles.inputRow}>
          <TextInput
            style={styles.input}
            placeholder={online ? 'Type a command' : 'Waiting for the PC…'}
            placeholderTextColor={colors.muted}
            value={text}
            onChangeText={setText}
            onSubmitEditing={send}
            returnKeyType="send"
            editable={online}
          />
          <Pressable style={styles.send} onPress={send} disabled={!online || !text.trim()} accessibilityLabel="Send">
            <Icon name="send" color={colors.bg} size={20} />
          </Pressable>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg, paddingTop: 8 },
  center: { alignItems: 'center', paddingHorizontal: 24, paddingTop: 24, paddingBottom: 16, gap: 12 },
  state: { color: colors.accent300, fontSize: 14, marginTop: 8, ...font.heading },
  you: { color: colors.muted, fontSize: 16, textAlign: 'center', fontStyle: 'italic' },
  reply: { color: colors.text, fontSize: 18, lineHeight: 26, textAlign: 'center' },
  error: { color: colors.danger, fontSize: 15, textAlign: 'center' },
  note: { color: colors.warn, fontSize: 13, textAlign: 'center' },
  start: {
    flexDirection: 'row', gap: 8, paddingHorizontal: 20, height: 48, borderRadius: 12, alignItems: 'center',
    backgroundColor: colors.accent400, marginTop: 8,
  },
  startText: { color: colors.bg, fontWeight: '700', fontSize: 16 },
  controls: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-evenly', paddingVertical: 8 },
  round: {
    width: 52, height: 52, borderRadius: 26, borderWidth: 1, borderColor: colors.line, alignItems: 'center',
    justifyContent: 'center', backgroundColor: colors.panel,
  },
  dim: { opacity: 0.35 },
  inputRow: { flexDirection: 'row', gap: 8, padding: 12, borderTopWidth: 1, borderColor: colors.line },
  input: {
    flex: 1, height: 48, borderRadius: 24, paddingHorizontal: 18, color: colors.text, backgroundColor: colors.panel,
    borderWidth: 1, borderColor: colors.line, fontSize: 16,
  },
  send: { width: 48, height: 48, borderRadius: 24, backgroundColor: colors.accent400, alignItems: 'center', justifyContent: 'center' },
});
