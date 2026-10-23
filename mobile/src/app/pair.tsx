// Pair with Jarvis: scan the QR code from the PC's Settings › Phone app, or type the address and code.
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as Device from 'expo-device';
import { router } from 'expo-router';
import { useRef, useState } from 'react';
import { ActivityIndicator, KeyboardAvoidingView, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { pairDevice } from '@/api/connection';
import { parseQr } from '@/api/protocol';
import { Icon } from '@/components/Icon';
import { useJarvis } from '@/state/JarvisProvider';
import { colors, font } from '@/theme';

export default function Pair() {
  const { setPaired } = useJarvis();
  const [permission, requestPermission] = useCameraPermissions();
  const [scanning, setScanning] = useState(false);
  const [host, setHost] = useState('');
  const [port, setPort] = useState('8765');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const scanned = useRef(false);

  async function pair(h: string, p: number, c: string) {
    setBusy(true);
    setError('');
    try {
      const name = Device.deviceName || Device.modelName || 'Android phone';
      const pairing = await pairDevice(h.trim(), p, c.trim(), name);
      await setPaired(pairing);
      router.replace('/home');
    } catch (e) {
      setError((e as Error).message);
      scanned.current = false;
    } finally {
      setBusy(false);
    }
  }

  async function startScan() {
    if (!permission?.granted) {
      const r = await requestPermission();
      if (!r.granted) { setError('Camera permission is needed to scan the code. You can type it instead.'); return; }
    }
    scanned.current = false;
    setScanning(true);
  }

  if (scanning) {
    return (
      <View style={styles.flex}>
        <CameraView
          style={styles.flex}
          barcodeScannerSettings={{ barcodeTypes: ['qr'] }}
          onBarcodeScanned={({ data }) => {
            if (scanned.current) return;
            const q = parseQr(data);
            if (!q) { setError('That is not a Jarvis pairing code.'); return; }
            scanned.current = true;
            setScanning(false);
            setHost(q.host); setPort(String(q.port)); setCode(q.code);
            pair(q.host, q.port, q.code);
          }}
        />
        <SafeAreaView style={styles.scanOverlay}>
          <Text style={styles.scanText}>Point at the QR code in Jarvis › Settings › Phone app</Text>
          <Pressable style={styles.secondary} onPress={() => setScanning(false)}><Text style={styles.secondaryText}>Cancel</Text></Pressable>
        </SafeAreaView>
      </View>
    );
  }

  return (
    <SafeAreaView style={styles.screen}>
      <KeyboardAvoidingView behavior="padding" style={styles.flex}>
        <Text style={styles.title}>Jarvis</Text>
        <Text style={styles.lead}>
          Connect this phone to Jarvis on your PC. On the PC open Settings › Phone app and click “Pair a phone”.
          Both devices need Tailscale turned on and signed in to the same account.
        </Text>
        <Pressable style={styles.primary} onPress={startScan} disabled={busy}>
          <Icon name="qr" color={colors.bg} />
          <Text style={styles.primaryText}>Scan the QR code</Text>
        </Pressable>
        <Text style={styles.or}>or enter it</Text>
        <TextInput style={styles.input} placeholder="PC address (100.x.y.z)" placeholderTextColor={colors.muted}
                   value={host} onChangeText={setHost} autoCapitalize="none" keyboardType="numbers-and-punctuation" />
        <View style={styles.row}>
          <TextInput style={[styles.input, { flex: 1 }]} placeholder="Port" placeholderTextColor={colors.muted}
                     value={port} onChangeText={setPort} keyboardType="number-pad" />
          <TextInput style={[styles.input, { flex: 2 }]} placeholder="6-digit code" placeholderTextColor={colors.muted}
                     value={code} onChangeText={setCode} keyboardType="number-pad" maxLength={6} />
        </View>
        <Pressable style={styles.secondary} disabled={busy || !host || code.length !== 6}
                   onPress={() => pair(host, parseInt(port, 10) || 8765, code)}>
          {busy ? <ActivityIndicator color={colors.accent300} /> : <Text style={styles.secondaryText}>Pair</Text>}
        </Pressable>
        {!!error && <Text style={styles.error}>{error}</Text>}
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  screen: { flex: 1, backgroundColor: colors.bg, padding: 24 },
  title: { color: colors.accent300, fontSize: 30, marginTop: 24, ...font.heading, letterSpacing: 6 },
  lead: { color: colors.muted, fontSize: 15, lineHeight: 22, marginTop: 12, marginBottom: 28 },
  primary: {
    flexDirection: 'row', gap: 10, height: 56, borderRadius: 12, backgroundColor: colors.accent400,
    alignItems: 'center', justifyContent: 'center',
  },
  primaryText: { color: colors.bg, fontSize: 17, fontWeight: '700' },
  or: { color: colors.muted, textAlign: 'center', marginVertical: 18 },
  input: {
    height: 50, borderWidth: 1, borderColor: colors.line, borderRadius: 10, paddingHorizontal: 14,
    color: colors.text, backgroundColor: colors.panel, marginBottom: 10, fontSize: 16,
  },
  row: { flexDirection: 'row', gap: 10 },
  secondary: {
    height: 52, borderRadius: 12, borderWidth: 1, borderColor: colors.accent500, alignItems: 'center',
    justifyContent: 'center', marginTop: 6,
  },
  secondaryText: { color: colors.text, fontSize: 16, fontWeight: '600' },
  error: { color: colors.danger, marginTop: 14, fontSize: 14 },
  scanOverlay: { position: 'absolute', left: 0, right: 0, bottom: 0, padding: 24, gap: 12 },
  scanText: { color: '#fff', textAlign: 'center', fontSize: 15, backgroundColor: 'rgba(0,0,0,0.5)', padding: 10, borderRadius: 8 },
});
