import type { CapabilityOut } from "@ezconverter/contracts";
import { type BatchController, type BatchItem, type BatchState, FAMILY_LABEL, FIDELITY, STATE_LABEL, type TargetChoice, explainError, formatBytes, formatDuration, itemStatus } from "@ezconverter/shared-domain";
import { Alert, Badge, Button, FileTile, Icon, OptionsForm, ProgressBar, Select, cx } from "@ezconverter/ui";
import { useEffect, useState } from "react";

interface Props {
  item: BatchItem; phase: BatchState["phase"]; controller: BatchController;
  formats: Map<string, { name: string; family: string }>; caps: Map<string, CapabilityOut>;
}

export function ItemRow({ item, phase, controller, formats, caps }: Props) {
  const [targets, setTargets] = useState<TargetChoice[]>([]);
  const [open, setOpen] = useState(false);
  const st = itemStatus(item, phase);
  const fmt = item.file?.format ?? undefined;
  const editable = phase === "idle" && item.intake.state === "ready" && !item.duplicateOf;

  useEffect(() => {
    let live = true;
    if (item.intake.state === "ready" && fmt) void controller.availableTargets(item.key).then((t) => live && setTargets(t));
    return () => { live = false; };
  }, [controller, item.key, item.intake.state, item.policy, fmt]);

  // Option specs come from the capability the plan resolved to (the final step owns the options).
  const finalCap = item.plan?.steps.at(-1)?.capability_id;
  const cap = (finalCap && caps.get(finalCap)) || (fmt && item.target ? [...caps.values()].find((c) => c.source === fmt && c.target === item.target) : undefined);
  const specs = cap?.options ?? [];
  const optErrors = item.planError?.code === "invalid_options" && typeof item.planError.details?.option === "string" ? { [item.planError.details.option as string]: item.planError.message } : undefined;

  const byFamily = new Map<string, TargetChoice[]>();
  for (const t of targets) { const fam = formats.get(t.target)?.family ?? "other"; byFamily.set(fam, [...(byFamily.get(fam) ?? []), t]); }

  const ext = item.source.name.includes(".") ? item.source.name.split(".").pop() : fmt;
  const tone = st === "succeeded" ? "good" : st === "failed" || st === "invalid" || st === "ineligible" ? "bad" : "accent";

  return (
    <li className="ez-rise rounded-card border border-line/70 bg-surface p-4 shadow-card sm:p-5" data-testid="file-row" data-status={st} data-name={item.source.name}>
      <div className="flex items-start gap-3.5">
        <FileTile ext={ext} tone={tone} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <p className="max-w-full truncate font-bold" title={item.source.name}>{item.source.name}</p>
            {item.source.size != null && <span className="text-xs font-medium text-muted">{formatBytes(item.source.size)}</span>}
            {fmt && <Badge tone="info" title={`Detected from the file's content (${item.file?.detection.method})`}>{formats.get(fmt)?.name ?? fmt.toUpperCase()}</Badge>}
            {item.file?.detection.extension_mismatch && <Badge tone="warn" title={(item.file.detection.notes ?? []).join(" ")}>Extension doesn't match content</Badge>}
            {item.duplicateOf && <Badge tone="warn">Duplicate — skipped</Badge>}
          </div>
          <StatusLine item={item} status={st} />
        </div>
        {phase === "idle" && <Button variant="ghost" aria-label={`Remove ${item.source.name}`} onClick={() => controller.remove(item.key)} className="!min-w-11 !px-2"><Icon name="x" /></Button>}
      </div>

      {item.intake.state === "uploading" && <div className="mt-3"><ProgressBar value={item.intake.progress} label={`Reading ${item.source.name}`} /></div>}
      {item.intake.state === "error" && <div className="mt-3"><Alert tone="bad" title={explainError(item.intake.error?.code ?? "").title}>{item.intake.error?.message}</Alert></div>}

      {editable && (
        <div className="mt-4 flex flex-wrap items-end gap-3">
          <div className="flex min-w-0 flex-1 basis-72 items-end gap-2">
            <span className="grid min-h-11 shrink-0 place-items-center rounded-full bg-surface2 px-4 text-sm font-bold text-ink" aria-hidden="true">{(fmt ?? "?").toUpperCase()}</span>
            <span className="mb-2 grid size-7 shrink-0 place-items-center rounded-full bg-accentsoft text-accent" aria-hidden="true"><Icon name="arrow" size={14} /></span>
            <div className="min-w-0 flex-1">
              <Select label="Convert to" data-testid="target-select" value={item.target ?? ""} onChange={(e) => controller.setTarget(item.key, e.target.value || undefined)} className="w-full">
                <option value="">Choose a format…</option>
                {[...byFamily].map(([fam, list]) => (
                  <optgroup key={fam} label={FAMILY_LABEL[fam] ?? fam}>
                    {list.map((t) => <option key={t.target} value={t.target}>{(t.transform ? "Keep " : "") + (formats.get(t.target)?.name ?? t.target.toUpperCase())}{t.steps > 1 ? " (2+ steps)" : ""}{t.remote ? " — remote" : ""}</option>)}
                  </optgroup>
                ))}
              </Select>
            </div>
          </div>
          <div className="flex items-center gap-2 pb-1.5">
            {item.plan && <Badge tone={FIDELITY[item.plan.fidelity].tone} title={FIDELITY[item.plan.fidelity].description}>{FIDELITY[item.plan.fidelity].label}</Badge>}
            {item.planning && <span className="text-xs text-muted" role="status">Checking…</span>}
          </div>
          {item.target && specs.length > 0 && (
            <Button variant="ghost" icon="settings" aria-expanded={open} onClick={() => setOpen(!open)} data-testid="settings-toggle">Settings ({specs.length})</Button>
          )}
        </div>
      )}

      {editable && item.routeNote && (
        <p className={cx("mt-2 flex items-center gap-1.5 text-xs", item.usesRemote ? "text-warn" : "text-muted")} data-testid="route-note">
          <Icon name={item.usesRemote ? "cloud" : "shield"} size={14} />{item.routeNote}
        </p>
      )}
      {open && editable && cap && <div className="mt-3 rounded-2xl bg-surface2 p-4"><OptionsForm specs={specs} values={item.options} errors={optErrors} onChange={(next) => controller.setOptions(item.key, next)} /></div>}

      {editable && item.planError && item.planError.code !== "invalid_options" && (
        <div className="mt-3"><Alert tone="bad" title={explainError(item.planError.code).title}>{item.planError.message}
          {Array.isArray(item.planError.details?.available_targets) && (item.planError.details.available_targets as string[]).length > 0 && <p className="mt-1 text-xs">Available: {(item.planError.details.available_targets as string[]).join(", ")}</p>}</Alert></div>
      )}
      {editable && item.plan && item.plan.warnings.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs text-warn" aria-label="Notes about this conversion">
          {item.plan.warnings.map((w) => <li key={w} className="flex gap-1.5"><Icon name="alert" size={14} className="mt-0.5 shrink-0" />{w}</li>)}
        </ul>
      )}
      {editable && item.plan && item.plan.estimate.seconds > 0 && (
        <p className="mt-2 text-xs text-muted">Estimated {formatDuration(item.plan.estimate.seconds)} · up to ~{item.plan.estimate.memory_mb} MB memory (rough)</p>
      )}

      {item.job && phase !== "idle" && (
        <div className="mt-3 space-y-2">
          {(item.job.state === "RUNNING" || item.job.state === "VERIFYING" || item.job.state === "QUEUED") && (
            <ProgressBar value={item.job.state === "QUEUED" ? 0 : (item.job.progress ?? null)} label={`Converting ${item.source.name}`} />
          )}
          {item.job.state === "FAILED" && item.job.error && (
            <Alert tone="bad" title={explainError(item.job.error.code).title}
                   action={item.job.can_retry ? <Button icon="retry" onClick={() => void controller.retryJob(item.key)}>Retry</Button> : undefined}>
              {item.job.error.message}{explainError(item.job.error.code).action ? ` ${explainError(item.job.error.code).action}` : ""}
            </Alert>
          )}
        </div>
      )}
    </li>
  );
}

function StatusLine({ item, status }: { item: BatchItem; status: string }) {
  const text: Record<string, string> = {
    uploading: "Reading file…", queued: "Waiting…", "needs-target": "Choose what to convert this to", planning: "Checking…", invalid: "Can't convert as requested",
    ready: item.target ? `Ready → ${item.target.toUpperCase()}` : "Ready", waiting: "Waiting its turn",
    converting: item.job ? STATE_LABEL[item.job.state] + (item.job.phase && item.job.state === "RUNNING" ? ` (${item.job.phase})` : "") : "Converting",
    succeeded: "Done", failed: "Failed", cancelled: "Cancelled", skipped: "Skipped", duplicate: "Same file already added", ineligible: "Not convertible",
  };
  const tone = ({ succeeded: "text-good", failed: "text-bad", invalid: "text-bad", ineligible: "text-bad", cancelled: "text-muted" } as Record<string, string>)[status] ?? "text-muted";
  return <p className={cx("mt-0.5 text-sm font-medium", tone)} data-testid="item-status">{text[status] ?? status}</p>;
}
