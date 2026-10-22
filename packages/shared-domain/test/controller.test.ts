import { describe, expect, inject, it } from "vitest";
import { EZClient } from "@ezconverter/api-client";
import { BatchController, MemoryStore, type Runtime, itemStatus, summarize } from "../src";
import { junk, sample } from "./fixtures";

const url = inject("backendUrl");
const d = url ? describe : describe.skip;

const device = (): Runtime => ({ config: { id: "device", label: "This device", location: "local", baseUrl: url }, client: new EZClient({ baseUrl: url, token: "test-token" }) });
const make = (opts: { runtimes?: Runtime[]; store?: MemoryStore; allowRemote?: boolean } = {}) => {
  const runtimes = opts.runtimes ?? [device()];
  return new BatchController({ runtimes: () => runtimes, settings: () => ({ allowRemote: opts.allowRemote ?? false, defaultPolicy: "AUTO" }), store: opts.store, poll: { minMs: 50, maxMs: 200 } });
};
async function until(c: BatchController, pred: () => boolean, ms = 90_000) {
  const end = Date.now() + ms;
  while (Date.now() < end) { if (pred()) return; await new Promise((r) => setTimeout(r, 50)); }
  throw new Error("timed out: " + JSON.stringify(c.getState().items.map((i) => [i.key, i.intake.state, i.job?.state])));
}

d("BatchController against the real backend", () => {
  it("runs a mixed batch end to end: independent outcomes, real results, ZIP link", async () => {
    const c = make();
    await c.addFiles([sample("png", "a.png"), sample("json", "b.json"), junk("c.dat"), sample("png", "a-again.png"), sample("csv", "d.csv")]);
    await until(c, () => c.getState().items.every((i) => i.intake.state === "ready" || i.intake.state === "error"));
    let st = c.getState();
    expect(st.items.map((i) => itemStatus(i, st.phase))).toEqual(["needs-target", "needs-target", "ineligible", "duplicate", "needs-target"]);
    expect(st.items[2]!.intake.error?.message).toMatch(/not recognised/i);

    const targets = await c.availableTargets(st.items[0]!.key);
    expect(targets.map((t) => t.target)).toEqual(expect.arrayContaining(["jpeg", "webp"]));
    expect(targets.every((t) => !t.remote)).toBe(true);

    c.setTarget(st.items[0]!.key, "webp");
    c.setTarget(st.items[1]!.key, "yaml");
    c.setTarget(st.items[4]!.key, "docx");              // csv -> docx is not a thing: must surface as an invalid item
    await c.settled();
    st = c.getState();
    expect(st.items.map((i) => itemStatus(i, st.phase))).toEqual(["ready", "ready", "ineligible", "duplicate", "invalid"]);
    expect(st.items[0]!.plan?.fidelity).toBe("lossy");
    expect(st.items[4]!.planError?.code).toBe("unsupported_conversion");
    expect(summarize(st).startable).toBe(2);

    await c.start();
    await until(c, () => c.getState().phase === "finished" && c.getState().items.filter((i) => i.job?.state === "SUCCEEDED").every((i) => !!i.result));
    st = c.getState();
    expect(st.items.map((i) => itemStatus(i, st.phase))).toEqual(["succeeded", "succeeded", "ineligible", "duplicate", "invalid"].map((s, k) => (k >= 2 ? (s === "invalid" ? "skipped" : s) : s)));
    expect(summarize(st)).toMatchObject({ succeeded: 2, failed: 0, finished: true, progress: 1 });
    expect(st.items[0]!.result?.name).toBe("a.webp");

    const dl = await c.downloadUrl(st.items[0]!.key);
    const res = await fetch(dl!);
    expect(res.status).toBe(200);
    expect(new Uint8Array(await res.arrayBuffer()).slice(0, 4)).toEqual(new Uint8Array([0x52, 0x49, 0x46, 0x46]));   // RIFF
    const zip = await fetch((await c.archiveUrl())!);
    const buf = new Uint8Array(await zip.arrayBuffer());
    expect(zip.headers.get("content-type")).toBe("application/zip");
    expect(String.fromCharCode(buf[0]!, buf[1]!)).toBe("PK");
    await c.deleteEverything();
    expect(c.getState().items).toHaveLength(0);
  });

  it("reports a failing conversion on its own item without disturbing the others", async () => {
    const c = make();
    await c.addFiles([sample("json", "good.json"), { name: "bad.json", size: 9, blob: new Blob(['{"a": 1,']) }]);
    await until(c, () => c.getState().items.every((i) => i.intake.state !== "queued" && i.intake.state !== "uploading"));
    expect(itemStatus(c.getState().items[1]!, "idle")).toMatch(/needs-target/);     // "bad.json" is plain text to the detector
    c.setAllTarget("yaml");
    await c.settled();
    const st = c.getState();
    expect(st.items[0]!.plan).toBeDefined();
    expect(st.items[1]!.planError?.code).toBe("unsupported_conversion");            // rejected at planning, with an explanation
    await c.start();
    await until(c, () => c.getState().phase === "finished");
    expect(c.getState().items[0]!.job?.state).toBe("SUCCEEDED");
    expect(c.getState().items[1]!.job).toBeUndefined();
  });

  it("LOCAL_ONLY with no capable local runtime uploads nothing", async () => {
    const dead: Runtime = { config: { id: "device", label: "This device", location: "local", baseUrl: "http://127.0.0.1:9" }, client: new EZClient({ baseUrl: "http://127.0.0.1:9" }) };
    const c = make({ runtimes: [dead, { ...device(), config: { id: "cloud", label: "Cloud", location: "remote", baseUrl: url } }], allowRemote: false });
    await c.addFiles([sample("png")]);
    await until(c, () => c.getState().items[0]!.intake.state === "error");
    expect(c.getState().items[0]!.intake.error).toMatchObject({ code: "network_error" });   // the local runtime is down; the cloud was NOT used for intake
    expect(c.getState().items[0]!.file).toBeUndefined();
  });

  it("re-attaches to a running batch after an app restart", async () => {
    const store = new MemoryStore();
    const c1 = make({ store });
    await c1.addFiles([sample("mp4", "clip.mp4")]);
    await until(c1, () => c1.getState().items[0]!.intake.state === "ready");
    c1.setTarget(c1.getState().items[0]!.key, "webm");
    await c1.settled();
    await c1.start();
    // "restart": a brand new controller with the same persisted store
    const c2 = make({ store });
    expect(await c2.resume()).toBe(1);
    await until(c2, () => c2.getState().phase === "finished" && !!c2.getState().items[0]!.result);
    expect(c2.getState().items[0]!.job?.state).toBe("SUCCEEDED");
    expect(c2.getState().items[0]!.source.name).toBe("clip.mp4");
  });

  it("cancel stops a batch and keeps the finished work", async () => {
    const c = make();
    await c.addFiles([sample("mp4", "long.mp4")]);
    await until(c, () => c.getState().items[0]!.intake.state === "ready");
    c.setTarget(c.getState().items[0]!.key, "webm");
    await c.settled();
    await c.start();
    await c.cancel();
    await until(c, () => c.getState().phase === "finished");
    const j = c.getState().items[0]!.job!;
    expect(["CANCELLED", "SUCCEEDED"]).toContain(j.state);       // cancellation is best-effort: both are honest outcomes
    if (j.state === "CANCELLED") expect(c.getState().items[0]!.result).toBeUndefined();
  });
});
