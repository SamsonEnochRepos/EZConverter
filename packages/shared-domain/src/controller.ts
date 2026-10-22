import type { UploadSource } from "@ezconverter/api-client";
import { EZApiError } from "@ezconverter/api-client";
import type { BatchOut, ErrorBody, ExecutionPolicy, FileOut, JobResultOut, TargetOut } from "@ezconverter/contracts";
import { isTerminal } from "@ezconverter/contracts";
import { type Action, type BatchItem, type BatchRun, type BatchState, initialState, isStartable, reducer } from "./batch";
import { CapabilityProbe, type RouterSettings, type Runtime, routeConversion } from "./router";
import { type KeyValueStore, getJSON, setJSON } from "./storage";

export interface ControllerDeps {
  runtimes: () => Runtime[];
  settings: () => RouterSettings & { defaultPolicy: ExecutionPolicy };
  store?: KeyValueStore;
  uploadConcurrency?: number;
  planDebounceMs?: number;
  poll?: { minMs: number; maxMs: number };
  newKey?: () => string;
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
}

export interface TargetChoice { target: string; steps: number; fidelity: TargetOut["fidelity"]; runtimeId: string; remote: boolean; transform: boolean }

const RUNS_KEY = "ez.activeRuns";
const defaultSleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((res) => { const t = setTimeout(res, ms); signal?.addEventListener("abort", () => { clearTimeout(t); res(); }, { once: true }); });
const toBody = (e: unknown): ErrorBody =>
  e instanceof EZApiError ? { code: e.code, message: e.message, details: e.details } : { code: "internal_error", message: (e as Error).message ?? "Unexpected error", details: {} };

export class BatchController {
  private state: BatchState;
  private listeners = new Set<() => void>();
  private abort = new AbortController();
  private uploadAborts = new Map<string, AbortController>();
  private fileIds = new Map<string, Map<string, string>>();      // item key -> runtime id -> server file id
  private planSeq = new Map<string, number>();
  private probe = new CapabilityProbe();
  private queue: string[] = [];
  private activeUploads = 0;
  private keyCounter = 0;
  private polling = new Set<string>();
  private idle: Array<() => void> = [];
  private planTimers = new Map<string, ReturnType<typeof setTimeout>>();
  private starting?: Promise<void>;

  constructor(private deps: ControllerDeps) {
    this.state = initialState(deps.settings().defaultPolicy);
  }

  // ------------------------------------------------------------------ store plumbing

  getState = (): BatchState => this.state;
  subscribe = (fn: () => void): (() => void) => { this.listeners.add(fn); return () => this.listeners.delete(fn); };
  private dispatch(a: Action): void { this.state = reducer(this.state, a); this.listeners.forEach((l) => l()); }
  private rt(id: string): Runtime | undefined { return this.deps.runtimes().find((r) => r.config.id === id); }
  private key(): string { return this.deps.newKey ? this.deps.newKey() : `i${Date.now().toString(36)}${(this.keyCounter++).toString(36)}`; }
  private item(key: string): BatchItem | undefined { return this.state.items.find((i) => i.key === key); }
  /** Resolves when no upload/plan/poll work is outstanding (used by tests and by "wait until ready" UIs). */
  async settled(): Promise<void> {
    while (this.activeUploads || this.queue.length || this.polling.size || this.planTimers.size || [...this.planSeq.values()].some((v) => v < 0)) {
      await new Promise<void>((r) => { this.idle.push(r); setTimeout(r, 50); });
    }
  }
  private pulse(): void { const w = this.idle; this.idle = []; w.forEach((r) => r()); }

  // ------------------------------------------------------------------ intake

  async addFiles(sources: UploadSource[]): Promise<string[]> {
    if (this.state.phase === "finished") this.dispatch({ type: "reset-run" });
    const policy = this.state.defaultPolicy;
    const items: BatchItem[] = sources.map((source) => ({
      key: this.key(), source, intake: { state: "queued", progress: 0 }, options: {}, policy, planning: false,
    }));
    this.dispatch({ type: "add", items });
    for (const i of items) { this.queue.push(i.key); }
    this.pumpUploads();
    return items.map((i) => i.key);
  }

  /**
   * Where a newly added file is read and identified. Local first. A remote runtime is used only with the user's explicit
   * consent: REMOTE_ALLOWED is consent by itself, AUTO needs the standing `allowRemote` setting, LOCAL_ONLY never uploads.
   */
  private intakeRuntime(policy: ExecutionPolicy): Runtime | undefined {
    const rts = this.deps.runtimes();
    const local = rts.find((r) => r.config.location === "local");
    if (local) return local;
    const mayUpload = policy === "REMOTE_ALLOWED" || (policy === "AUTO" && this.deps.settings().allowRemote);
    return mayUpload ? rts.find((r) => r.config.location === "remote") : undefined;
  }

