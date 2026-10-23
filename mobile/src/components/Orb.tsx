// The HUD orb, coloured by Jarvis's phase: rotating segmented rings and a pulsing core.
import { useEffect, useRef } from 'react';
import { Animated, Easing, StyleSheet, View } from 'react-native';
import Svg, { Circle, Defs, RadialGradient, Stop } from 'react-native-svg';

import type { Phase } from '../api/protocol';
import { isBusy } from '../state/reducer';
import { PHASE_COLOR } from '../theme';

export function Orb({ phase, size = 240 }: { phase: Phase; size?: number }) {
  const spin = useRef(new Animated.Value(0)).current;
  const pulse = useRef(new Animated.Value(0)).current;
  const busy = isBusy(phase);
  const color = PHASE_COLOR[phase];

  useEffect(() => {
    spin.setValue(0);
    const loop = Animated.loop(Animated.timing(spin, {
      toValue: 1, duration: busy ? 2600 : 14000, easing: Easing.linear, useNativeDriver: true,
    }));
    loop.start();
    return () => loop.stop();
  }, [busy, spin]);

  useEffect(() => {
    const loop = Animated.loop(Animated.sequence([
      Animated.timing(pulse, { toValue: 1, duration: busy ? 500 : 1800, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
      Animated.timing(pulse, { toValue: 0, duration: busy ? 500 : 1800, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
    ]));
    loop.start();
    return () => loop.stop();
  }, [busy, pulse]);

  const rotate = spin.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] });
  const counter = spin.interpolate({ inputRange: [0, 1], outputRange: ['360deg', '0deg'] });
  const scale = pulse.interpolate({ inputRange: [0, 1], outputRange: [0.92, 1.06] });
  const glow = pulse.interpolate({ inputRange: [0, 1], outputRange: [0.35, 0.8] });
  const r = size / 2;
  const circ = (rad: number) => 2 * Math.PI * rad;

  return (
    <View style={{ width: size, height: size }} accessibilityRole="image" accessibilityLabel={`Jarvis is ${phase}`}>
      <Animated.View style={[StyleSheet.absoluteFill, { transform: [{ rotate }] }]}>
        <Svg width={size} height={size}>
          <Circle cx={r} cy={r} r={r * 0.92} stroke={color} strokeWidth={3} fill="none"
                  strokeDasharray={`${circ(r * 0.92) / 4 - 14} 14`} strokeLinecap="round" />
        </Svg>
      </Animated.View>
      <Animated.View style={[StyleSheet.absoluteFill, { transform: [{ rotate: counter }] }]}>
        <Svg width={size} height={size}>
          <Circle cx={r} cy={r} r={r * 0.78} stroke={color} strokeOpacity={0.55} strokeWidth={1.5} fill="none"
                  strokeDasharray="2 9" />
        </Svg>
      </Animated.View>
      <Animated.View style={[StyleSheet.absoluteFill, { opacity: glow, transform: [{ scale }] }]}>
        <Svg width={size} height={size}>
          <Defs>
            <RadialGradient id="core" cx="50%" cy="50%" r="50%">
              <Stop offset="0" stopColor="#e4fbff" stopOpacity="1" />
              <Stop offset="0.35" stopColor={color} stopOpacity="0.85" />
              <Stop offset="1" stopColor={color} stopOpacity="0" />
            </RadialGradient>
          </Defs>
          <Circle cx={r} cy={r} r={r * 0.55} fill="url(#core)" />
        </Svg>
      </Animated.View>
      <Svg width={size} height={size} style={StyleSheet.absoluteFill}>
        <Circle cx={r} cy={r} r={r * 0.62} stroke={color} strokeOpacity={0.35} strokeWidth={1} fill="none" />
      </Svg>
    </View>
  );
}
