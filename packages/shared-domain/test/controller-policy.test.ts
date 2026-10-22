import { describe, expect, it, vi } from "vitest";
import type { EZClient } from "@ezconverter/api-client";
import { BatchController, type Runtime } from "../src";

function remoteRuntime() {
  const upload = vi.fn(async () => ({ id: "f1", original_name: "a.png", size: 1, sha256: "x", format: "png", created_at: 0, expires_at: 1, eligible: true, ineligible_reason: null,
    detection: { format: "png", confidence: "high", method: "magic", extension_mismatch: false, damaged: false, details: {}, notes: [] } }));
  const rt = { config: { id: "cloud", label: "Cloud", location: "remote", baseUrl: "https://c" }, client: { upload, targets: async () => ({ source: "png", targets: [] }), deleteFile: async () => undefined } as unknown as EZClient } as Runtime;
  return { rt, upload };
}
const file = { name: "a.png", size: 1, blob: new Blob(["x"]) };
const settle = async (c: BatchController) => { for (let i = 0; i < 40 && c.getState().items.some((x) => x.intake.state === "queued" || x.intake.state === "uploading"); i++) await new Promise((r) => setTimeout(r, 10)); };

describe("intake never uploads without consent", () => {
  it.each([
    ["LOCAL_ONLY", true, false],     // never, even with the standing remote consent
    ["LOCAL_ONLY", false, false],
    ["AUTO", false, false],          // AUTO needs the consent setting
    ["AUTO", true, true],
    ["REMOTE_ALLOWED", false, true], // explicit policy is consent by itself
  ] as const)("policy=%s allowRemote=%s -> uploads=%s", async (policy, allowRemote, uploads) => {
    const { rt, upload } = remoteRuntime();
    const c = new BatchController({ runtimes: () => [rt], settings: () => ({ allowRemote, defaultPolicy: policy }) });
    await c.addFiles([file]);
    await settle(c);
    expect(upload.mock.calls.length > 0).toBe(uploads);
    const it0 = c.getState().items[0]!;
    if (!uploads) {
      expect(it0.intake.state).toBe("error");
      expect(it0.intake.error?.message).toMatch(/not uploaded anywhere/);
      expect(it0.intake.error?.code).toBe(policy === "LOCAL_ONLY" ? "local_only_violation" : "no_runtime");
    }
  });
});
