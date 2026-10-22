import { describe, expect, inject, it } from "vitest";
import { EZApiError, EZClient } from "../src";
import { sample } from "./fixtures";

const url = inject("backendUrl");
const d = url ? describe : describe.skip;

d("EZClient against the real backend", () => {
  const client = new EZClient({ baseUrl: url, token: "test-token" });

  it("reports health without credentials and rejects bad tokens with the standard envelope", async () => {
    expect((await new EZClient({ baseUrl: url }).health()).location).toBe("local");
    await expect(new EZClient({ baseUrl: url, token: "nope" }).capabilities()).rejects.toMatchObject({ code: "unauthenticated", status: 401 });
  });

  it("lists verified capabilities", async () => {
    const caps = await client.capabilities();
    expect(caps.capabilities.length).toBeGreaterThan(200);
    expect(caps.capabilities.every((c) => c.verified && c.available)).toBe(true);
    expect(caps.formats.find((f) => f.id === "png")?.family).toBe("image");
  });

  it("uploads, plans, converts and downloads a real file", async () => {
    const progress: number[] = [];
    const f = await client.upload(sample("png"), { policy: "LOCAL_ONLY", onProgress: (p) => progress.push(p) });
    expect(f.format).toBe("png");
    expect(progress.at(-1)).toBe(1);
    const plan = await client.plan({ file_id: f.id, target: "webp", options: { quality: 60 }, execution_policy: "LOCAL_ONLY" });
    expect(plan.steps[0]!.capability_id).toBe("pillow.png->webp");
    let job = await client.convert({ file_id: f.id, target: "webp", options: { quality: 60 }, execution_policy: "LOCAL_ONLY" }, "idem-1");
    expect((await client.convert({ file_id: f.id, target: "webp", options: { quality: 60 }, execution_policy: "LOCAL_ONLY" }, "idem-1")).id).toBe(job.id);
    for (let i = 0; i < 100 && !["SUCCEEDED", "FAILED"].includes(job.state); i++) { await new Promise((r) => setTimeout(r, 100)); job = await client.job(job.id); }
    expect(job.state).toBe("SUCCEEDED");
    const res = await client.jobResult(job.id);
    expect(res.verification.ok).toBe(true);
    const blob = await client.downloadBlob(res.download_path);
    expect(blob.size).toBe(res.size);
    const signed = await client.signedUrl(res.download_path);
    expect((await fetch(signed)).status).toBe(200);              // works without Authorization
  });

  it("surfaces actionable errors as EZApiError", async () => {
    const f = await client.upload(sample("png"), { policy: "LOCAL_ONLY" });
    const err = await client.plan({ file_id: f.id, target: "svg", options: {}, execution_policy: "LOCAL_ONLY" }).catch((e) => e);
    expect(err).toBeInstanceOf(EZApiError);
    expect(err).toMatchObject({ code: "unsupported_conversion", status: 422 });
    expect(err.details.available_targets).toContain("jpeg");
    await expect(client.job("0".repeat(32))).rejects.toMatchObject({ code: "not_found" });
  });

  it("keeps users apart", async () => {
    const f = await client.upload(sample("json"), { policy: "LOCAL_ONLY" });
    const other = new EZClient({ baseUrl: url, token: "other-token" });
    await expect(other.getFile(f.id)).rejects.toMatchObject({ status: 404 });
  });

  it("reports a clear error when the server is unreachable", async () => {
    await expect(new EZClient({ baseUrl: "http://127.0.0.1:9", token: "x" }).health()).rejects.toMatchObject({ code: "network_error" });
  });
});
