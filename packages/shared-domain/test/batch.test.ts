import { describe, expect, it } from "vitest";
import type { FileOut, JobOut } from "@ezconverter/contracts";
import { type BatchItem, commonTargets, groupByConversion, initialState, itemStatus, markDuplicates, reducer, summarize } from "../src";

const file = (id: string, format: string, sha = id, eligible = true): FileOut => ({
  id, original_name: `${id}.${format}`, size: 100, sha256: sha, format, created_at: 0, expires_at: 1, eligible,
  ineligible_reason: eligible ? null : "bad", detection: { format, confidence: "high", method: "magic", extension_mismatch: false, damaged: false, details: {}, notes: [] },
} as FileOut);
const item = (key: string, over: Partial<BatchItem> = {}): BatchItem => ({ key, source: { name: `${key}.png`, size: 10 }, intake: { state: "ready", progress: 1 }, options: {}, policy: "AUTO", planning: false, ...over });
const job = (id: string, state: JobOut["state"], progress: number | null = null): JobOut => ({ id, state, progress, file_id: "f", original_name: "x", source_format: "png", target_format: "webp", attempts: 1, max_attempts: 3, warnings: [], cancel_requested: false, can_retry: false, created_at: 0, updated_at: 0, expires_at: 1 } as JobOut);

describe("batch reducer", () => {
  it("flags duplicates by content hash after upload and by name+size before", () => {
    let s = reducer(initialState(), { type: "add", items: [item("a"), item("b", { source: { name: "a.png", size: 10 } }), item("c", { source: { name: "other.png", size: 10 } })] });
    expect(s.items.map((i) => i.duplicateOf)).toEqual([undefined, "a", undefined]);
    s = reducer(s, { type: "identified", key: "a", file: file("fa", "png", "same"), runtimeId: "r" });
    s = reducer(s, { type: "identified", key: "c", file: file("fc", "png", "same"), runtimeId: "r" });
    expect(s.items.find((i) => i.key === "c")?.duplicateOf).toBe("a");
    expect(itemStatus(s.items.find((i) => i.key === "c")!, s.phase)).toBe("duplicate");
  });

  it("ineligible files carry the server's reason and are never startable", () => {
    const s = reducer(initialState(), { type: "add", items: [item("a", { intake: { state: "uploading", progress: 0.4 } })] });
    const t = reducer(s, { type: "identified", key: "a", file: file("fa", "dat", "x", false), runtimeId: "r" });
    expect(itemStatus(t.items[0]!, t.phase)).toBe("ineligible");
    expect(t.items[0]!.intake.error?.message).toBe("bad");
  });

  it("set-all-target applies only to ready, unique files and resets stale plans", () => {
    let s = reducer(initialState(), { type: "add", items: [item("a"), item("b", { intake: { state: "error", progress: 1 } }), item("c", { source: { name: "z", size: 1 } })] });
    s = reducer(s, { type: "set-all-target", target: "webp" });
    expect(s.items.map((i) => i.target)).toEqual(["webp", undefined, "webp"]);
    s = reducer(s, { type: "planned", key: "a", plan: { options: { quality: 80 } } as any });
    s = reducer(s, { type: "set-options", key: "a", options: { quality: 50 } });
    expect(s.items[0]!.plan).toBeUndefined();     // a stale plan must not stay startable
  });

  it("progress aggregates terminal jobs as complete and flags indeterminate engines", () => {
    let s = reducer(initialState(), { type: "add", items: [item("a"), item("b"), item("c")] });
    s = { ...s, phase: "running", items: [{ ...s.items[0]!, job: job("j1", "SUCCEEDED") }, { ...s.items[1]!, job: job("j2", "RUNNING", 0.5) }, { ...s.items[2]!, job: job("j3", "RUNNING", null) }] };
    const m = summarize(s);
    expect(m.progress).toBeCloseTo((1 + 0.5 + 0) / 3);
    expect(m.indeterminate).toBe(true);
    expect(m.succeeded).toBe(1);
    expect(m.running).toBe(2);
  });

  it("batch updates mark the run done and finish the phase", () => {
    let s = reducer(initialState(), { type: "add", items: [item("a")] });
    const j = job("j1", "QUEUED");
    s = reducer(s, { type: "run-started", run: { runtimeId: "r", batchId: "b1", usesRemote: false, jobKeys: { j1: "a" }, done: false }, jobs: [j] });
    expect(s.phase).toBe("running");
    s = reducer(s, { type: "batch-update", batchId: "b1", batch: { id: "b1", state: "COMPLETED", jobs: [job("j1", "FAILED")] } as any });
    expect(s.phase).toBe("finished");
    expect(itemStatus(s.items[0]!, s.phase)).toBe("failed");
  });

  it("groups by conversion and intersects common targets", () => {
    const items = [item("a", { file: file("a", "png"), target: "webp" }), item("b", { file: file("b", "png"), target: "webp" }), item("c", { file: file("c", "docx"), target: "pdf" })];
    expect(groupByConversion(items).map((g) => [g.key, g.items.length])).toEqual([["png>webp", 2], ["docx>pdf", 1]]);
    expect(commonTargets([["pdf", "png"], ["png", "txt"], ["png"]])).toEqual(["png"]);
    expect(commonTargets([])).toEqual([]);
  });

  it("markDuplicates is stable when called repeatedly", () => {
    const once = markDuplicates([item("a"), item("b")]);
    expect(markDuplicates(once)).toEqual(once);
  });
});

describe("default policy", () => {
  it("applies to files added afterwards without touching existing items", () => {
    let s = reducer(initialState("AUTO"), { type: "add", items: [item("a", { policy: "AUTO" })] });
    s = reducer(s, { type: "set-default-policy", policy: "LOCAL_ONLY" });
    expect(s.defaultPolicy).toBe("LOCAL_ONLY");
    expect(s.items[0]!.policy).toBe("AUTO");
  });
});
