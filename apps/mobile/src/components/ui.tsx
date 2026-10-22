import { type ReactNode, useEffect, useRef, useState } from "react";
import { Animated, Easing, FlatList, Modal, Pressable, Switch, Text, TextInput, View, type ViewStyle } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import type { OptionOut } from "@ezconverter/contracts";
import { serifFamily, type Theme } from "../theme";
import { useApp } from "../state";

export const MIN_TOUCH = 48;

export function useT(): Theme { return useApp().theme; }

/** Text-presentation glyphs (U+FE0E stops Android turning them into colour emoji). Kept dependency-free on purpose. */
export const G = { swap: "⇄", history: "↺", gear: "⚙︎", up: "↑", diag: "↗", check: "✓", close: "✕", batch: "⧉", lock: "◉", warn: "⚠︎" };

export function Txt({ children, size = 15, weight = "400", color, style, ...rest }: { children: ReactNode; size?: number; weight?: "400" | "500" | "600" | "700" | "800"; color?: string; style?: object } & Omit<React.ComponentProps<typeof Text>, "style">) {
  const t = useT();
  const scale = useApp().settings.largeText ? 1.2 : 1;
  return <Text {...rest} style={[{ fontSize: size * scale, fontWeight: weight, color: color ?? t.text }, style]}>{children}</Text>;
}

/** The single italic-serif accent word inside a headline. */
export function Serif({ children, size, color }: { children: ReactNode; size: number; color?: string }) {
  const t = useT();
  const scale = useApp().settings.largeText ? 1.2 : 1;
  return <Text style={{ fontFamily: serifFamily(), fontStyle: "italic", fontWeight: "500", fontSize: size * scale, color: color ?? t.text }}>{children}</Text>;
}

function lift(t: Theme, strong = false): ViewStyle {
  return { shadowColor: t.shadow, shadowOpacity: t.dark ? 0.35 : strong ? 0.28 : 0.09, shadowRadius: strong ? 14 : 16, shadowOffset: { width: 0, height: strong ? 6 : 6 }, elevation: strong ? 5 : 2 };
}

export function Card({ children, style, testID }: { children: ReactNode; style?: ViewStyle; testID?: string }) {
  const t = useT();
  return <View testID={testID} style={[{ backgroundColor: t.surface, borderColor: t.line, borderWidth: 1, borderRadius: 22, padding: 16 }, lift(t), style]}>{children}</View>;
}

export function Btn({ title, onPress, variant = "secondary", disabled, testID, accessibilityLabel, icon }: { title: string; onPress: () => void; variant?: "primary" | "secondary" | "danger" | "ghost" | "hero"; disabled?: boolean; testID?: string; accessibilityLabel?: string; icon?: string }) {
  const t = useT();
  const bg = { primary: t.accent, secondary: t.surface, danger: t.badSoft, ghost: "transparent", hero: "#ffffff" }[variant];
  const fg = { primary: t.accentText, secondary: t.text, danger: t.bad, ghost: t.muted, hero: "#0a4fd6" }[variant];
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={accessibilityLabel ?? title} accessibilityState={{ disabled: !!disabled }} testID={testID} disabled={disabled} onPress={onPress}
               style={({ pressed }) => [{ minHeight: MIN_TOUCH, minWidth: MIN_TOUCH, paddingHorizontal: 20, borderRadius: 999, alignItems: "center", justifyContent: "center", flexDirection: "row", gap: 8, backgroundColor: bg,
                                          borderWidth: variant === "secondary" ? 1 : 0, borderColor: t.line, opacity: disabled ? 0.45 : pressed ? 0.85 : 1, transform: [{ scale: pressed ? 0.985 : 1 }] },
                                        variant === "primary" && !disabled ? lift(t, true) : null, variant === "hero" ? lift({ ...t, shadow: "#051450" }, true) : null]}>
      {icon ? <Txt weight="700" color={fg}>{icon}</Txt> : null}
      <Txt weight="600" color={fg}>{title}</Txt>
    </Pressable>
  );
}

export function Badge({ tone = "neutral", children }: { tone?: "good" | "warn" | "bad" | "info" | "neutral"; children: string }) {
  const t = useT();
  const c = { good: [t.goodSoft, t.good], warn: [t.warnSoft, t.warn], bad: [t.badSoft, t.bad], info: [t.infoSoft, t.info], neutral: [t.surface2, t.muted] }[tone];
  return <View style={{ backgroundColor: c[0], borderRadius: 99, paddingHorizontal: 10, paddingVertical: 4, alignSelf: "flex-start" }}><Txt size={12} weight="700" color={c[1]}>{children}</Txt></View>;
}

