import { STATE_LABEL, formatBytes } from "@ezconverter/shared-domain";
import { useEffect, useState } from "react";
import { FlatList, View } from "react-native";
import { Badge, Btn, Card, FileTile, Hero, Serif, Txt, useT } from "../components/ui";
import { type HistoryEntry, loadHistory, saveHistory, useApp } from "../state";

export function History() {
  const t = useT();
  const { catalog } = useApp();
  const [items, setItems] = useState<HistoryEntry[]>([]);
  useEffect(() => { void loadHistory().then(setItems); }, []);
  return (
    <FlatList data={items} keyExtractor={(e) => e.id} contentContainerStyle={{ paddingBottom: 24 }} ItemSeparatorComponent={() => <View style={{ height: 10 }} />}
      ListHeaderComponent={
        <View style={{ gap: 14, paddingBottom: 14 }}>
          <Hero compact title={<>Conversion <Serif size={26} color="#ffffff">history</Serif></>} subtitle="Stored only on this phone. Converted files are deleted from the converter automatically." />
          {items.length > 0 && <View style={{ paddingHorizontal: 16 }}><Btn title="Clear history" variant="danger" onPress={() => { void saveHistory([]); setItems([]); }} /></View>}
        </View>
      }
      ListEmptyComponent={<View style={{ paddingHorizontal: 16 }}><Card style={{ alignItems: "center", gap: 6, padding: 28 }}><Txt size={34}>↺</Txt><Txt color={t.muted}>Nothing here yet. Finished conversions will be listed.</Txt></Card></View>}
      renderItem={({ item: e }) => (
        <View style={{ paddingHorizontal: 16 }}>
          <Card style={{ flexDirection: "row", alignItems: "center", gap: 14, padding: 12 }}>
            <FileTile ext={e.target} size={44} tone={e.state === "FAILED" ? "bad" : "accent"} />
            <View style={{ flex: 1, gap: 3 }}>
              <Txt weight="700" numberOfLines={1}>{e.name}</Txt>
              <Txt size={12} color={t.muted}>{`${e.source.toUpperCase()} → ${catalog.formats.get(e.target)?.name ?? e.target.toUpperCase()} · ${new Date(e.at).toLocaleString()}${e.size ? ` · ${formatBytes(e.size)}` : ""}`}</Txt>
              {e.error && <Txt size={12} color={t.bad}>{e.error}</Txt>}
              <Badge tone={e.state === "SUCCEEDED" ? "good" : e.state === "FAILED" ? "bad" : "neutral"}>{STATE_LABEL[e.state as keyof typeof STATE_LABEL] ?? e.state}</Badge>
            </View>
          </Card>
        </View>
      )} />
  );
}
