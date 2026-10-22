import { describe, expect, it, vi } from "vitest";
import type { EZClient } from "@ezconverter/api-client";
import { BatchController, MemoryStore, type Runtime, isStartable } from "../src";

// Deterministic regressions for plan invalidation and start(): fake runtimes only, no backend involved.

const fileOut = (id: string) => ({ id, original_name: "a.png", size: 1, sha256: `sha-${id}`, format: "png", created_at: 0, expires_at: 1, eligible: true, ineligible_reason: null,
  detection: { format: "png", confidence: "high", method: "magic", extension_mismatch: false, damaged: false, details: {}, notes: [] } });
const planOut = (target: string, options: Record<string, unknown>) => ({ source: "png", target, steps: [], fidelity: "lossy", warnings: [], options,
  estimate: { memory_mb: 0, seconds: 0, rough: true, progress: "indeterminate" }, location: "local", multi_step: false });
const jobOut = (id: string, fileId: string, target: string, state = "SUCCEEDED") => ({ id, file_id: fileId, original_name: "a.png", target_format: target, state, progress: 1 });

interface Fake { rt: Runtime; client: Record<string, ReturnType<typeof vi.fn>> }
function fakeRuntime(id: string, location: "local" | "remote", targets: string[], over: Record<string, unknown> = {}): Fake {
  let n = 0;
  const batches = new Map<string, unknown>();
  const client: Record<string, ReturnType<typeof vi.fn>> = {
    upload: vi.fn(async () => fileOut(`${id}-f${++n}`)),
    targets: vi.fn(async () => ({ source: "png", targets: targets.map((t) => ({ target: t, steps: 1, fidelity: "lossy" })) })),
    deleteFile: vi.fn(async () => undefined),
    plan: vi.fn(async (req: { target: string; options: Record<string, unknown> }) => planOut(req.target, req.options)),
    createBatch: vi.fn(async (req: { items: { file_id: string; target: string }[] }) => {
      const bid = `${id}-b${++n}`;
      const jobs = req.items.map((it, k) => jobOut(`${bid}-j${k}`, it.file_id, it.target));
      const b = { id: bid, state: "COMPLETED", jobs };
      batches.set(bid, b);
      return b;
    }),
    batch: vi.fn(async (bid: string) => batches.get(bid)),
    jobResult: vi.fn(async () => ({})),
  };
  Object.assign(client, over);
  return { rt: { config: { id, label: id, location, baseUrl: `http://${id}` }, client: client as unknown as EZClient }, client };
}

const file = { name: "a.png", size: 1, blob: new Blob(["x"]) };
async function until(pred: () => boolean, ms = 3000) {
  const end = Date.now() + ms;
  while (Date.now() < end) { if (pred()) return; await new Promise((r) => setTimeout(r, 5)); }
  throw new Error("timed out");
}
const deferred = <T>() => { let resolve!: (v: T) => void; const promise = new Promise<T>((r) => { resolve = r; }); return { promise, resolve }; };
const make = (rts: Runtime[], over: { store?: MemoryStore; defaultPolicy?: "AUTO" | "REMOTE_ALLOWED" } = {}) =>
  new BatchController({ runtimes: () => rts, settings: () => ({ allowRemote: true, defaultPolicy: over.defaultPolicy ?? "AUTO" }), store: over.store,
    planDebounceMs: 60, poll: { minMs: 5, maxMs: 10 } });
async function ready(c: BatchController, n: number) {
  const keys = await c.addFiles(Array.from({ length: n }, () => file));
  await until(() => c.getState().items.every((i) => i.intake.state === "ready"));
  return keys;
}

