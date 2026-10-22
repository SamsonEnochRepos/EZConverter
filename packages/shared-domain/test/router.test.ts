import { describe, expect, it } from "vitest";
import type { EZClient } from "@ezconverter/api-client";
import { CapabilityProbe, type Runtime, routeConversion } from "../src";

function rt(id: string, location: "local" | "remote", can: Record<string, string[]> | "down"): Runtime & { calls: number } {
  const o = {
    calls: 0,
    config: { id, label: id === "device" ? "This device" : "Cloud", location, baseUrl: `http://${id}` },
    client: {
      targets: async (source: string) => {
        o.calls++;
        if (can === "down") throw new Error("connection refused");
        return { source, targets: (can[source] ?? []).map((t) => ({ target: t, steps: 1, fidelity: "lossy", engines: ["x"], transform: false })) };
      },
    } as unknown as EZClient,
  };
  return o as unknown as Runtime & { calls: number };
}

const device = () => rt("device", "local", { docx: ["pdf"], png: ["jpeg"] });
const cloud = () => rt("cloud", "remote", { docx: ["pdf"], heic: ["jpeg"], png: ["jpeg", "avif"] });

describe("execution routing", () => {
  it("LOCAL_ONLY uses a capable local runtime and never touches remote ones", async () => {
    const c = cloud();
    const d = await routeConversion("LOCAL_ONLY", [device(), c], { source: "docx", target: "pdf" }, { allowRemote: true });
    expect(d).toMatchObject({ ok: true, usesRemote: false, policy: "LOCAL_ONLY" });
    expect(c.calls).toBe(0);
  });

  it("LOCAL_ONLY never falls back to the cloud, even when the cloud could do it and consent was given", async () => {
    const d = await routeConversion("LOCAL_ONLY", [device(), cloud()], { source: "heic", target: "jpeg" }, { allowRemote: true });
    expect(d.ok).toBe(false);
    if (!d.ok) {
      expect(d.code).toBe("local_only_violation");
      expect(d.message).toMatch(/nothing was uploaded/i);
      expect(d.message).toMatch(/Switch to “Allow remote”/);   // tells the user how to proceed
    }
  });

  it("LOCAL_ONLY with an unreachable local runtime fails closed", async () => {
    const d = await routeConversion("LOCAL_ONLY", [rt("device", "local", "down"), cloud()], { source: "docx", target: "pdf" }, { allowRemote: true });
    expect(d).toMatchObject({ ok: false, code: "local_only_violation" });
  });

  it("AUTO prefers local, and only uses remote with standing consent", async () => {
    expect(await routeConversion("AUTO", [device(), cloud()], { source: "png", target: "jpeg" }, { allowRemote: false })).toMatchObject({ ok: true, usesRemote: false });
    const noConsent = await routeConversion("AUTO", [device(), cloud()], { source: "heic", target: "jpeg" }, { allowRemote: false });
    expect(noConsent).toMatchObject({ ok: false, code: "remote_consent_required" });
    const consent = await routeConversion("AUTO", [device(), cloud()], { source: "heic", target: "jpeg" }, { allowRemote: true });
    expect(consent).toMatchObject({ ok: true, usesRemote: true, policy: "REMOTE_ALLOWED" });
    if (consent.ok) expect(consent.explanation).toMatch(/uploaded to Cloud/);
  });

  it("REMOTE_ALLOWED is explicit consent but still prefers a capable local runtime", async () => {
    expect(await routeConversion("REMOTE_ALLOWED", [device(), cloud()], { source: "docx", target: "pdf" }, { allowRemote: false })).toMatchObject({ ok: true, usesRemote: false });
    expect(await routeConversion("REMOTE_ALLOWED", [device(), cloud()], { source: "heic", target: "jpeg" }, { allowRemote: false })).toMatchObject({ ok: true, usesRemote: true });
  });

  it("explains unsupported conversions and missing runtimes", async () => {
    expect(await routeConversion("AUTO", [device(), cloud()], { source: "mp3", target: "docx" }, { allowRemote: true })).toMatchObject({ ok: false, code: "unsupported_conversion" });
    expect(await routeConversion("AUTO", [], { source: "png", target: "jpeg" }, { allowRemote: true })).toMatchObject({ ok: false, code: "no_runtime" });
  });

  it("caches capability probes per runtime and source", async () => {
    const d = device(), probe = new CapabilityProbe(60_000);
    for (let i = 0; i < 3; i++) await routeConversion("AUTO", [d], { source: "png", target: "jpeg" }, { allowRemote: false }, probe);
    expect(d.calls).toBe(1);
    probe.invalidate();
    await routeConversion("AUTO", [d], { source: "png", target: "jpeg" }, { allowRemote: false }, probe);
    expect(d.calls).toBe(2);
  });
});
