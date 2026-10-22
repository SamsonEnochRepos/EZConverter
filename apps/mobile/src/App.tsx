import { StatusBar } from "expo-status-bar";
import { useEffect, useRef, useState } from "react";
import { Pressable, View } from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import { G, Txt } from "./components/ui";
import { History } from "./screens/History";
import { Convert } from "./screens/Convert";
import { Settings } from "./screens/Settings";
import { AppProvider, type HistoryEntry, loadHistory, saveHistory, useApp } from "./state";

type Tab = "convert" | "history" | "settings";

function Shell() {
  const { state, theme: t } = useApp();
  const [tab, setTab] = useState<Tab>("convert");
  const recorded = useRef(new Set<string>());
  useEffect(() => {
    if (state.phase !== "finished") return;
    const fresh = state.runs.filter((r) => !recorded.current.has(r.batchId));
    if (!fresh.length) return;
    fresh.forEach((r) => recorded.current.add(r.batchId));
    void loadHistory().then((h) => {
      const entries: HistoryEntry[] = state.items.filter((i) => i.job && fresh.some((r) => r.jobKeys[i.job!.id] === i.key)).map((i) => ({ id: i.job!.id, at: Date.now(), name: i.source.name, source: i.job!.source_format, target: i.job!.target_format, state: i.job!.state, size: i.result?.size, error: i.job!.error?.message }));
      return saveHistory([...entries, ...h]);
    });
  }, [state.phase, state.runs, state.items]);
  const tabs: [Tab, string, string][] = [["convert", "Convert", G.swap], ["history", "History", G.history], ["settings", "Settings", G.gear]];
  return (
    <View style={{ flex: 1, backgroundColor: t.bg }}>
      {/* The blue header sits under the status bar in both themes, so the icons are always light. */}
      <StatusBar style="light" />
      <View style={{ flex: 1 }}>
        {tab === "convert" && <Convert goSettings={() => setTab("settings")} goHistory={() => setTab("history")} />}
        {tab === "history" && <History />}
        {tab === "settings" && <Settings />}
      </View>
      <SafeAreaView edges={["bottom", "left", "right"]} style={{ backgroundColor: t.surface, borderTopLeftRadius: 26, borderTopRightRadius: 26, borderTopWidth: 1, borderColor: t.line }}>
        <View accessibilityRole="tablist" style={{ flexDirection: "row", paddingHorizontal: 10, paddingTop: 8, paddingBottom: 6, gap: 6 }}>
          {tabs.map(([k, label, glyph]) => {
            const on = tab === k;
            return (
              <Pressable key={k} testID={`tab-${k}`} accessibilityRole="tab" accessibilityState={{ selected: on }} accessibilityLabel={label} onPress={() => setTab(k)}
                         style={{ flex: 1, minHeight: 58, borderRadius: 20, alignItems: "center", justifyContent: "center", gap: 1, backgroundColor: on ? t.accentSoft : "transparent" }}>
                <Txt size={20} weight="700" color={on ? t.accent : t.muted}>{glyph}</Txt>
                <Txt size={12} weight={on ? "800" : "500"} color={on ? t.accent : t.muted}>{label}</Txt>
              </Pressable>
            );
          })}
        </View>
      </SafeAreaView>
      <View accessibilityLiveRegion="polite" style={{ position: "absolute", width: 1, height: 1, opacity: 0 }}><Txt>{state.phase === "running" ? "Conversion in progress" : state.phase === "finished" ? "Conversion finished" : ""}</Txt></View>
    </View>
  );
}

export function App() {
  return <SafeAreaProvider><AppProvider><Shell /></AppProvider></SafeAreaProvider>;
}