  private pumpUploads(): void {
    const limit = this.deps.uploadConcurrency ?? 3;
    while (this.activeUploads < limit && this.queue.length) {
      const key = this.queue.shift()!;
      const it = this.item(key);
      if (!it) continue;
      this.activeUploads++;
      void this.intake(it).finally(() => { this.activeUploads--; this.pumpUploads(); this.pulse(); });
    }
  }

  private async intake(it: BatchItem): Promise<void> {
    const rt = this.intakeRuntime(it.policy);
    if (!rt) {
      const localOnly = it.policy === "LOCAL_ONLY";
      this.dispatch({ type: "intake", key: it.key, patch: { state: "error", error: {
        code: localOnly ? "local_only_violation" : "no_runtime", details: {},
        message: localOnly
          ? "Processing is set to “Local only” and no local converter is available, so this file was not uploaded anywhere. Start the local converter, or switch to “Allow remote”."
          : "No local converter is reachable and remote processing is off, so this file was not uploaded anywhere. Start the local converter or allow remote processing in Settings." } } });
      return;
    }
    const ac = new AbortController();
    this.uploadAborts.set(it.key, ac);
    this.dispatch({ type: "intake", key: it.key, patch: { state: "uploading", progress: 0, runtimeId: rt.config.id } });
    try {
      const file = await rt.client.upload(it.source, {
        policy: rt.config.location === "local" ? "LOCAL_ONLY" : "REMOTE_ALLOWED", signal: ac.signal,
        onProgress: (f) => this.dispatch({ type: "intake", key: it.key, patch: { progress: f } }),
      });
      if (!this.item(it.key)) { void rt.client.deleteFile(file.id).catch(() => undefined); return; }   // removed while uploading
      this.rememberFile(it.key, rt.config.id, file.id);
      this.dispatch({ type: "identified", key: it.key, file, runtimeId: rt.config.id });
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      this.dispatch({ type: "intake", key: it.key, patch: { state: "error", error: toBody(e) } });
    } finally { this.uploadAborts.delete(it.key); }
  }

  private rememberFile(key: string, runtimeId: string, fileId: string): void {
    const m = this.fileIds.get(key) ?? new Map();
    m.set(runtimeId, fileId);
    this.fileIds.set(key, m);
  }

  remove(key: string): void {
    this.uploadAborts.get(key)?.abort();
    const ids = this.fileIds.get(key);
    if (ids) for (const [rid, fid] of ids) void this.rt(rid)?.client.deleteFile(fid).catch(() => undefined);
    this.fileIds.delete(key);
    this.queue = this.queue.filter((k) => k !== key);
    clearTimeout(this.planTimers.get(key));
    this.planTimers.delete(key);
    this.dispatch({ type: "remove", key });
  }

  async clear(): Promise<void> {
    for (const i of [...this.state.items]) this.remove(i.key);
    this.abort.abort(); this.abort = new AbortController();
    await this.persistRuns([]);
    this.dispatch({ type: "clear" });
  }

  // ------------------------------------------------------------------ choosing targets and planning

  /** Formats this file can become, from every converter the current policy allows. */
  async availableTargets(key: string): Promise<TargetChoice[]> {
    const it = this.item(key);
    if (!it?.file?.format) return [];
    const out = new Map<string, TargetChoice>();
    const policy = it.policy;
    for (const rt of this.deps.runtimes()) {
      if (rt.config.location === "remote" && (policy === "LOCAL_ONLY")) continue;
      try {
        for (const t of (await rt.client.targets(it.file.format)).targets) {
          const prior = out.get(t.target);
          if (!prior || (prior.remote && rt.config.location === "local"))
            out.set(t.target, { target: t.target, steps: t.steps, fidelity: t.fidelity, runtimeId: rt.config.id, remote: rt.config.location === "remote", transform: !!t.transform });
        }
      } catch { /* unreachable runtime: simply contributes nothing */ }
    }
    return [...out.values()].sort((a, b) => a.target.localeCompare(b.target));
  }

  /** Discards any plan request still in flight for this item, so its late answer cannot overwrite newer settings. */
  private invalidatePlan(key: string): void {
    const cur = this.planSeq.get(key);
    if (cur !== undefined && cur < 0) this.planSeq.set(key, -cur);   // no longer in flight as far as settled() is concerned
  }

