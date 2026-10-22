import type { BatchController, BatchItem, BatchState, TargetChoice } from "@ezconverter/shared-domain";
import { FAMILY_LABEL, FIDELITY, STATE_LABEL, explainError, formatBytes, formatDuration, itemStatus } from "@ezconverter/shared-domain";
import { useEffect, useState } from "react";
import { Pressable, View } from "react-native";
import { useApp } from "../state";
import { Badge, Btn, Card, ChoiceSheet, FileTile, G, MIN_TOUCH, Notice, OptionsForm, Progress, Txt, useT } from "./ui";

export function ItemCard({ item, phase, controller }: { item: BatchItem; phase: BatchState["phase"]; controller: BatchController }) {
  const t = useT();
  const { catalog } = useApp();
  const [targets, setTargets] = useState<TargetChoice[]>([]);
  const [picker, setPicker] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const st = itemStatus(item, phase);
  const fmt = item.file?.format ?? undefined;
  const editable = phase === "idle" && item.intake.state === "ready" && !item.duplicateOf;

  useEffect(() => {
    let live = true;
    if (item.intake.state === "ready" && fmt) void controller.availableTargets(item.key).then((x) => live && setTargets(x));
    return () => { live = false; };
  }, [controller, item.key, item.intake.state, item.policy, fmt]);

  const finalCap = item.plan?.steps.at(-1)?.capability_id;
  const cap = (finalCap && catalog.caps.get(finalCap)) || (fmt && item.target ? [...catalog.caps.values()].find((c) => c.source === fmt && c.target === item.target) : undefined);
  const specs = cap?.options ?? [];
  const name = (id: string) => catalog.formats.get(id)?.name ?? id.toUpperCase();
  const statusText: Record<string, string> = {
    uploading: "Reading file…", queued: "Waiting…", "needs-target": "Choose a format to convert to", planning: "Checking…", invalid: "Can't convert as requested",
    ready: item.target ? `Ready → ${item.target.toUpperCase()}` : "Ready", waiting: "Waiting its turn",
    converting: item.job ? STATE_LABEL[item.job.state] : "Converting", succeeded: "Done", failed: "Failed", cancelled: "Cancelled", skipped: "Skipped", duplicate: "Same file already added", ineligible: "Not convertible",
  };
  const statusColor = st === "succeeded" ? t.good : st === "failed" || st === "invalid" || st === "ineligible" ? t.bad : t.muted;
  const ext = item.source.name.includes(".") ? item.source.name.split(".").pop() : fmt;
  const tone = st === "succeeded" ? "good" : st === "failed" || st === "invalid" || st === "ineligible" ? "bad" : "accent";
  const fidTone = (x: string) => (x === "good" ? "good" : x === "warn" ? "warn" : "info");

  return (
    <Card style={{ gap: 12 }}>
      <View testID="file-card" accessibilityLabel={`${item.source.name}, ${statusText[st] ?? st}`} style={{ flexDirection: "row", alignItems: "flex-start", gap: 14 }}>
        <FileTile ext={ext} tone={tone} />
        <View style={{ flex: 1, gap: 4 }}>
          <Txt weight="700" numberOfLines={2}>{item.source.name}</Txt>
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
            {item.source.size != null && <Txt size={12} weight="500" color={t.muted}>{formatBytes(item.source.size)}</Txt>}
            {fmt && <Badge tone="info">{name(fmt)}</Badge>}
            {item.file?.detection.extension_mismatch && <Badge tone="warn">Extension ≠ content</Badge>}
            {item.duplicateOf && <Badge tone="warn">Duplicate</Badge>}
          </View>
          <Txt size={14} weight="500" color={statusColor} testID="item-status">{statusText[st] ?? st}</Txt>
        </View>
        {phase === "idle" && (
          <Pressable accessibilityRole="button" accessibilityLabel={`Remove ${item.source.name}`} onPress={() => controller.remove(item.key)}
                     style={{ width: MIN_TOUCH - 8, height: MIN_TOUCH - 8, borderRadius: 99, alignItems: "center", justifyContent: "center", backgroundColor: t.surface2 }}>
            <Txt weight="700" color={t.muted}>{G.close}</Txt>
          </Pressable>
        )}
      </View>

      {item.intake.state === "uploading" && <Progress value={item.intake.progress} label={`Reading ${item.source.name}`} />}
      {item.intake.state === "error" && <Notice tone="bad" title={explainError(item.intake.error?.code ?? "").title}>{item.intake.error?.message}</Notice>}

      {editable && (
        <View style={{ gap: 10 }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <View style={{ minHeight: MIN_TOUCH, paddingHorizontal: 16, borderRadius: 99, backgroundColor: t.surface2, alignItems: "center", justifyContent: "center" }} accessible={false} importantForAccessibility="no-hide-descendants">
              <Txt weight="800">{(fmt ?? "?").toUpperCase()}</Txt>
            </View>
            <Txt weight="800" color={t.accent}>→</Txt>
            <View style={{ flex: 1 }}><Btn testID="target-button" title={item.target ? `Convert to: ${name(item.target)}` : "Choose format…"} onPress={() => setPicker(true)} /></View>
          </View>
          {(item.plan || item.planning) && <View style={{ flexDirection: "row", gap: 8, alignItems: "center" }}>{item.plan && <Badge tone={fidTone(FIDELITY[item.plan.fidelity].tone)}>{FIDELITY[item.plan.fidelity].label}</Badge>}{item.planning && <Txt size={12} color={t.muted}>Checking…</Txt>}</View>}
          {item.routeNote && <Txt size={12} color={item.usesRemote ? t.warn : t.muted} testID="route-note">{item.routeNote}</Txt>}
          {item.target && specs.length > 0 && <Btn testID="settings-button" icon={G.gear} title={settingsOpen ? "Hide settings" : `Settings (${specs.length})`} variant="ghost" onPress={() => setSettingsOpen(!settingsOpen)} />}
          {settingsOpen && cap && <OptionsForm specs={specs} values={item.options} onChange={(n) => controller.setOptions(item.key, n)} />}
          {item.planError && <Notice tone="bad" title={explainError(item.planError.code).title}>{item.planError.message}</Notice>}
          {item.plan?.warnings.map((w) => <Txt key={w} size={12} color={t.warn}>{G.warn} {w}</Txt>)}
          {item.plan && item.plan.estimate.seconds > 0 && <Txt size={12} color={t.muted}>Estimated {formatDuration(item.plan.estimate.seconds)} (rough)</Txt>}
          <ChoiceSheet visible={picker} title="Convert to" selected={item.target} onClose={() => setPicker(false)} onSelect={(v) => controller.setTarget(item.key, v)}
                       choices={targets.map((x) => ({ value: x.target, label: `${x.transform ? "Keep " : ""}${name(x.target)}${x.steps > 1 ? " (multi-step)" : ""}`, hint: FIDELITY[x.fidelity].label, group: FAMILY_LABEL[catalog.formats.get(x.target)?.family ?? ""] ?? "Other" }))} />
        </View>
      )}

      {item.job && phase !== "idle" && (
        <View style={{ gap: 8 }}>
          {(item.job.state === "RUNNING" || item.job.state === "VERIFYING" || item.job.state === "QUEUED") && <Progress value={item.job.state === "QUEUED" ? 0 : (item.job.progress ?? null)} label={`Converting ${item.source.name}`} />}
          {item.job.state === "FAILED" && item.job.error && (
            <>
              <Notice tone="bad" title={explainError(item.job.error.code).title}>{`${item.job.error.message} ${explainError(item.job.error.code).action}`}</Notice>
              {item.job.can_retry && <Btn title="Retry" onPress={() => void controller.retryJob(item.key)} />}
            </>
          )}
        </View>
      )}
    </Card>
  );
}
