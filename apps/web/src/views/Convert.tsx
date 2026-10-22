import { type BatchItem, BUILT_IN_PRESETS, FAMILY_LABEL, FIDELITY, commonTargets, explainError, formatBytes, groupByConversion, summarize } from "@ezconverter/shared-domain";
import { Alert, Badge, Button, Card, Dropzone, FileTile, Icon, ProgressBar, ProgressRing, Select, cx } from "@ezconverter/ui";
import { useEffect, useMemo, useState } from "react";
import { ItemRow } from "../components/ItemRow";
import { loadHistory, useApp } from "../state";

async function saveFromUrl(url: string, name: string) {
  const a = document.createElement("a");
  a.href = url; a.download = name; a.rel = "noopener";
  document.body.appendChild(a); a.click(); a.remove();
}

const toSource = (f: File) => ({ name: f.name, size: f.size, type: f.type, blob: f });

export function Convert({ goSettings }: { goSettings: () => void }) {
  const { state, controller, catalog, settings, setSettings } = useApp();
  const sum = summarize(state);
  const [grouped, setGrouped] = useState(false);
  const [bulkTargets, setBulkTargets] = useState<string[]>([]);
  const ready = state.items.filter((i) => i.intake.state === "ready" && !i.duplicateOf);

  // Targets every ready, unique file can reach (so "convert all to" never offers an impossible choice).
  const readyKey = ready.map((i) => `${i.key}:${i.policy}`).join(",");
  useEffect(() => {
    let live = true;
    void Promise.all(ready.map((i) => controller.availableTargets(i.key))).then((all) => { if (live) setBulkTargets(commonTargets(all.map((l) => l.map((t) => t.target)))); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [controller, readyKey]);

  if (state.items.length === 0) return <Home onFiles={(f) => void controller.addFiles(f.map(toSource))} goSettings={goSettings} />;
  if (state.phase === "finished") return <Results />;

  const running = state.phase === "running";
  const families = new Set(ready.map((i) => catalog.formats.get(i.file?.format ?? "")?.family).filter(Boolean) as string[]);
  const presets = BUILT_IN_PRESETS.filter((p) => families.has(p.family));
  const groups = groupByConversion(state.items);
  const rows = (items: BatchItem[]) => items.map((i) => <ItemRow key={i.key} item={i} phase={state.phase} controller={controller} formats={catalog.formats} caps={catalog.caps} />);

  return (
    <div className="space-y-4">
      <Card className="flex flex-wrap items-end gap-3 p-4 sm:p-5">
        <Select label="Processing" value={state.defaultPolicy} disabled={running} onChange={(e) => controller.setPolicy("all", e.target.value as typeof state.defaultPolicy)} data-testid="policy-select">
          <option value="AUTO">Automatic (device first)</option>
          <option value="LOCAL_ONLY">Local only — never upload</option>
          <option value="REMOTE_ALLOWED">Allow remote converters</option>
        </Select>
        {!running && ready.length > 1 && (
          <Select label="Convert all to" value="" onChange={(e) => e.target.value && controller.setAllTarget(e.target.value)} data-testid="bulk-target">
            <option value="">{bulkTargets.length ? "Choose a format…" : "No format fits every file"}</option>
            {bulkTargets.map((t) => <option key={t} value={t}>{catalog.formats.get(t)?.name ?? t.toUpperCase()}</option>)}
          </Select>
        )}
        {!running && presets.length > 0 && (
          <Select label="Preset" value="" onChange={(e) => { const p = presets.find((x) => x.id === e.target.value); if (p) controller.applyPreset(p, new Map([...catalog.formats].map(([k, v]) => [k, v.family]))); }}>
            <option value="">Apply a preset…</option>
            {presets.map((p) => <option key={p.id} value={p.id}>{p.name} — {p.description}</option>)}
          </Select>
        )}
        <label className="ml-auto flex min-h-11 items-center gap-2 text-sm font-medium"><input type="checkbox" className="size-5 accent-[var(--accent)]" checked={grouped} onChange={(e) => setGrouped(e.target.checked)} />Group by conversion</label>
      </Card>

      {state.defaultPolicy === "AUTO" && !settings.allowRemote && state.items.some((i) => i.planError?.code === "remote_consent_required") && (
        <Alert tone="warn" title="Some files need a remote converter"
               action={<Button onClick={() => { setSettings({ allowRemote: true }); controller.setPolicy("all", "AUTO"); }}>Allow remote processing</Button>}>
          Those files can't be converted on this device. Nothing has been uploaded.
        </Alert>
      )}

      {running && (
        <Card className="flex flex-wrap items-center gap-5 p-5" data-testid="overall-progress">
          <ProgressRing value={sum.indeterminate && sum.progress === 0 ? null : sum.progress} label="Overall progress" />
          <div className="min-w-0 flex-1 basis-48 space-y-2">
            <p className="text-lg font-bold">Converting {sum.running} of {state.items.filter((i) => i.job).length}…</p>
            <p className="text-sm text-muted">{sum.indeterminate ? "In progress" : `${Math.round(sum.progress * 100)}%`} · each file is checked before it is handed back.</p>
            <ProgressBar value={sum.indeterminate && sum.progress === 0 ? null : sum.progress} label="Overall progress" />
          </div>
          <Button variant="danger" icon="x" onClick={() => void controller.cancel()}>Cancel remaining</Button>
        </Card>
      )}

      {grouped && !running ? groups.map((g) => (
        <section key={g.key} aria-label={`${g.source} to ${g.target}`} className="space-y-2">
          <h2 className="text-sm font-bold text-muted">{g.source.toUpperCase()} → {g.target.toUpperCase()} · {g.items.length} file{g.items.length > 1 ? "s" : ""}</h2>
          <ul className="space-y-3">{rows(g.items)}</ul>
        </section>
      )) : (
        <ul className="space-y-3" aria-label="Files">{rows(state.items)}</ul>
      )}

      {!running && (
        <>
          <Dropzone compact onFiles={(f) => void controller.addFiles(f.map(toSource))} />
          <div className="sticky bottom-24 z-10 flex flex-wrap items-center gap-3 rounded-full border border-line/70 bg-surface/95 py-2.5 pl-6 pr-2.5 shadow-card backdrop-blur lg:bottom-4">
            <p className="min-w-40 flex-1 text-sm font-medium text-muted" data-testid="start-summary">
              {sum.uploading > 0 ? `Reading ${sum.uploading} file${sum.uploading > 1 ? "s" : ""}…` : `${sum.startable} of ${state.items.length} ready to convert${sum.skipped ? ` · ${sum.skipped} will be skipped` : ""}`}
            </p>
            <Button variant="ghost" onClick={() => void controller.clear()}>Clear all</Button>
            <Button variant="primary" icon="arrow" disabled={sum.startable === 0 || sum.uploading > 0} onClick={() => void controller.start()} data-testid="start-button">
              Convert {sum.startable || ""} file{sum.startable === 1 ? "" : "s"}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

function Home({ onFiles, goSettings }: { onFiles: (f: File[]) => void; goSettings: () => void }) {
  const { catalog, settings } = useApp();
  const [recent, setRecent] = useState<string[]>([]);
  useEffect(() => { void loadHistory().then((h) => setRecent([...new Set(h.filter((e) => e.state === "SUCCEEDED").map((e) => e.target))].slice(0, 6))); }, []);
  const byFamily = useMemo(() => {
    const sources = new Map<string, Set<string>>();
    for (const c of catalog.caps.values()) { const fam = catalog.formats.get(c.source)?.family; if (fam) sources.set(fam, (sources.get(fam) ?? new Set()).add(c.source)); }
    return sources;
  }, [catalog]);
  const noConverter = catalog.statuses.length > 0 && catalog.statuses.every((s) => s.state !== "online") && !settings.allowRemote;
  return (
    <div className="space-y-6">
      <section className="ez-hero rounded-[2rem] bg-[image:var(--hero)] px-6 pb-16 pt-9 text-white shadow-glow sm:px-10 sm:pb-20 sm:pt-12">
        <p className="inline-flex items-center gap-2 rounded-full bg-white/15 px-3 py-1 text-xs font-semibold"><Icon name="sparkle" size={14} />Private by default · verified output</p>
        <h1 className="mt-4 max-w-2xl text-4xl font-extrabold leading-[1.05] tracking-tight sm:text-5xl">Convert anything, <span className="ez-serif">simply.</span></h1>
        <p className="mt-3 max-w-xl text-base text-white/85">Drop one file or fifty. EZConverter identifies each one by its content, offers only conversions it can really do, and tells you what will change.</p>
      </section>
      <div className="relative z-10 -mt-20 px-3 sm:-mt-24 sm:px-8"><Dropzone onFiles={onFiles} disabled={noConverter} /></div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Card className="p-5" data-testid="runtime-status">
          <h2 className="mb-3 font-bold">Converters</h2>
          {catalog.statuses.length === 0 && <p className="text-sm text-muted">None configured. <button className="font-semibold text-accent underline" onClick={goSettings}>Add one</button></p>}
          <ul className="space-y-2.5 text-sm">
            {catalog.statuses.map((s) => (
              <li key={s.id} className="flex items-center gap-3">
                <span className="grid size-9 place-items-center rounded-full bg-accentsoft text-accent"><Icon name={s.location === "local" ? "device" : "cloud"} size={16} /></span>
                <span className="flex-1 font-medium">{s.label}</span>
                {s.state === "online" ? <Badge tone="good">{s.caps?.capabilities.length ?? 0} conversions</Badge> : <Badge tone={s.state === "unauthorized" ? "warn" : "bad"} title={s.error}>{s.state === "unauthorized" ? "Needs token" : "Offline"}</Badge>}
              </li>
            ))}
          </ul>
        </Card>
        <Card className="p-5">
          <h2 className="mb-3 font-bold">Recently used formats</h2>
          {recent.length ? <div className="flex flex-wrap gap-2">{recent.map((t) => <Badge key={t} tone="info">{catalog.formats.get(t)?.name ?? t.toUpperCase()}</Badge>)}</div> : <p className="text-sm text-muted">Your finished conversions will appear here.</p>}
        </Card>
      </div>
      {byFamily.size > 0 && (
        <Card className="p-5">
          <h2 className="mb-4 font-bold">What it can convert here</h2>
          <dl className="grid gap-x-8 gap-y-4 text-sm sm:grid-cols-2">
            {[...byFamily].map(([fam, set]) => (
              <div key={fam}>
                <dt className="mb-1.5 text-xs font-bold uppercase tracking-wide text-muted">{FAMILY_LABEL[fam] ?? fam}</dt>
                <dd className="flex flex-wrap gap-1.5">{[...set].sort().map((x) => <span key={x} className="rounded-full bg-surface2 px-2.5 py-1 text-xs font-semibold">{x.toUpperCase()}</span>)}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-4 text-xs text-muted">Only conversions that passed the automated engine tests are listed.</p>
        </Card>
      )}
    </div>
  );
}

function Results() {
  const { state, controller } = useApp();
  const sum = summarize(state);
  const [busy, setBusy] = useState<string>();
  const [zipError, setZipError] = useState<string>();
  const items = state.items;
  const ok = items.filter((i) => i.job?.state === "SUCCEEDED");
  const bad = items.filter((i) => i.job?.state === "FAILED");
  const cancelled = items.filter((i) => i.job?.state === "CANCELLED");
  const skipped = items.filter((i) => !i.job);
  const retryable = bad.some((i) => i.job?.can_retry);

  const dl = async (i: BatchItem) => { setBusy(i.key); try { const url = await controller.downloadUrl(i.key); if (url) await saveFromUrl(url, i.result?.name ?? "download"); } finally { setBusy(undefined); } };
  const zip = async (idx: number) => { setBusy(`zip${idx}`); setZipError(undefined); try { const url = await controller.archiveUrl(idx); if (url) await saveFromUrl(url, "ezconverter-batch.zip"); } catch (e) { setZipError((e as Error).message); } finally { setBusy(undefined); } };

  const allFailed = bad.length > 0 && ok.length === 0;
  return (
    <div className="space-y-5" data-testid="results">
      <Card className="flex flex-wrap items-center gap-4 p-5 sm:p-6">
        <span className={cx("grid size-16 shrink-0 place-items-center rounded-full ring-8", allFailed ? "bg-badsoft text-bad ring-badsoft/50" : "bg-goodsoft text-good ring-goodsoft/50")}>
          <Icon name={allFailed ? "alert" : "check"} size={30} />
        </span>
        <div className="min-w-0 flex-1 basis-56">
          <h1 className="text-2xl font-extrabold tracking-tight" data-testid="results-summary">{sum.succeeded} converted{sum.failed ? `, ${sum.failed} failed` : ""}{sum.cancelled ? `, ${sum.cancelled} cancelled` : ""}{skipped.length ? `, ${skipped.length} skipped` : ""}</h1>
          <p className="text-sm text-muted">Originals were not changed. Results are kept temporarily and deleted automatically.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {ok.length > 0 && state.runs.map((r, idx) => <Button key={r.batchId} variant="primary" icon="download" disabled={busy === `zip${idx}`} onClick={() => void zip(idx)} data-testid="zip-button">{state.runs.length > 1 ? `ZIP (${idx + 1})` : "Download all as ZIP"}</Button>)}
          {retryable && <Button icon="retry" onClick={() => void controller.retryFailed()}>Retry failed</Button>}
          <Button onClick={() => void controller.clear()} data-testid="new-batch">Convert more</Button>
        </div>
      </Card>
      {zipError && <Alert tone="bad" title="Couldn't prepare the ZIP">{zipError}</Alert>}

      {ok.length > 0 && (
        <section aria-label="Converted files" className="space-y-2.5">
          <h2 className="text-sm font-bold text-muted">Converted</h2>
          <ul className="space-y-3">
            {ok.map((i) => (
              <li key={i.key} className="ez-rise rounded-card border border-line/70 bg-surface p-4 shadow-card" data-testid="result-row" data-name={i.source.name}>
                <div className="flex flex-wrap items-center gap-3.5">
                  <FileTile ext={i.result?.name.split(".").pop()} tone="good" />
                  <div className="min-w-0 flex-1 basis-48">
                    <p className="truncate font-bold" data-testid="result-name">{i.result?.name ?? i.source.name}</p>
                    <p className="text-xs text-muted">{i.result ? formatBytes(i.result.size) : ""} · from {i.source.name}</p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    {i.result && <Badge tone={FIDELITY[i.result.fidelity].tone} title={FIDELITY[i.result.fidelity].description}>{FIDELITY[i.result.fidelity].label}</Badge>}
                    {i.result?.verification.ok && <Badge tone="good" title={i.result.verification.checks.map((c) => `${c.passed ? "✓" : "✗"} ${c.name}: ${c.detail}`).join("\n")}><Icon name="check" size={12} />Verified</Badge>}
                  </div>
                  <Button variant="primary" icon="download" disabled={!i.result || busy === i.key} onClick={() => void dl(i)} data-testid="download-button">Download</Button>
                </div>
                {i.job && (i.job.warnings ?? []).length > 0 && <ul className="mt-3 space-y-1 text-xs text-warn">{(i.job.warnings ?? []).slice(0, 4).map((w) => <li key={w}>• {w}</li>)}</ul>}
              </li>
            ))}
          </ul>
        </section>
      )}
      {bad.length > 0 && (
        <section aria-label="Failed files" className="space-y-2.5">
          <h2 className="text-sm font-bold text-muted">Failed</h2>
          <ul className="space-y-2">
            {bad.map((i) => (
              <li key={i.key} data-testid="failed-row" data-name={i.source.name}>
                <Alert tone="bad" title={`${i.source.name}: ${explainError(i.job!.error?.code ?? "").title}`}
                       action={i.job!.can_retry ? <Button icon="retry" onClick={() => void controller.retryJob(i.key)}>Retry</Button> : undefined}>
                  {i.job!.error?.message} {explainError(i.job!.error?.code ?? "").action}
                </Alert>
              </li>
            ))}
          </ul>
        </section>
      )}
      {(cancelled.length > 0 || skipped.length > 0) && (
        <section aria-label="Not converted" className="space-y-1 text-sm text-muted">
          <h2 className="text-sm font-bold">Not converted</h2>
          <ul>{[...cancelled, ...skipped].map((i) => <li key={i.key}>• {i.source.name} — {i.job ? "cancelled" : i.duplicateOf ? "duplicate" : i.intake.state === "error" ? "not convertible" : "no target chosen"}</li>)}</ul>
        </section>
      )}
      <div className="flex justify-end"><Button variant="danger" icon="trash" onClick={() => void controller.deleteEverything()} data-testid="delete-now">Delete results now</Button></div>
    </div>
  );
}