  setTarget(key: string, target: string | undefined): void { this.invalidatePlan(key); this.dispatch({ type: "set-target", key, target }); if (target) void this.plan(key); }
  setAllTarget(target: string, keys?: string[]): void {
    this.dispatch({ type: "set-all-target", target, keys });
    for (const i of this.state.items) if (i.target === target && (!keys || keys.includes(i.key))) void this.plan(i.key);
  }
  /**
   * Edits invalidate the plan immediately, so a stale plan is never startable; only the network call is debounced.
   * Without this, clicking "Convert" within the debounce window would run with the previous settings.
   */
  setOptions(key: string, options: Record<string, unknown>): void {
    this.invalidatePlan(key);   // a plan request already in flight must not land during the debounce window
    this.dispatch({ type: "set-options", key, options });
    clearTimeout(this.planTimers.get(key));
    this.planTimers.set(key, setTimeout(() => { this.planTimers.delete(key); void this.plan(key).finally(() => this.pulse()); }, this.deps.planDebounceMs ?? 250));
  }
  /** Policy for files added from now on (the saved "default processing mode"). Existing items keep theirs. */
  setDefaultPolicy(policy: ExecutionPolicy): void { this.dispatch({ type: "set-default-policy", policy }); }
  setPolicy(key: string | "all", policy: ExecutionPolicy): void {
    this.dispatch({ type: "set-policy", key, policy });
    this.probe.invalidate();
    for (const i of this.state.items) if ((key === "all" || i.key === key) && i.target) void this.plan(i.key);
  }
  applyPreset(preset: { target: string; options: Record<string, unknown>; family: string }, formats: Map<string, string>, keys?: string[]): void {
    for (const i of this.state.items) {
      if (keys && !keys.includes(i.key)) continue;
      if (i.intake.state !== "ready" || i.duplicateOf || formats.get(i.file?.format ?? "") !== preset.family) continue;
      this.dispatch({ type: "set-target", key: i.key, target: preset.target });
      this.dispatch({ type: "set-options", key: i.key, options: preset.options });
      void this.plan(i.key);
    }
  }

  async plan(key: string): Promise<void> {
    const it = this.item(key);
    if (!it?.file?.format || !it.target) return;
    const seq = Math.abs(this.planSeq.get(key) ?? 0) + 1;   // abs: an earlier request may still be marked in flight (negative)
    this.planSeq.set(key, -seq);              // negative = in flight (see settled())
    this.dispatch({ type: "planning", key });
    const stale = () => this.planSeq.get(key) !== -seq;
    try {
      const settings = this.deps.settings();
      const route = await routeConversion(it.policy, this.deps.runtimes(), { source: it.file.format, target: it.target }, settings, this.probe);
      if (stale()) return;
      if (!route.ok) {
        this.dispatch({ type: "planned", key, error: { code: route.code, message: route.message, details: {} } });
        return;
      }
      const meta = { runtimeId: route.runtime.config.id, usesRemote: route.usesRemote, note: route.explanation };
      const fileId = this.fileIds.get(key)?.get(route.runtime.config.id);
      if (!fileId) {   // the file lives on another runtime: no upload happens before the user starts the batch
        this.dispatch({ type: "planned", key, route: meta, plan: previewPlan(it, route.target) });
        return;
      }
      const plan = await route.runtime.client.plan({ file_id: fileId, target: it.target, options: it.options, execution_policy: route.policy });
      if (stale()) return;
      this.dispatch({ type: "planned", key, plan, route: meta });
    } catch (e) {
      if (!stale()) this.dispatch({ type: "planned", key, error: toBody(e) });
    } finally {
      if (this.planSeq.get(key) === -seq) this.planSeq.set(key, seq);
      this.pulse();
    }
  }

  // ------------------------------------------------------------------ running

  /** Concurrent calls (a double click before 'run-started') share one submission instead of converting every file twice. */
  start(): Promise<void> {
    if (this.starting) return this.starting;
    const p = this.submit().finally(() => { if (this.starting === p) this.starting = undefined; });
    this.starting = p;
    return p;
  }

