/** Pure batch workspace state: no I/O, no framework. The controller (controller.ts) performs the side effects. */
import type { BatchOut, ErrorBody, FileOut, JobOut, JobResultOut, PlanOut } from "@ezconverter/contracts";
import { isTerminal } from "@ezconverter/contracts";
import type { ExecutionPolicy } from "@ezconverter/contracts";
import type { UploadSource } from "@ezconverter/api-client";

export type IntakeState = "queued" | "uploading" | "ready" | "error";

export interface BatchItem {
  key: string;
  source: UploadSource;
  intake: { state: IntakeState; progress: number; error?: ErrorBody; runtimeId?: string };
  file?: FileOut;                       // present once uploaded and identified
  target?: string;
  options: Record<string, unknown>;
  policy: ExecutionPolicy;
  plan?: PlanOut;
  planError?: ErrorBody;
  planning: boolean;
  routeNote?: string;                   // human explanation of where it will run
  runtimeId?: string;                   // runtime chosen for execution
  usesRemote?: boolean;
  job?: JobOut;
  result?: JobResultOut;
  duplicateOf?: string;
  removed?: boolean;
}

export interface BatchRun { runtimeId: string; batchId: string; usesRemote: boolean; jobKeys: Record<string, string> /* job id -> item key */; done: boolean }

export interface BatchState {
  items: BatchItem[];
  runs: BatchRun[];
  phase: "idle" | "running" | "finished";
  defaultPolicy: ExecutionPolicy;
}

export const initialState = (defaultPolicy: ExecutionPolicy = "AUTO"): BatchState => ({ items: [], runs: [], phase: "idle", defaultPolicy });

export type Action =
  | { type: "add"; items: BatchItem[] }
  | { type: "remove"; key: string }
  | { type: "clear" }
  | { type: "intake"; key: string; patch: Partial<BatchItem["intake"]> }
  | { type: "identified"; key: string; file: FileOut; runtimeId: string }
  | { type: "set-target"; key: string; target: string | undefined }
  | { type: "set-all-target"; target: string; keys?: string[] }
  | { type: "set-options"; key: string; options: Record<string, unknown> }
  | { type: "set-policy"; key: string | "all"; policy: ExecutionPolicy }
  | { type: "set-default-policy"; policy: ExecutionPolicy }
  | { type: "planning"; key: string }
  | { type: "planned"; key: string; plan?: PlanOut; error?: ErrorBody; route?: { runtimeId: string; usesRemote: boolean; note: string } }
  | { type: "run-started"; run: BatchRun; jobs: JobOut[] }
  | { type: "batch-update"; batchId: string; batch: BatchOut }
  | { type: "results"; results: Record<string, JobResultOut> }
  | { type: "reset-run" };

/** Duplicate detection: same content hash after upload, or same name+size+type before it. */
export function markDuplicates(items: BatchItem[]): BatchItem[] {
  const seen = new Map<string, string>();
  return items.map((it) => {
    if (it.removed) return it;
    const id = it.file ? `sha:${it.file.sha256}` : `meta:${it.source.name}|${it.source.size ?? ""}|${it.source.type ?? ""}`;
    const prior = seen.get(id);
    if (!prior) { seen.set(id, it.key); return it.duplicateOf ? { ...it, duplicateOf: undefined } : it; }
    return it.duplicateOf === prior ? it : { ...it, duplicateOf: prior };
  });
}

const patch = (s: BatchState, key: string, f: (i: BatchItem) => BatchItem): BatchState =>
  ({ ...s, items: s.items.map((i) => (i.key === key ? f(i) : i)) });

