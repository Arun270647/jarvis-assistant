// Hold to talk: records AAC (m4a, mono, 16 kHz) while held and sends it on release.
import {
  AudioModule, AudioQuality, IOSOutputFormat, RecordingOptions, setAudioModeAsync, useAudioRecorder,
} from 'expo-audio';
import { File } from 'expo-file-system';
import * as Haptics from 'expo-haptics';
import { useRef, useState } from 'react';

import { useAssistantName } from '@/state/JarvisProvider';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { colors } from '../theme';
import { Icon } from './Icon';

const VOICE: RecordingOptions = {
  extension: '.m4a',
  sampleRate: 16000,
  numberOfChannels: 1,
  bitRate: 48000,
  android: { extension: '.m4a', outputFormat: 'mpeg4', audioEncoder: 'aac', sampleRate: 16000 },
  ios: {
    outputFormat: IOSOutputFormat.MPEG4AAC, audioQuality: AudioQuality.HIGH,
    linearPCMBitDepth: 16, linearPCMIsBigEndian: false, linearPCMIsFloat: false,
  },
  web: { mimeType: 'audio/webm', bitsPerSecond: 48000 },
};

const MIN_MS = 500;
const MAX_MS = 60000;

export function MicButton({ disabled, onRecorded, onError }: {
  disabled?: boolean;
  onRecorded: (base64: string) => void;
  onError: (message: string) => void;
}) {
  const recorder = useAudioRecorder(VOICE);
  const name = useAssistantName();
  const [recording, setRecording] = useState(false);
  const startedAt = useRef(0);
  const limit = useRef<ReturnType<typeof setTimeout> | null>(null);
  const active = useRef(false);

  async function start() {
    if (disabled || active.current) return;
    const perm = await AudioModule.requestRecordingPermissionsAsync();
    if (!perm.granted) { onError(`Microphone permission is needed to talk to ${name}.`); return; }
    active.current = true;
    try {
      await setAudioModeAsync({ playsInSilentMode: true, allowsRecording: true });
      await recorder.prepareToRecordAsync();
      recorder.record();
      startedAt.current = Date.now();
      setRecording(true);
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
      limit.current = setTimeout(stop, MAX_MS);
    } catch (e) {
      active.current = false;
      onError(`Could not start recording: ${(e as Error).message}`);
    }
  }

  async function stop() {
    if (!active.current) return;
    active.current = false;
    if (limit.current) clearTimeout(limit.current);
    setRecording(false);
    try {
      await recorder.stop();
      await setAudioModeAsync({ playsInSilentMode: true, allowsRecording: false });
      const uri = recorder.uri;
      if (!uri) throw new Error('no recording');
      const file = new File(uri);
      if (Date.now() - startedAt.current < MIN_MS) {
        file.delete();
        onError('Hold the button while you speak.');
        return;
      }
      const base64 = await file.base64();
      file.delete();
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
      onRecorded(base64);
    } catch (e) {
      onError(`Recording failed: ${(e as Error).message}`);
    }
  }

  return (
    <View style={styles.wrap}>
      <Pressable
        onPressIn={start}
        onPressOut={stop}
        disabled={disabled}
        accessibilityRole="button"
        accessibilityLabel="Hold to talk"
        style={[styles.button, recording && styles.recording, disabled && styles.disabled]}>
        <Icon name="mic" size={30} color={recording ? colors.bg : colors.accent200} />
      </Pressable>
      <Text style={styles.hint}>{recording ? 'Release to send' : 'Hold to talk'}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { alignItems: 'center', gap: 6 },
  button: {
    width: 76, height: 76, borderRadius: 38, alignItems: 'center', justifyContent: 'center',
    borderWidth: 2, borderColor: colors.accent400, backgroundColor: colors.panel,
  },
  recording: { backgroundColor: '#4ade80', borderColor: '#4ade80', transform: [{ scale: 1.12 }] },
  disabled: { opacity: 0.35 },
  hint: { color: colors.muted, fontSize: 12 },
});