  private async submit(): Promise<void> {
    const items = this.state.items.filter(isStartable);
    if (!items.length) return;
    const groups = new Map<string, BatchItem[]>();
    for (const i of items) groups.set(i.runtimeId!, [...(groups.get(i.runtimeId!) ?? []), i]);
    const runs: BatchRun[] = [];
    for (const [runtimeId, group] of groups) {
      const rt = this.rt(runtimeId);
      if (!rt) continue;
      const entries: { item: BatchItem; fileId: string }[] = [];
      for (const it of group) {
        let fid = this.fileIds.get(it.key)?.get(runtimeId);
        if (!fid) {
          try {   // explicit, consented upload to the chosen remote converter
            const f: FileOut = await rt.client.upload(it.source, { policy: "REMOTE_ALLOWED" });
            this.rememberFile(it.key, runtimeId, f.id);
            fid = f.id;
          } catch (e) { this.dispatch({ type: "planned", key: it.key, error: toBody(e), route: { runtimeId, usesRemote: true, note: "" } }); continue; }
        }
        entries.push({ item: it, fileId: fid });
      }
      if (!entries.length) continue;
      let batch: BatchOut;
      try {
        batch = await rt.client.createBatch({
          items: entries.map(({ item, fileId }) => ({ file_id: fileId, target: item.target!, options: item.options, execution_policy: rt.config.location === "local" ? "LOCAL_ONLY" : "REMOTE_ALLOWED" })),
          execution_policy: rt.config.location === "local" ? "LOCAL_ONLY" : "REMOTE_ALLOWED",
        }, `${this.key()}-${runtimeId}`);
      } catch (e) {   // report this group's items and carry on: earlier groups already run and must still be persisted and polled
        for (const { item } of entries) this.dispatch({ type: "planned", key: item.key, error: toBody(e), route: { runtimeId, usesRemote: rt.config.location === "remote", note: "" } });
        continue;
      }
      const jobKeys: Record<string, string> = {};
      for (const j of batch.jobs) { const e = entries.find((x) => x.fileId === j.file_id); if (e) jobKeys[j.id] = e.item.key; }
      const run: BatchRun = { runtimeId, batchId: batch.id, usesRemote: rt.config.location === "remote", jobKeys, done: false };
      runs.push(run);
      this.dispatch({ type: "run-started", run, jobs: batch.jobs });
    }
    try { await this.persistRuns(this.state.runs); }
    finally { for (const r of runs) void this.pollRun(r); }   // polling must start even if persisting fails
  }

  private async pollRun(run: BatchRun): Promise<void> {
    if (this.polling.has(run.batchId)) return;
    this.polling.add(run.batchId);
    const rt = this.rt(run.runtimeId);
    const { minMs, maxMs } = this.deps.poll ?? { minMs: 250, maxMs: 1500 };
    const sleep = this.deps.sleep ?? defaultSleep;
    const signal = this.abort.signal;
    let delay = minMs, lastSig = "", errors = 0;
    try {
      while (rt && !signal.aborted) {
        try {
          const b = await rt.client.batch(run.batchId, signal);
          errors = 0;
          this.dispatch({ type: "batch-update", batchId: run.batchId, batch: b });
          if (b.state === "COMPLETED") { await this.collectResults(run); return; }
          const sig = b.jobs.map((j) => `${j.state}:${j.progress ?? "-"}`).join("|");
          delay = sig === lastSig ? Math.min(maxMs, Math.round(delay * 1.3)) : minMs;
          lastSig = sig;
        } catch (e) {
          if ((e as Error).name === "AbortError") return;
          if (e instanceof EZApiError && !e.retryable) return;       // not found / unauthorised: stop, don't spin
          if (++errors > 20) return;
          delay = Math.min(maxMs * 2, delay * 2);                    // network blip: back off, state stays intact server-side
        }
        await sleep(delay, signal);
      }
    } finally { this.polling.delete(run.batchId); this.pulse(); }
  }

  private async collectResults(run: BatchRun): Promise<void> {
    const rt = this.rt(run.runtimeId);
    if (!rt) return;
    const results: Record<string, JobResultOut> = {};
    await Promise.all(this.state.items.filter((i) => i.job && run.jobKeys[i.job.id] === i.key && i.job.state === "SUCCEEDED").map(async (i) => {
      try { results[i.job!.id] = await rt.client.jobResult(i.job!.id); } catch { /* expired results simply have no download */ }
    }));
    this.dispatch({ type: "results", results });
  }

  /**
   * Re-syncs every unfinished run immediately and restarts any polling loop that stopped (for example because the OS
   * suspended the app). Jobs keep running on the converter while the app is suspended; this just catches the UI up.
   */
  async refresh(): Promise<void> {
    await Promise.all(this.state.runs.filter((r) => !r.done).map(async (r) => {
      const rt = this.rt(r.runtimeId);
      if (!rt) return;
      try {
        const b = await rt.client.batch(r.batchId);
        this.dispatch({ type: "batch-update", batchId: r.batchId, batch: b });
        if (b.state === "COMPLETED") await this.collectResults(r); else void this.pollRun(r);
      } catch { /* still unreachable: the next foreground event tries again */ }
    }));
  }

