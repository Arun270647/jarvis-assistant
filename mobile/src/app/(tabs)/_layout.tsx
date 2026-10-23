import { Redirect, Tabs } from 'expo-router';
import type { ColorValue } from 'react-native';

import { Icon, IconName } from '@/components/Icon';
import { useJarvis } from '@/state/JarvisProvider';
import { colors } from '@/theme';

const tab = (title: string, icon: IconName) => ({
  title,
  tabBarIcon: ({ color }: { color: ColorValue }) => <Icon name={icon} color={String(color ?? colors.muted)} />,
});

export default function TabsLayout() {
  const { ready, pairing } = useJarvis();
  if (ready && !pairing) return <Redirect href="/pair" />;
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarStyle: { backgroundColor: colors.panel, borderTopColor: colors.line },
        tabBarActiveTintColor: colors.accent300,
        tabBarInactiveTintColor: colors.muted,
        sceneStyle: { backgroundColor: colors.bg },
      }}>
      <Tabs.Screen name="home" options={tab('Assistant', 'home')} />
      <Tabs.Screen name="activity" options={tab('Activity', 'activity')} />
      <Tabs.Screen name="screen" options={tab('Screen', 'screen')} />
      <Tabs.Screen name="settings" options={tab('Settings', 'settings')} />
    </Tabs>
  );
}