describe("plan invalidation", () => {
  it("a plan already in flight cannot overwrite newer options", async () => {
    const dev = fakeRuntime("dev", "local", ["jpeg"]);
    const first = deferred<ReturnType<typeof planOut>>();
    dev.client.plan!.mockImplementationOnce(async () => first.promise);
    const c = make([dev.rt]);
    const [key] = await ready(c, 1);
    c.setTarget(key!, "jpeg");
    await until(() => dev.client.plan!.mock.calls.length === 1);        // plan #1 (old options) is now in flight
    c.setOptions(key!, { quality: 90 });
    first.resolve(planOut("jpeg", {}));                                 // stale answer lands inside the debounce window
    await new Promise((r) => setTimeout(r, 5));
    const mid = c.getState().items[0]!;
    expect(mid.options).toEqual({ quality: 90 });                      // the new options survive
    expect(mid.plan).toBeUndefined();                                   // and the stale plan is not startable
    expect(isStartable(mid)).toBe(false);
    await c.settled();
    const end = c.getState().items[0]!;
    expect(end.options).toEqual({ quality: 90 });
    expect(end.plan?.options).toEqual({ quality: 90 });
    expect(isStartable(end)).toBe(true);
  });

  it("two overlapping plan() calls on one item still settle (no stuck in-flight marker)", async () => {
    const dev = fakeRuntime("dev", "local", ["jpeg"]);
    const gate = deferred<void>();
    dev.client.plan!.mockImplementation(async (req: { target: string; options: Record<string, unknown> }) => { await gate.promise; return planOut(req.target, req.options); });
    const c = make([dev.rt]);
    const [key] = await ready(c, 1);
    c.setTarget(key!, "jpeg");
    await until(() => dev.client.plan!.mock.calls.length === 1);
    void c.plan(key!);                                                  // a second plan() while the first is in flight
    gate.resolve();
    await Promise.race([c.settled(), new Promise((_, rej) => setTimeout(() => rej(new Error("settled() hung")), 2000))]);
    expect(isStartable(c.getState().items[0]!)).toBe(true);
  });
});

describe("start()", () => {
  it("concurrent calls submit each file once", async () => {
    const dev = fakeRuntime("dev", "local", ["jpeg"]);
    const gate = deferred<void>();
    const real = dev.client.createBatch!.getMockImplementation() as (...a: unknown[]) => Promise<unknown>;
    dev.client.createBatch!.mockImplementation(async (...a: unknown[]) => { await gate.promise; return real(...a); });
    const c = make([dev.rt]);
    const [key] = await ready(c, 1);
    c.setTarget(key!, "jpeg");
    await c.settled();
    const p1 = c.start();
    const p2 = c.start();                                              // double click before 'run-started'
    gate.resolve();
    await Promise.all([p1, p2]);
    await c.settled();
    expect(dev.client.createBatch).toHaveBeenCalledTimes(1);
    expect(c.getState().runs).toHaveLength(1);
  });

  it("a failing later group does not strand the runs that already started", async () => {
    const dev = fakeRuntime("dev", "local", ["jpeg"]);
    const cloud = fakeRuntime("cloud", "remote", ["webp"], { createBatch: vi.fn(async () => { throw new Error("cloud exploded"); }) });
    const store = new MemoryStore();
    const c = make([dev.rt, cloud.rt], { store, defaultPolicy: "REMOTE_ALLOWED" });
    const [a, b] = await ready(c, 2);
    c.setTarget(a!, "jpeg");                                            // local group
    c.setTarget(b!, "webp");                                            // only the remote can do this one
    await c.settled();
    expect(c.getState().items.map((i) => i.runtimeId)).toEqual(["dev", "cloud"]);

    await expect(c.start()).resolves.toBeUndefined();                  // the failure is surfaced on the item, not thrown
    await c.settled();
    const st = c.getState();
    expect(st.runs.map((r) => r.runtimeId)).toEqual(["dev"]);
    expect(JSON.parse((await store.get("ez.activeRuns"))!)).toHaveLength(1);   // persisted for resume
    expect(dev.client.batch).toHaveBeenCalled();                        // and polled
    expect(st.items[0]!.job?.state).toBe("SUCCEEDED");
    expect(st.items[1]!.job).toBeUndefined();
    expect(st.items[1]!.planError?.message).toBe("cloud exploded");     // the failed group is reported on its items
    expect(st.items[1]!.planning).toBe(false);
  });
});