  async cancel(): Promise<void> {
    await Promise.all(this.state.runs.filter((r) => !r.done).map((r) => this.rt(r.runtimeId)?.client.cancelBatch(r.batchId).then((b) =>
      this.dispatch({ type: "batch-update", batchId: r.batchId, batch: b })).catch(() => undefined)));
  }

  async retryFailed(): Promise<void> {
    for (const r of this.state.runs) {
      const rt = this.rt(r.runtimeId);
      if (!rt) continue;
      const b = await rt.client.retryFailed(r.batchId);
      this.dispatch({ type: "batch-update", batchId: r.batchId, batch: b });
      void this.pollRun(r);
    }
  }

  async retryJob(key: string): Promise<void> {
    const it = this.item(key);
    const run = this.state.runs.find((r) => it?.job && r.jobKeys[it.job.id] === key);
    const rt = run && this.rt(run.runtimeId);
    if (!it?.job || !run || !rt) return;
    await rt.client.retryJob(it.job.id);
    this.dispatch({ type: "batch-update", batchId: run.batchId, batch: await rt.client.batch(run.batchId) });
    void this.pollRun({ ...run, done: false });
  }

  // ------------------------------------------------------------------ results & cleanup

  async downloadUrl(key: string): Promise<string | undefined> {
    const it = this.item(key);
    const run = this.state.runs.find((r) => it?.job && r.jobKeys[it.job.id] === key);
    const rt = run && this.rt(run.runtimeId);
    if (!it?.result || !rt) return undefined;
    return rt.client.signedUrl(it.result.artifact_id);
  }

  async archiveUrl(runIndex = 0): Promise<string | undefined> {
    const run = this.state.runs[runIndex];
    const rt = run && this.rt(run.runtimeId);
    return rt && run ? rt.client.batchArchiveLink(run.batchId) : undefined;
  }

  /** Deletes results and uploads from the converters that hold them (the "delete now" control). */
  async deleteEverything(): Promise<void> {
    for (const r of this.state.runs) await this.rt(r.runtimeId)?.client.deleteBatch(r.batchId).catch(() => undefined);
    for (const [key, m] of this.fileIds) for (const [rid, fid] of m) await this.rt(rid)?.client.deleteFile(fid).catch(() => undefined);
    this.fileIds.clear();
    await this.clear();
  }

  // ------------------------------------------------------------------ resume after restart

  private async persistRuns(runs: BatchRun[]): Promise<void> {
    if (!this.deps.store) return;
    await setJSON(this.deps.store, RUNS_KEY, runs.map((r) => ({ runtimeId: r.runtimeId, batchId: r.batchId, usesRemote: r.usesRemote })));
  }

  /** Re-attaches to batches that were running when the app last closed. The server kept executing them. */
  async resume(): Promise<number> {
    if (!this.deps.store) return 0;
    const saved = await getJSON<{ runtimeId: string; batchId: string; usesRemote: boolean }[]>(this.deps.store, RUNS_KEY, []);
    let n = 0;
    for (const s of saved) {
      const rt = this.rt(s.runtimeId);
      if (!rt) continue;
      try {
        const b = await rt.client.batch(s.batchId);
        const jobKeys: Record<string, string> = {};
        const items: BatchItem[] = b.jobs.map((j) => {
          const key = this.key();
          jobKeys[j.id] = key;
          return { key, source: { name: j.original_name }, intake: { state: "ready", progress: 1, runtimeId: s.runtimeId }, options: j.plan?.options ?? {},
                   policy: "LOCAL_ONLY", planning: false, target: j.target_format, job: j, usesRemote: s.usesRemote, runtimeId: s.runtimeId } as BatchItem;
        });
        const run: BatchRun = { runtimeId: s.runtimeId, batchId: s.batchId, usesRemote: s.usesRemote, jobKeys, done: false };
        this.dispatch({ type: "add", items });
        this.dispatch({ type: "run-started", run, jobs: b.jobs });
        void this.pollRun(run);
        n++;
      } catch { /* expired or unreachable: nothing to resume */ }
    }
    if (!n) await this.persistRuns([]);
    return n;
  }
}

function previewPlan(it: BatchItem, t: TargetOut): NonNullable<BatchItem["plan"]> {
  return { source: it.file!.format ?? "", target: t.target, steps: [], fidelity: t.fidelity, warnings: [], options: it.options,
           estimate: { memory_mb: 0, seconds: 0, rough: true, progress: "indeterminate" }, location: "remote", multi_step: t.steps > 1 };
}

export { isTerminal };