export function reducer(s: BatchState, a: Action): BatchState {
  switch (a.type) {
    case "add": return { ...s, phase: s.phase === "finished" ? "idle" : s.phase, items: markDuplicates([...s.items, ...a.items]) };
    case "remove": return { ...s, items: s.items.filter((i) => i.key !== a.key) };
    case "clear": return initialState(s.defaultPolicy);
    case "intake": return patch(s, a.key, (i) => ({ ...i, intake: { ...i.intake, ...a.patch } }));
    case "identified":
      return { ...s, items: markDuplicates(s.items.map((i) => i.key === a.key
        ? { ...i, file: a.file, intake: { state: a.file.eligible ? "ready" : "error", progress: 1, runtimeId: a.runtimeId,
            error: a.file.eligible ? undefined : { code: "unsupported_input", message: a.file.ineligible_reason ?? "This file can't be converted.", details: {} } } }
        : i)) };
    case "set-target": return patch(s, a.key, (i) => ({ ...i, target: a.target, options: {}, plan: undefined, planError: undefined, routeNote: undefined, runtimeId: undefined }));
    case "set-all-target": {
      const only = a.keys ? new Set(a.keys) : undefined;
      return { ...s, items: s.items.map((i) => (i.intake.state === "ready" && (!only || only.has(i.key)) && !i.duplicateOf
        ? { ...i, target: a.target, options: {}, plan: undefined, planError: undefined, routeNote: undefined, runtimeId: undefined } : i)) };
    }
    case "set-options": return patch(s, a.key, (i) => ({ ...i, options: a.options, plan: undefined, planError: undefined }));
    case "set-policy":
      return a.key === "all"
        ? { ...s, defaultPolicy: a.policy, items: s.items.map((i) => ({ ...i, policy: a.policy, plan: undefined, planError: undefined })) }
        : patch(s, a.key, (i) => ({ ...i, policy: a.policy, plan: undefined, planError: undefined }));
    case "set-default-policy": return { ...s, defaultPolicy: a.policy };
    case "planning": return patch(s, a.key, (i) => ({ ...i, planning: true }));
    case "planned":
      return patch(s, a.key, (i) => ({ ...i, planning: false, plan: a.plan, planError: a.error, routeNote: a.route?.note,
        runtimeId: a.route?.runtimeId, usesRemote: a.route?.usesRemote, options: a.plan ? a.plan.options : i.options }));
    case "run-started": {
      const byKey = new Map(Object.entries(a.run.jobKeys).map(([jobId, key]) => [key, a.jobs.find((j) => j.id === jobId)]));
      return { ...s, phase: "running", runs: [...s.runs, a.run], items: s.items.map((i) => (byKey.has(i.key) ? { ...i, job: byKey.get(i.key), result: undefined } : i)) };
    }
    case "batch-update": {
      const run = s.runs.find((r) => r.batchId === a.batchId);
      if (!run) return s;
      const byJob = new Map(a.batch.jobs.map((j) => [j.id, j]));
      const runs = s.runs.map((r) => (r.batchId === a.batchId ? { ...r, done: a.batch.state === "COMPLETED" } : r));
      const items = s.items.map((i) => {
        const jobId = i.job && run.jobKeys[i.job.id] === i.key ? i.job.id : undefined;
        const j = jobId ? byJob.get(jobId) : undefined;
        return j ? { ...i, job: j } : i;
      });
      return { ...s, runs, items, phase: runs.every((r) => r.done) ? "finished" : "running" };
    }
    case "results": return { ...s, items: s.items.map((i) => (i.job && a.results[i.job.id] ? { ...i, result: a.results[i.job.id] } : i)) };
    case "reset-run": return { ...s, runs: [], phase: "idle", items: s.items.map((i) => ({ ...i, job: undefined, result: undefined })) };
  }
}

// ------------------------------------------------------------------------------------ selectors

export type ItemStatus = "uploading" | "queued" | "needs-target" | "planning" | "invalid" | "ready" | "waiting" | "converting" | "succeeded" | "failed" | "cancelled" | "skipped" | "duplicate" | "ineligible";

export function itemStatus(i: BatchItem, phase: BatchState["phase"]): ItemStatus {
  if (i.job) {
    switch (i.job.state) {
      case "SUCCEEDED": return "succeeded";
      case "FAILED": return "failed";
      case "CANCELLED": return "cancelled";
      case "RUNNING": case "VERIFYING": return "converting";
      default: return "waiting";
    }
  }
  if (i.intake.state === "error") return "ineligible";
  if (i.intake.state !== "ready") return i.intake.state === "uploading" ? "uploading" : "queued";
  if (i.duplicateOf) return "duplicate";
  if (phase !== "idle") return "skipped";
  if (!i.target) return "needs-target";
  if (i.planning) return "planning";
  if (i.planError) return "invalid";
  return i.plan ? "ready" : "planning";
}

export const isStartable = (i: BatchItem) => i.intake.state === "ready" && !i.duplicateOf && !!i.target && !!i.plan && !i.planError && !i.planning;

export interface Summary {
  total: number; startable: number; uploading: number; succeeded: number; failed: number; cancelled: number; skipped: number;
  running: number; progress: number; indeterminate: boolean; finished: boolean;
}

export function summarize(s: BatchState): Summary {
  const out: Summary = { total: s.items.length, startable: 0, uploading: 0, succeeded: 0, failed: 0, cancelled: 0, skipped: 0, running: 0, progress: 0, indeterminate: false, finished: s.phase === "finished" };
  let withJobs = 0, acc = 0;
  for (const i of s.items) {
    const st = itemStatus(i, s.phase);
    if (st === "uploading" || st === "queued") out.uploading++;
    if (isStartable(i)) out.startable++;
    if (st === "succeeded") out.succeeded++;
    if (st === "failed") out.failed++;
    if (st === "cancelled") out.cancelled++;
    if (st === "skipped" || st === "duplicate" || st === "ineligible") out.skipped++;
    if (st === "converting" || st === "waiting") out.running++;
    if (i.job) {
      withJobs++;
      if (isTerminal(i.job.state)) acc += 1;
      else if (i.job.progress == null) { if (i.job.state === "RUNNING" || i.job.state === "VERIFYING") out.indeterminate = true; }
      else acc += i.job.progress;
    }
  }
  out.progress = withJobs ? acc / withJobs : 0;
  return out;
}

/** Groups items that share the same conversion (source → target) so settings can be applied once. */
export function groupByConversion(items: BatchItem[]): { key: string; source: string; target: string; items: BatchItem[] }[] {
  const m = new Map<string, { key: string; source: string; target: string; items: BatchItem[] }>();
  for (const i of items) {
    if (!i.file?.format || !i.target) continue;
    const key = `${i.file.format}>${i.target}`;
    const g = m.get(key) ?? { key, source: i.file.format, target: i.target, items: [] };
    g.items.push(i);
    m.set(key, g);
  }
  return [...m.values()];
}

/** Targets offered for the whole selection: only those every ready, unique file can reach. */
export function commonTargets(perItem: string[][]): string[] {
  if (!perItem.length) return [];
  return perItem.reduce((acc, cur) => acc.filter((t) => cur.includes(t)));
}
