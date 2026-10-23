import { Redirect } from 'expo-router';
import { ActivityIndicator, View } from 'react-native';

import { useJarvis } from '@/state/JarvisProvider';
import { colors } from '@/theme';

export default function Index() {
  const { ready, pairing } = useJarvis();
  if (!ready) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.bg, alignItems: 'center', justifyContent: 'center' }}>
        <ActivityIndicator color={colors.accent400} />
      </View>
    );
  }
  return <Redirect href={pairing ? '/home' : '/pair'} />;
}
