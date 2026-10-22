import { afterEach, describe, expect, it } from "vitest";
import { DEFAULT_SETTINGS } from "@ezconverter/shared-domain";
import { bootSettings, webStore } from "./platform";

afterEach(() => { delete (window as { ezDesktop?: unknown }).ezDesktop; localStorage.clear(); });

describe("bootSettings", () => {
  it("falls back to defaults and merges saved values", () => {
    expect(bootSettings(null)).toEqual(DEFAULT_SETTINGS);
    expect(bootSettings({ theme: "dark", remoteUrl: "https://c" })).toMatchObject({ theme: "dark", remoteUrl: "https://c", allowRemote: false });
  });
  it("the desktop shell's sidecar address always wins over saved values, so a stale saved address cannot redirect files", () => {
    (window as { ezDesktop?: unknown }).ezDesktop = { localRuntime: { url: "http://127.0.0.1:5555", token: "launch-token" } };
    const s = bootSettings({ localUrl: "http://evil.example", localToken: "stolen" });
    expect(s.localUrl).toBe("http://127.0.0.1:5555");
    expect(s.localToken).toBe("launch-token");
  });
});

describe("webStore", () => {
  it("round-trips and never throws when storage is blocked", async () => {
    await webStore.set("k", "v");
    expect(await webStore.get("k")).toBe("v");
    const orig = Storage.prototype.getItem;
    Storage.prototype.getItem = () => { throw new Error("blocked"); };
    try { expect(await webStore.get("k")).toBeNull(); } finally { Storage.prototype.getItem = orig; }
  });
});
