import { BUILT_IN_PRESETS, FIDELITY, formatBytes, summarize } from "@ezconverter/shared-domain";
import { useKeepAwake } from "expo-keep-awake";
import { useEffect, useState } from "react";
import { ActivityIndicator, FlatList, Pressable, ScrollView, View } from "react-native";
import { ItemCard } from "../components/ItemCard";
import { Badge, Btn, Card, ChoiceSheet, FileTile, G, Hero, Notice, ProgressRing, SectionTitle, Serif, Toggle, Txt, useT } from "../components/ui";
import { pickFiles, saveAndShare } from "../platform";
import { type HistoryEntry, loadHistory, useApp } from "../state";

export function Convert({ goSettings, goHistory }: { goSettings: () => void; goHistory: () => void }) {
  const { state, controller, catalog, runtimes } = useApp();
  const t = useT();
  const sum = summarize(state);
  const running = state.phase === "running";
  if (running) return <Running />;
  if (state.items.length === 0) return <Home goSettings={goSettings} goHistory={goHistory} />;
  if (state.phase === "finished") return <Results />;

  const ready = state.items.filter((i) => i.intake.state === "ready" && !i.duplicateOf);
  const families = new Set(ready.map((i) => catalog.formats.get(i.file?.format ?? "")?.family).filter(Boolean) as string[]);
  const presets = BUILT_IN_PRESETS.filter((p) => families.has(p.family));
  return (
    <View style={{ flex: 1 }}>
      <FlatList data={state.items} keyExtractor={(i) => i.key} contentContainerStyle={{ paddingBottom: 170 }} ItemSeparatorComponent={() => <View style={{ height: 12 }} />}
        ListHeaderComponent={
          <View style={{ gap: 12, paddingBottom: 14 }}>
            <Hero compact title={`${state.items.length} file${state.items.length > 1 ? "s" : ""} ready to shape`} subtitle="Pick a format for each one, then convert.">
              <PolicyRow />
              {presets.length > 0 && <PresetRow presets={presets} />}
            </Hero>
            <View style={{ gap: 10, paddingHorizontal: 16 }}>
              {runtimes.length === 0 && <Notice tone="warn" title="No converter set up">Add the address of a converter in Settings. Nothing has been uploaded.</Notice>}
              {state.items.some((i) => i.planError?.code === "remote_consent_required") && (
                <Notice tone="warn" title="Needs a remote converter">Your phone can't convert some of these on its own. Allow uploading in Settings to continue. Nothing has been uploaded.</Notice>
              )}
            </View>
          </View>
        }
        renderItem={({ item }) => <View style={{ paddingHorizontal: 16 }}><ItemCard item={item} phase={state.phase} controller={controller} /></View>} />
      <View style={{ position: "absolute", left: 12, right: 12, bottom: 10, padding: 14, gap: 10, backgroundColor: t.surface, borderRadius: 26, borderWidth: 1, borderColor: t.line, shadowColor: t.shadow, shadowOpacity: 0.2, shadowRadius: 18, shadowOffset: { width: 0, height: 8 }, elevation: 8 }}>
        <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
          <Txt size={13} weight="600" color={t.muted} testID="start-summary">
            {sum.uploading > 0 ? `Reading ${sum.uploading} file${sum.uploading > 1 ? "s" : ""}…` : `${sum.startable} of ${state.items.length} ready${sum.skipped ? ` · ${sum.skipped} skipped` : ""}`}
          </Txt>
          <Pressable accessibilityRole="button" accessibilityLabel="Clear all" onPress={() => void controller.clear()} style={{ minHeight: 32, justifyContent: "center", paddingHorizontal: 6 }}><Txt size={13} weight="700" color={t.muted}>Clear all</Txt></Pressable>
        </View>
        <View style={{ flexDirection: "row", gap: 10 }}>
          <View style={{ flex: 1 }}><Btn title="Add files" icon="+" onPress={async () => { const f = await pickFiles(); if (f.length) await controller.addFiles(f); }} /></View>
          <View style={{ flex: 1 }}><Btn testID="start-button" title={`Convert ${sum.startable || ""}`.trim()} icon={G.swap} variant="primary" disabled={sum.startable === 0 || sum.uploading > 0} onPress={() => void controller.start()} /></View>
        </View>
      </View>
    </View>
  );
}

