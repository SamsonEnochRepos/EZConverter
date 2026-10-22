import { EZClient } from "@ezconverter/api-client";
import { type AppSettings } from "@ezconverter/shared-domain";
import { useState } from "react";
import { ScrollView, View } from "react-native";
import { Badge, Btn, Card, ChoiceSheet, Field, Hero, Notice, Serif, Toggle, Txt, useT } from "../components/ui";
import { useApp } from "../state";

export function Settings() {
  const { settings, setSettings, catalog, refreshCatalog } = useApp();
  const t = useT();
  const [test, setTest] = useState<{ ok: boolean; text: string }>();
  const [sheet, setSheet] = useState<"policy" | "theme" | null>(null);
  const run = async () => {
    setTest(undefined);
    try {
      const c = new EZClient({ baseUrl: settings.remoteUrl, token: settings.remoteToken || undefined });
      const [h, caps] = await Promise.all([c.health(), c.capabilities()]);
      setTest({ ok: true, text: `Connected: ${caps.capabilities.length} verified conversions · files kept ${h.retention_hours} h · up to ${h.max_upload_mb} MB each.` });
    } catch (e) { setTest({ ok: false, text: (e as Error).message }); }
  };
  const policyLabel = { AUTO: "Automatic", LOCAL_ONLY: "Local only", REMOTE_ALLOWED: "Allow remote" }[settings.defaultPolicy];
  return (
    <ScrollView contentContainerStyle={{ paddingBottom: 28 }}>
      <Hero compact title={<>Your <Serif size={26} color="#ffffff">settings</Serif></>} subtitle="Choose where conversions run and what stays on this phone." />
      <View style={{ padding: 16, gap: 14 }}>
      <Card style={{ gap: 12 }} testID="runtime-remote">
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}><Txt size={17} weight="800">Converter</Txt>
          <Badge tone={catalog.status.state === "online" ? "good" : catalog.status.state === "unauthorized" ? "warn" : "bad"}>{catalog.status.state === "online" ? "Online" : catalog.status.state === "unauthorized" ? "Needs token" : catalog.status.state === "checking" ? "Checking" : "Offline"}</Badge></View>
        <Txt size={13} color={t.muted}>A phone can't run the conversion engines, so files are sent to a converter you control (https recommended).</Txt>
        <Field testID="url-field" label="Address" value={settings.remoteUrl} placeholder="https://converter.example.com" keyboardType="url" onChangeText={(v) => setSettings({ remoteUrl: v.trim() })} />
        <Field testID="token-field" label="Access token" value={settings.remoteToken} secure hint="Stored in the device keychain/keystore." onChangeText={(v) => setSettings({ remoteToken: v.trim() })} />
        <View style={{ flexDirection: "row", gap: 8 }}><View style={{ flex: 1 }}><Btn testID="test-connection" title="Test connection" onPress={() => void run()} /></View><View style={{ flex: 1 }}><Btn title="Refresh" variant="ghost" onPress={refreshCatalog} /></View></View>
        {test && <Notice tone={test.ok ? "good" : "bad"}>{test.text}</Notice>}
        {settings.remoteUrl.startsWith("http://") && <Notice tone="warn" title="Not encrypted">This address uses http. Use https so files and your token can't be read in transit.</Notice>}
      </Card>
      <Card style={{ gap: 12 }}>
        <Txt size={17} weight="800">Privacy &amp; processing</Txt>
        <Btn title={`Default: ${policyLabel}`} onPress={() => setSheet("policy")} />
        <Toggle testID="allow-remote" label="Allow uploading to my converter" hint="Used in Automatic mode. “Local only” always overrides this and never uploads." value={settings.allowRemote} onChange={(v) => setSettings({ allowRemote: v })} />
        <Txt size={13} color={t.muted} testID="retention-note">{catalog.status.health ? `Uploaded files and results are deleted automatically after ${catalog.status.health.retention_hours} hours.` : "Connect a converter to see its retention period."}</Txt>
      </Card>
      <Card style={{ gap: 12 }}>
        <Txt size={17} weight="800">Appearance &amp; accessibility</Txt>
        <Btn title={`Theme: ${settings.theme === "system" ? "Match system" : settings.theme === "dark" ? "Dark" : "Light"}`} onPress={() => setSheet("theme")} />
        <Toggle label="Larger text" value={settings.largeText} onChange={(v) => setSettings({ largeText: v })} />
        <Toggle label="Reduce motion" value={settings.reduceMotion} onChange={(v) => setSettings({ reduceMotion: v })} />
      </Card>
      </View>
      <ChoiceSheet visible={sheet === "policy"} title="Default processing" selected={settings.defaultPolicy} onClose={() => setSheet(null)} onSelect={(v) => setSettings({ defaultPolicy: v as AppSettings["defaultPolicy"] })}
                   choices={[{ value: "AUTO", label: "Automatic" }, { value: "LOCAL_ONLY", label: "Local only", hint: "Never upload; most conversions will be refused on a phone" }, { value: "REMOTE_ALLOWED", label: "Allow remote converters" }]} />
      <ChoiceSheet visible={sheet === "theme"} title="Theme" selected={settings.theme} onClose={() => setSheet(null)} onSelect={(v) => setSettings({ theme: v as AppSettings["theme"] })}
                   choices={[{ value: "system", label: "Match system" }, { value: "light", label: "Light" }, { value: "dark", label: "Dark" }]} />
    </ScrollView>
  );
}