export function Notice({ tone = "info", title, children }: { tone?: "info" | "warn" | "bad" | "good"; title?: string; children?: ReactNode }) {
  const t = useT();
  const c = { good: [t.goodSoft, t.good], warn: [t.warnSoft, t.warn], bad: [t.badSoft, t.bad], info: [t.infoSoft, t.info] }[tone];
  return (
    <View accessibilityRole={tone === "bad" ? "alert" : undefined} style={{ backgroundColor: c[0], borderRadius: 18, padding: 14, gap: 2 }}>
      {title && <Txt weight="700" color={c[1]}>{title}</Txt>}
      {children !== undefined && <Txt size={14} color={c[1]}>{children}</Txt>}
    </View>
  );
}

/** A document tile carrying the file's format, so a row is recognisable at a glance. */
export function FileTile({ ext, tone = "accent", size = 48 }: { ext?: string; tone?: "accent" | "good" | "bad"; size?: number }) {
  const t = useT();
  const label = (ext ?? "").replace(/^\./, "").slice(0, 4).toUpperCase();
  const [bg, fg] = { accent: [t.accentSoft, t.accent], good: [t.goodSoft, t.good], bad: [t.badSoft, t.bad] }[tone];
  return (
    <View accessible={false} importantForAccessibility="no-hide-descendants" style={{ width: size, height: size, borderRadius: 16, backgroundColor: bg, alignItems: "center", justifyContent: "center" }}>
      <View style={{ width: size * 0.42, height: size * 0.52, borderRadius: 5, borderWidth: 2, borderColor: fg, opacity: 0.85 }} />
      {label ? <View style={{ position: "absolute", bottom: -6, backgroundColor: fg, borderRadius: 6, paddingHorizontal: 5, paddingVertical: 1 }}><Text style={{ color: tone === "accent" ? t.accentText : t.surface, fontSize: 9, fontWeight: "800", letterSpacing: 0.4 }}>{label}</Text></View> : null}
    </View>
  );
}

/** value null = indeterminate: shows a moving bar, never an invented percentage. */
export function Progress({ value, label }: { value: number | null; label: string }) {
  const t = useT();
  const reduce = useApp().settings.reduceMotion;
  const x = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (value !== null || reduce) return;
    const loop = Animated.loop(Animated.timing(x, { toValue: 1, duration: 1300, easing: Easing.inOut(Easing.ease), useNativeDriver: true }));
    loop.start();
    return () => loop.stop();
  }, [value, reduce, x]);
  const pct = value === null ? undefined : Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <View accessible accessibilityRole="progressbar" accessibilityLabel={label} accessibilityValue={pct === undefined ? { text: "In progress" } : { min: 0, max: 100, now: pct }}
          style={{ height: 10, borderRadius: 99, backgroundColor: t.surface2, overflow: "hidden" }}>
      {pct === undefined
        ? <Animated.View style={{ position: "absolute", top: 0, bottom: 0, width: "40%", borderRadius: 99, backgroundColor: t.accent, transform: [{ translateX: x.interpolate({ inputRange: [0, 1], outputRange: [-120, 320] }) }] }} />
        : <View style={{ height: 10, width: `${pct}%`, borderRadius: 99, backgroundColor: t.accent }} />}
    </View>
  );
}

/**
 * Circular progress drawn from two clipped half-circles (no SVG dependency).
 * `value` null spins a fixed arc and shows no number, same rule as Progress.
 */
export function ProgressRing({ value, label, size = 112, stroke = 10 }: { value: number | null; label: string; size?: number; stroke?: number }) {
  const t = useT();
  const reduce = useApp().settings.reduceMotion;
  const spin = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (value !== null || reduce) return;
    const loop = Animated.loop(Animated.timing(spin, { toValue: 1, duration: 1100, easing: Easing.linear, useNativeDriver: true }));
    loop.start();
    return () => loop.stop();
  }, [value, reduce, spin]);
  const pct = value === null ? undefined : Math.round(Math.max(0, Math.min(1, value)) * 100);
  const p = pct ?? 28;
  const half = size / 2;
  const right = (Math.min(p, 50) / 50) * 180, left = (Math.max(p - 50, 0) / 50) * 180;
  const circle = (borders: object, deg: number, offsetLeft: number) => (
    <View style={{ position: "absolute", top: 0, left: offsetLeft, width: size, height: size, borderRadius: half, borderWidth: stroke, borderColor: "transparent", ...borders, transform: [{ rotate: `${deg - 135}deg` }] }} />
  );
  const spinStyle = pct === undefined ? { transform: [{ rotate: spin.interpolate({ inputRange: [0, 1], outputRange: ["0deg", "360deg"] }) }] } : null;
  return (
    <View accessible accessibilityRole="progressbar" accessibilityLabel={label} accessibilityValue={pct === undefined ? { text: "In progress" } : { min: 0, max: 100, now: pct }}
          style={{ width: size, height: size, alignItems: "center", justifyContent: "center" }}>
      <View style={{ position: "absolute", width: size, height: size, borderRadius: half, borderWidth: stroke, borderColor: t.surface2 }} />
      <Animated.View style={[{ position: "absolute", width: size, height: size }, spinStyle]}>
        <View style={{ position: "absolute", left: half, width: half, height: size, overflow: "hidden" }}>{circle({ borderTopColor: t.accent, borderRightColor: t.accent }, right, -half)}</View>
        <View style={{ position: "absolute", left: 0, width: half, height: size, overflow: "hidden" }}>{circle({ borderBottomColor: t.accent, borderLeftColor: t.accent }, left, 0)}</View>
      </Animated.View>
      {pct !== undefined && <Txt size={size * 0.26} weight="800" color={t.accent}>{`${pct}%`}</Txt>}
    </View>
  );
}