/** Hero-coloured button that opens a sheet of choices. */
function HeroSelect({ label, testID, onPress }: { label: string; testID?: string; onPress: () => void }) {
  return (
    <Pressable testID={testID} accessibilityRole="button" accessibilityLabel={label} onPress={onPress}
               style={{ minHeight: 48, borderRadius: 99, paddingHorizontal: 18, backgroundColor: "rgba(255,255,255,0.18)", flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
      <Txt weight="600" color="#ffffff">{label}</Txt>
      <Txt weight="700" color="#ffffff">⌄</Txt>
    </Pressable>
  );
}

function PolicyRow() {
  const { state, controller } = useApp();
  const [open, setOpen] = useState(false);
  const label = { AUTO: "Automatic", LOCAL_ONLY: "Local only", REMOTE_ALLOWED: "Allow remote" }[state.defaultPolicy];
  return (
    <>
      <HeroSelect testID="policy-button" label={`Processing: ${label}`} onPress={() => setOpen(true)} />
      <ChoiceSheet visible={open} title="Processing" selected={state.defaultPolicy} onClose={() => setOpen(false)} onSelect={(p) => controller.setPolicy("all", p)}
                   choices={[{ value: "AUTO", label: "Automatic", hint: "Use this device when it can; ask before uploading" }, { value: "LOCAL_ONLY", label: "Local only", hint: "Never upload. This phone can't convert on its own, so most conversions will be refused" }, { value: "REMOTE_ALLOWED", label: "Allow remote converters", hint: "Upload to your converter" }]} />
    </>
  );
}

function PresetRow({ presets }: { presets: typeof BUILT_IN_PRESETS }) {
  const { controller, catalog } = useApp();
  const [open, setOpen] = useState(false);
  return (
    <>
      <HeroSelect label="Apply a preset…" onPress={() => setOpen(true)} />
      <ChoiceSheet visible={open} title="Presets" onClose={() => setOpen(false)} choices={presets.map((p) => ({ value: p.id, label: p.name, hint: p.description }))}
                   onSelect={(id) => { const p = presets.find((x) => x.id === id); if (p) controller.applyPreset(p, new Map([...catalog.formats].map(([k, v]) => [k, v.family]))); }} />
    </>
  );
}

function ActionTile({ glyph, title, onPress, testID }: { glyph: string; title: string; onPress: () => void; testID?: string }) {
  const t = useT();
  return (
    <Pressable testID={testID} accessibilityRole="button" accessibilityLabel={title} onPress={onPress} style={({ pressed }) => ({ flex: 1, opacity: pressed ? 0.85 : 1 })}>
      <Card style={{ minHeight: 148, justifyContent: "space-between", backgroundColor: t.surface }}>
        <View style={{ alignItems: "flex-end" }}><Txt size={20} weight="700" color={t.muted}>{G.diag}</Txt></View>
        <View style={{ gap: 12 }}>
          <View style={{ width: 46, height: 46, borderRadius: 23, backgroundColor: t.accent, alignItems: "center", justifyContent: "center" }}><Txt size={22} weight="700" color={t.accentText}>{glyph}</Txt></View>
          <Txt size={18} weight="800" style={{ lineHeight: 23 }}>{title}</Txt>
        </View>
      </Card>
    </Pressable>
  );
}

function Home({ goSettings, goHistory }: { goSettings: () => void; goHistory: () => void }) {
  const { controller, catalog, settings, setSettings, runtimes } = useApp();
  const t = useT();
  const [recent, setRecent] = useState<HistoryEntry[]>([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => { void loadHistory().then((h) => setRecent(h.filter((e) => e.state === "SUCCEEDED").slice(0, 3))); }, []);
  const needsConsent = runtimes.length > 0 && !settings.allowRemote && settings.defaultPolicy === "AUTO";
  const pick = async () => { setBusy(true); try { const f = await pickFiles(); if (f.length) await controller.addFiles(f); } finally { setBusy(false); } };
  const st = catalog.status.state;
  const dot = st === "online" ? "#7dffb0" : st === "checking" ? "#ffe08a" : "#ff9f9f";
  return (
    <ScrollView contentContainerStyle={{ paddingBottom: 28 }} testID="home-scroll">
      <Hero
        badge={<View accessible accessibilityLabel={`Converter ${st}`} style={{ flexDirection: "row", alignItems: "center", gap: 7, backgroundColor: "rgba(255,255,255,0.18)", borderRadius: 99, paddingHorizontal: 12, paddingVertical: 7 }}>
          <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: dot }} /><Txt size={12} weight="700" color="#ffffff">{st === "online" ? "Online" : st === "checking" ? "Connecting" : st === "unauthorized" ? "Needs token" : "Offline"}</Txt></View>}
        title={<>Convert your files in <Serif size={32} color="#ffffff">any format</Serif></>}
        subtitle="Pick files from your phone. EZConverter identifies each one by its content and only offers conversions it can really do.">
        <Btn testID="choose-files" variant="hero" icon={G.up} title={busy ? "Opening…" : "Choose files"} disabled={busy || runtimes.length === 0} onPress={() => void pick()} />
      </Hero>
      <View style={{ padding: 16, gap: 14 }}>
        {runtimes.length === 0 ? (
          <Notice tone="warn" title="Set up a converter first">This phone sends files to a converter you control. Add its address in Settings.</Notice>
        ) : needsConsent ? (
          <Card style={{ gap: 8 }}>
            <Txt size={17} weight="800">Allow uploading?</Txt>
            <Txt size={14} color={t.muted}>Phones can't convert most formats on their own. Files are sent over an encrypted connection to your converter and deleted automatically{catalog.status.health ? ` after ${catalog.status.health.retention_hours} hours` : ""}.</Txt>
            <Toggle testID="allow-remote" label="Allow uploading to my converter" value={settings.allowRemote} onChange={(v) => setSettings({ allowRemote: v })} />
          </Card>
        ) : null}

        <View style={{ flexDirection: "row", gap: 12 }}>
          <ActionTile testID="tile-batch" glyph={G.batch} title={"Batch\nconversions"} onPress={() => void pick()} />
          <ActionTile testID="tile-history" glyph={G.history} title={"Conversions\nhistory"} onPress={goHistory} />
        </View>

        <Card style={{ gap: 8 }} testID="runtime-status">
          <Txt size={17} weight="800">Converter</Txt>
          {catalog.status.state === "checking" && <View style={{ flexDirection: "row", gap: 8 }}><ActivityIndicator /><Txt color={t.muted}>Connecting…</Txt></View>}
          {catalog.status.state === "online" && <Badge tone="good">{`${catalog.status.caps?.capabilities.length ?? 0} verified conversions`}</Badge>}
          {catalog.status.state === "unauthorized" && <Notice tone="warn" title="Access token needed">Enter your token in Settings.</Notice>}
          {catalog.status.state === "offline" && <Notice tone="bad" title="Can't reach the converter">{catalog.status.error ?? "Check the address in Settings."}</Notice>}
          <Btn title="Settings" variant="ghost" onPress={goSettings} />
        </Card>

        <SectionTitle>Recent</SectionTitle>
        {recent.length === 0 ? <Txt size={14} color={t.muted}>Your finished conversions will appear here.</Txt> : recent.map((e) => (
          <Card key={e.id} style={{ flexDirection: "row", alignItems: "center", gap: 14, padding: 12 }}>
            <FileTile ext={e.target} size={44} />
            <View style={{ flex: 1 }}>
              <Txt weight="700" numberOfLines={1}>{e.name}</Txt>
              <Txt size={12} color={t.muted}>{`${e.source.toUpperCase()} → ${catalog.formats.get(e.target)?.name ?? e.target.toUpperCase()}${e.size ? ` · ${formatBytes(e.size)}` : ""}`}</Txt>
            </View>
          </Card>
        ))}
      </View>
    </ScrollView>
  );
}

function Running() {
  const { state, controller } = useApp();
  const t = useT();
  const sum = summarize(state);
  useKeepAwake();     // keep the screen on while converting; the converter keeps working if the OS suspends the app anyway
  const value = sum.indeterminate && sum.progress === 0 ? null : sum.progress;
  return (
    <View style={{ flex: 1 }}>
      <FlatList data={state.items} keyExtractor={(i) => i.key} contentContainerStyle={{ paddingBottom: 150 }} ItemSeparatorComponent={() => <View style={{ height: 12 }} />}
        ListHeaderComponent={
          <View style={{ paddingBottom: 14 }}>
            <Hero compact title={<>Processing your <Serif size={26} color="#ffffff">files</Serif></>} subtitle="Each file is checked before it is handed back.">
              <View style={{ alignItems: "center", paddingTop: 6 }}>
                <View style={{ backgroundColor: t.surface, borderRadius: 999, padding: 10 }}><ProgressRing value={value} label="Overall progress" size={116} /></View>
              </View>
            </Hero>
          </View>
        }
        renderItem={({ item }) => <View style={{ paddingHorizontal: 16 }}><ItemCard item={item} phase={state.phase} controller={controller} /></View>} />
      <View style={{ position: "absolute", left: 12, right: 12, bottom: 10, padding: 14, gap: 8, backgroundColor: t.surface, borderRadius: 26, borderWidth: 1, borderColor: t.line, shadowColor: t.shadow, shadowOpacity: 0.2, shadowRadius: 18, shadowOffset: { width: 0, height: 8 }, elevation: 8 }} testID="overall-progress">
        <Txt weight="700">Converting… {sum.indeterminate ? "" : `${Math.round(sum.progress * 100)}%`}</Txt>
        <Txt size={12} color={t.muted}>You can leave the app: conversion continues on the converter and picks up where you left off.</Txt>
        <Btn title="Cancel remaining" variant="danger" onPress={() => void controller.cancel()} />
      </View>
    </View>
  );
}

function Results() {
  const { state, controller } = useApp();
  const t = useT();
  const [busy, setBusy] = useState<string>();
  const [msg, setMsg] = useState<string>();
  const sum = summarize(state);
  const ok = state.items.filter((i) => i.job?.state === "SUCCEEDED");
  const bad = state.items.filter((i) => i.job?.state === "FAILED");
  const other = state.items.filter((i) => !i.job || i.job.state === "CANCELLED");
  const share = async (key: string) => {
    const it = state.items.find((i) => i.key === key); if (!it?.result) return;
    setBusy(key); setMsg(undefined);
    try { const url = await controller.downloadUrl(key); if (url) await saveAndShare(url, it.result.name, it.result.mime); } catch (e) { setMsg((e as Error).message); } finally { setBusy(undefined); }
  };
  const zip = async () => { setBusy("zip"); setMsg(undefined); try { const url = await controller.archiveUrl(0); if (url) await saveAndShare(url, "ezconverter-batch.zip", "application/zip"); } catch (e) { setMsg((e as Error).message); } finally { setBusy(undefined); } };
  const allFailed = bad.length > 0 && ok.length === 0;
  return (
    <ScrollView contentContainerStyle={{ paddingBottom: 28 }}>
      <Hero compact title={<>{allFailed ? "Something went " : "All done, "}<Serif size={26} color="#ffffff">{allFailed ? "wrong" : "successfully"}</Serif></>}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 14 }}>
          <View style={{ width: 56, height: 56, borderRadius: 28, backgroundColor: "#ffffff", alignItems: "center", justifyContent: "center" }}><Txt size={28} weight="800" color={allFailed ? t.bad : "#0f7a3d"}>{allFailed ? "!" : G.check}</Txt></View>
          <View style={{ flex: 1 }}>
            <Txt size={20} weight="800" color="#ffffff" testID="results-summary">{`${sum.succeeded} converted${sum.failed ? `, ${sum.failed} failed` : ""}${sum.cancelled ? `, ${sum.cancelled} cancelled` : ""}${other.length - sum.cancelled > 0 ? `, ${other.length - sum.cancelled} skipped` : ""}`}</Txt>
            <Txt size={13} color="#ffffff" style={{ opacity: 0.85 }}>Originals were not changed. Results are kept temporarily and deleted automatically.</Txt>
          </View>
        </View>
      </Hero>
      <View style={{ padding: 16, gap: 12 }} testID="results">
        {msg && <Notice tone="bad" title="Couldn't save">{msg}</Notice>}
        {ok.length > 1 && <Btn testID="zip-button" icon={G.up} title={busy === "zip" ? "Preparing…" : "Share all as ZIP"} variant="primary" disabled={busy === "zip"} onPress={() => void zip()} />}
        {ok.map((i) => (
          <Card key={i.key} style={{ gap: 10 }} testID="result-card">
            <View style={{ flexDirection: "row", alignItems: "center", gap: 14 }}>
              <FileTile ext={i.result?.name.split(".").pop()} tone="good" />
              <View style={{ flex: 1 }}>
                <Txt weight="700" testID="result-name">{i.result?.name ?? i.source.name}</Txt>
                <Txt size={12} color={t.muted}>{i.result ? formatBytes(i.result.size) : ""} · from {i.source.name}</Txt>
              </View>
            </View>
            <View style={{ flexDirection: "row", gap: 6, flexWrap: "wrap" }}>
              {i.result && <Badge tone={FIDELITY[i.result.fidelity].tone === "good" ? "good" : FIDELITY[i.result.fidelity].tone === "warn" ? "warn" : "info"}>{FIDELITY[i.result.fidelity].label}</Badge>}
              {i.result?.verification.ok && <Badge tone="good">{`${G.check} Verified`}</Badge>}
            </View>
            {(i.job?.warnings ?? []).slice(0, 3).map((w) => <Txt key={w} size={12} color={t.warn}>{G.warn} {w}</Txt>)}
            <Btn testID="share-button" title={busy === i.key ? "Preparing…" : "Save / Share"} variant="primary" disabled={!i.result || busy === i.key} onPress={() => void share(i.key)} />
          </Card>
        ))}
        {bad.map((i) => (
          <Notice key={i.key} tone="bad" title={`${i.source.name} failed`}>{i.job?.error?.message ?? ""}</Notice>
        ))}
        {bad.some((i) => i.job?.can_retry) && <Btn title="Retry failed" onPress={() => void controller.retryFailed()} />}
        <Btn testID="new-batch" title="Convert more" onPress={() => void controller.clear()} />
        <Btn title="Delete results now" variant="danger" onPress={() => void controller.deleteEverything()} />
      </View>
    </ScrollView>
  );
}
