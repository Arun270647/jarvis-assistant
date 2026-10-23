import * as Notifications from 'expo-notifications';
import { router, Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';

import { ApprovalSheet } from '@/components/ApprovalSheet';
import { JarvisProvider } from '@/state/JarvisProvider';
import { colors } from '@/theme';

function NotificationRouting() {
  const response = Notifications.useLastNotificationResponse();
  useEffect(() => {
    const screen = response?.notification.request.content.data?.screen;
    if (screen === 'activity') router.navigate('/activity');
    else if (screen) router.navigate('/home');
  }, [response]);
  return null;
}

export default function RootLayout() {
  return (
    <JarvisProvider>
      <StatusBar style="light" />
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.bg } }} />
      <ApprovalSheet />
      <NotificationRouting />
    </JarvisProvider>
  );
}