/** Blue gradient-style header with soft rounded bottom, like the reference. Extends under the status bar. */
export function Hero({ title, subtitle, badge, compact, children }: { title: ReactNode; subtitle?: string; badge?: ReactNode; compact?: boolean; children?: ReactNode }) {
  const t = useT();
  const insets = useSafeAreaInsets();
  return (
    <View style={{ backgroundColor: t.hero, borderBottomLeftRadius: 34, borderBottomRightRadius: 34, paddingTop: insets.top + 12, paddingHorizontal: 20, paddingBottom: compact ? 22 : 28, overflow: "hidden" }}>
      <View pointerEvents="none" style={{ position: "absolute", right: -90, top: -110, width: 260, height: 260, borderRadius: 130, backgroundColor: t.heroSoft, opacity: 0.55 }} />
      <View pointerEvents="none" style={{ position: "absolute", left: -70, bottom: -110, width: 190, height: 190, borderRadius: 95, backgroundColor: t.heroSoft, opacity: 0.35 }} />
      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", minHeight: 44 }}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
          <View style={{ width: 40, height: 40, borderRadius: 14, backgroundColor: "#ffffff", alignItems: "center", justifyContent: "center" }}><Text style={{ color: "#0a4fd6", fontSize: 20, fontWeight: "800" }}>{G.swap}</Text></View>
          <Text style={{ color: t.heroText, fontSize: 18, fontWeight: "800" }}>EZConverter</Text>
        </View>
        {badge}
      </View>
      <View style={{ marginTop: compact ? 14 : 22, gap: 8 }}>
        <Text accessibilityRole="header" style={{ color: t.heroText, fontSize: compact ? 26 : 32, lineHeight: compact ? 32 : 38, fontWeight: "800" }}>{title}</Text>
        {subtitle ? <Text style={{ color: t.heroText, opacity: 0.88, fontSize: 15, lineHeight: 21 }}>{subtitle}</Text> : null}
      </View>
      {children ? <View style={{ marginTop: 20, gap: 10 }}>{children}</View> : null}
    </View>
  );
}

export function SectionTitle({ children }: { children: string }) {
  return <Txt size={18} weight="800" style={{ marginTop: 4 }}>{children}</Txt>;
}

export interface Choice<T extends string | number> { value: T; label: string; hint?: string; group?: string }

/** Bottom-sheet single choice list. Used for target formats and enum settings. */
export function ChoiceSheet<T extends string | number>({ visible, title, choices, selected, onSelect, onClose }: { visible: boolean; title: string; choices: Choice<T>[]; selected?: T; onSelect: (v: T) => void; onClose: () => void }) {
  const t = useT();
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose} accessibilityViewIsModal>
      <Pressable style={{ flex: 1, backgroundColor: "rgba(6,13,31,0.55)" }} onPress={onClose} accessibilityLabel="Close" />
      <View style={{ maxHeight: "72%", backgroundColor: t.surface, borderTopLeftRadius: 28, borderTopRightRadius: 28, paddingTop: 10 }}>
        <View style={{ alignSelf: "center", width: 44, height: 5, borderRadius: 3, backgroundColor: t.line }} />
        <View style={{ paddingHorizontal: 20, paddingVertical: 12, flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
          <Txt size={19} weight="800">{title}</Txt>
          <Btn title="Close" variant="ghost" onPress={onClose} />
        </View>
        <FlatList data={choices} keyExtractor={(c) => String(c.value)} contentContainerStyle={{ paddingBottom: 28, paddingHorizontal: 12 }}
          renderItem={({ item, index }) => {
            const showGroup = item.group && (index === 0 || choices[index - 1]?.group !== item.group);
            const on = item.value === selected;
            return (
              <View>
                {showGroup && <Txt size={12} weight="800" color={t.muted} style={{ paddingHorizontal: 12, paddingTop: 12, paddingBottom: 6, letterSpacing: 0.6 }}>{item.group!.toUpperCase()}</Txt>}
                <Pressable accessibilityRole="menuitem" accessibilityState={{ selected: on }} onPress={() => { onSelect(item.value); onClose(); }}
                           style={{ minHeight: MIN_TOUCH, paddingHorizontal: 14, paddingVertical: 8, borderRadius: 16, flexDirection: "row", alignItems: "center", justifyContent: "space-between", backgroundColor: on ? t.accentSoft : "transparent" }}>
                  <View style={{ flex: 1 }}>
                    <Txt weight={on ? "700" : "500"} color={on ? t.accent : t.text}>{item.label}</Txt>
                    {item.hint && <Txt size={12} color={t.muted}>{item.hint}</Txt>}
                  </View>
                  {on && <Txt weight="800" color={t.accent}>{G.check}</Txt>}
                </Pressable>
              </View>
            );
          }} />
      </View>
    </Modal>
  );
}

