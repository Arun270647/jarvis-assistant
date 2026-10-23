// Live tool runs, agentic tasks (pause / cancel / resume / retry) and the conversation history.
import { useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { Alert, FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import type { Message, Task } from '@/api/protocol';
import { useJarvis } from '@/state/JarvisProvider';
import { colors, font } from '@/theme';

type View_ = 'live' | 'tasks' | 'history';
const ACTIVE = ['QUEUED', 'PLANNING', 'RUNNING', 'WAITING_FOR_APPROVAL', 'VERIFYING'];
const STATUS_COLOR: Record<string, string> = {
  COMPLETED: colors.ok, FAILED: colors.danger, CANCELLED: colors.muted, BLOCKED: colors.warn, PAUSED: colors.warn,
};

const time = (t?: number) => (t ? new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '');

export default function Activity() {
  const { state, request, refreshTasks } = useJarvis();
  const [view, setView] = useState<View_>('live');
  const [history, setHistory] = useState<Message[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    if (state.link !== 'online') return;
    setRefreshing(true);
    setError('');
    try {
      await refreshTasks();
      setHistory(await request<Message[]>({ type: 'history', limit: 80 }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRefreshing(false);
    }
  }, [state.link, refreshTasks, request]);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  async function taskAction(t: Task, action: 'pause' | 'cancel' | 'resume' | 'retry') {
    const go = async () => {
      try {
        await request({ type: 'task', task_id: t.id, action });
        await refreshTasks();
      } catch (e) {
        setError((e as Error).message);
      }
    };
    if (action === 'cancel') {
      Alert.alert('Cancel task?', t.objective, [{ text: 'Keep it' }, { text: 'Cancel task', style: 'destructive', onPress: go }]);
    } else {
      go();
    }
  }

  const tasks = Object.values(state.tasks).sort((a, b) => b.id - a.id);

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <View style={styles.seg}>
        {(['live', 'tasks', 'history'] as View_[]).map((v) => (
          <Pressable key={v} onPress={() => setView(v)} style={[styles.segBtn, view === v && styles.segOn]}>
            <Text style={[styles.segText, view === v && styles.segTextOn]}>{v === 'live' ? 'Running now' : v}</Text>
          </Pressable>
        ))}
      </View>
      {!!error && <Text style={styles.error}>{error}</Text>}
      {view === 'live' && (
        <FlatList
          data={state.live}
          keyExtractor={(r) => r.callId}
          ListEmptyComponent={<Text style={styles.empty}>Tool runs appear here while Jarvis works.</Text>}
          renderItem={({ item }) => (
            <View style={styles.card}>
              <View style={styles.rowBetween}>
                <Text style={styles.tool}>{item.tool}</Text>
                <Text style={{ color: item.status === 'running' ? colors.warn : item.status === 'ok' ? colors.ok : colors.danger }}>
                  {item.status === 'running' ? 'running…' : item.status}{item.ms ? ` · ${Math.round(item.ms)} ms` : ''}
                </Text>
              </View>
              {!!item.purpose && <Text style={styles.body}>{item.purpose}</Text>}
            </View>
          )}
        />
      )}
      {view === 'tasks' && (
        <FlatList
          data={tasks}
          keyExtractor={(t) => String(t.id)}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={load} tintColor={colors.accent400} />}
          ListEmptyComponent={<Text style={styles.empty}>No tasks yet. Ask Jarvis for something with several steps.</Text>}
          renderItem={({ item }) => {
            const active = ACTIVE.includes(item.status);
            const resumable = ['BLOCKED', 'PAUSED'].includes(item.status);
            return (
              <View style={styles.card}>
                <View style={styles.rowBetween}>
                  <Text style={styles.tool}>#{item.id}</Text>
                  <Text style={{ color: STATUS_COLOR[item.status] || colors.accent300, fontWeight: '600' }}>{item.status}</Text>
                </View>
                <Text style={styles.body}>{item.objective}</Text>
                {!!(item.summary || item.blocked_reason) && (
                  <Text style={styles.sub}>{item.summary || item.blocked_reason}</Text>
                )}
                <View style={styles.actions}>
                  {active && <Btn label="Pause" onPress={() => taskAction(item, 'pause')} />}
                  {(active || resumable) && <Btn label="Cancel" onPress={() => taskAction(item, 'cancel')} />}
                  {resumable && <Btn label="Resume" onPress={() => taskAction(item, 'resume')} />}
                  {item.status === 'FAILED' && <Btn label="Retry" onPress={() => taskAction(item, 'retry')} />}
                </View>
              </View>
            );
          }}
        />
      )}
      {view === 'history' && (
        <FlatList
          data={[...history].reverse()}
          keyExtractor={(m) => String(m.id)}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={load} tintColor={colors.accent400} />}
          ListEmptyComponent={<Text style={styles.empty}>No conversation yet.</Text>}
          renderItem={({ item }) => (
            <View style={[styles.msg, item.role === 'user' ? styles.msgYou : styles.msgJarvis]}>
              <Text style={styles.msgMeta}>
                {item.role === 'user' ? `you${item.source === 'phone' ? ' · phone' : item.source === 'voice' ? ' · voice' : ''}` : item.role} · {time(item.created_at)}
              </Text>
              <Text style={item.role === 'error' ? styles.error : styles.body} selectable>{item.text}</Text>
            </View>
          )}
        />
      )}
    </SafeAreaView>
  );
}

function Btn({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Pressable onPress={onPress} style={styles.btn}>
      <Text style={styles.btnText}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  seg: { flexDirection: 'row', margin: 12, borderRadius: 10, borderWidth: 1, borderColor: colors.line, overflow: 'hidden' },
  segBtn: { flex: 1, paddingVertical: 10, alignItems: 'center' },
  segOn: { backgroundColor: colors.bgRaised },
  segText: { color: colors.muted, fontSize: 13, ...font.heading, letterSpacing: 1 },
  segTextOn: { color: colors.accent200 },
  card: { marginHorizontal: 12, marginBottom: 10, padding: 14, borderRadius: 12, backgroundColor: colors.panel, borderWidth: 1, borderColor: colors.line },
  rowBetween: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  tool: { color: colors.accent300, fontWeight: '700' },
  body: { color: colors.text, fontSize: 15, marginTop: 6, lineHeight: 21 },
  sub: { color: colors.muted, fontSize: 13, marginTop: 6 },
  actions: { flexDirection: 'row', gap: 8, marginTop: 10, flexWrap: 'wrap' },
  btn: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: colors.accent500 },
  btnText: { color: colors.text, fontWeight: '600' },
  empty: { color: colors.muted, textAlign: 'center', marginTop: 40, paddingHorizontal: 30 },
  error: { color: colors.danger, marginHorizontal: 12, marginBottom: 8 },
  msg: { marginHorizontal: 12, marginBottom: 8, padding: 12, borderRadius: 12, maxWidth: '88%' },
  msgYou: { alignSelf: 'flex-end', backgroundColor: colors.bgRaised },
  msgJarvis: { alignSelf: 'flex-start', backgroundColor: colors.panel, borderWidth: 1, borderColor: colors.line },
  msgMeta: { color: colors.muted, fontSize: 11, marginBottom: 2 },
});