export function Field({ label, value, onChangeText, secure, placeholder, hint, keyboardType, testID }: { label: string; value: string; onChangeText: (v: string) => void; secure?: boolean; placeholder?: string; hint?: string; keyboardType?: "default" | "numeric" | "url"; testID?: string }) {
  const t = useT();
  return (
    <View style={{ gap: 6 }}>
      <Txt size={13} weight="700" color={t.muted}>{label}</Txt>
      <TextInput testID={testID} accessibilityLabel={label} value={value} onChangeText={onChangeText} secureTextEntry={secure} placeholder={placeholder} placeholderTextColor={t.muted}
                 autoCapitalize="none" autoCorrect={false} keyboardType={keyboardType} style={{ minHeight: MIN_TOUCH, borderWidth: 1, borderColor: t.line, borderRadius: 18, paddingHorizontal: 16, color: t.text, backgroundColor: t.surface }} />
      {hint && <Txt size={12} color={t.muted}>{hint}</Txt>}
    </View>
  );
}

export function Toggle({ label, hint, value, onChange, testID }: { label: string; hint?: string; value: boolean; onChange: (v: boolean) => void; testID?: string }) {
  const t = useT();
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: MIN_TOUCH }}>
      <View style={{ flex: 1 }}><Txt weight="600">{label}</Txt>{hint && <Txt size={13} color={t.muted}>{hint}</Txt>}</View>
      <Switch testID={testID} accessibilityLabel={label} value={value} onValueChange={onChange} trackColor={{ true: t.accent, false: t.line }} thumbColor="#ffffff" />
    </View>
  );
}

/** Settings editor generated from a capability's declared options (same schema the web UI uses). */
export function OptionsForm({ specs, values, onChange }: { specs: OptionOut[]; values: Record<string, unknown>; onChange: (next: Record<string, unknown>) => void }) {
  const t = useT();
  const [sheet, setSheet] = useState<OptionOut | null>(null);
  const set = (name: string, v: unknown) => onChange({ ...values, [name]: v });
  return (
    <View style={{ gap: 14, backgroundColor: t.surface2, borderRadius: 18, padding: 14 }} testID="options-form">
      {specs.map((s) => {
        const v = values[s.name] ?? s.default;
        const label = s.name.replace(/_/g, " ");
        if (s.type === "bool") return <Toggle key={s.name} label={label} hint={s.description} value={Boolean(v)} onChange={(b) => set(s.name, b)} />;
        if (s.type === "enum") return (
          <View key={s.name} style={{ gap: 6 }}>
            <Txt size={13} weight="700" color={t.muted}>{label}</Txt>
            <Btn title={String(v === "\t" ? "Tab" : v)} onPress={() => setSheet(s)} />
            <Txt size={12} color={t.muted}>{s.description}</Txt>
          </View>
        );
        return <Field key={s.name} label={label} value={v === null || v === undefined ? "" : String(v)} keyboardType={s.type === "string" ? "default" : "numeric"}
                      hint={`${s.description}${s.minimum != null && s.maximum != null ? ` (${s.minimum}–${s.maximum})` : ""}`}
                      onChangeText={(txt) => { if (s.type === "string") set(s.name, txt); else if (txt.trim() !== "" && !Number.isNaN(Number(txt))) set(s.name, Number(txt)); }} />;
      })}
      {sheet && <ChoiceSheet visible title={sheet.name.replace(/_/g, " ")} selected={String(values[sheet.name] ?? sheet.default)} onClose={() => setSheet(null)}
                             choices={(sheet.choices ?? []).map((c) => ({ value: String(c), label: c === "\t" ? "Tab" : String(c) }))}
                             onSelect={(val) => set(sheet.name, (sheet.choices ?? []).find((c) => String(c) === val) ?? val)} />}
    </View>
  );
}
