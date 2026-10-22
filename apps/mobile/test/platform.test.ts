import { beforeEach, describe, expect, it, vi } from "vitest";

// Native modules are mocked: these tests check OUR logic (what is stored where, how picks map to uploads),
// not Expo's. Behaviour on real iOS/Android devices is verified separately (see docs/status).
const { store, secure, os } = vi.hoisted(() => ({ store: new Map<string, string>(), secure: new Map<string, string>(), os: { Platform: { OS: "ios" as string } } }));

vi.mock("react-native", () => ({ Platform: os.Platform, Linking: { openURL: vi.fn() } }));
vi.mock("@react-native-async-storage/async-storage", () => ({ default: {
  getItem: async (k: string) => store.get(k) ?? null, setItem: async (k: string, v: string) => void store.set(k, v), removeItem: async (k: string) => void store.delete(k) } }));
vi.mock("expo-secure-store", () => ({ WHEN_UNLOCKED_THIS_DEVICE_ONLY: 6,
  getItemAsync: async (k: string) => secure.get(k) ?? null, setItemAsync: async (k: string, v: string) => void secure.set(k, v), deleteItemAsync: async (k: string) => void secure.delete(k) }));
vi.mock("expo-document-picker", () => ({ getDocumentAsync: vi.fn() }));
vi.mock("expo-sharing", () => ({ isAvailableAsync: async () => true, shareAsync: vi.fn() }));
vi.mock("expo-file-system", () => ({ Paths: { cache: "/cache" }, File: class { static downloadFileAsync = vi.fn(async (_u: string, f: unknown) => f); exists = false; uri = "file:///cache/x"; constructor(public dir: string, public name: string) {} delete() {} } }));

import * as DocumentPicker from "expo-document-picker";
import * as Sharing from "expo-sharing";
import { appStore, assetsToSources, pickFiles, saveAndShare, secrets } from "../src/platform";
import { MOBILE_DEFAULTS, buildRuntimes } from "../src/state";

beforeEach(() => { store.clear(); secure.clear(); os.Platform.OS = "ios"; vi.clearAllMocks(); });

describe("secrets and storage", () => {
  it("keeps the access token in the secure store and out of plain storage (native)", async () => {
    await secrets.set("remoteToken", "s3cret");
    expect(secure.get("remoteToken")).toBe("s3cret");
    expect([...store.values()].join()).not.toContain("s3cret");
    expect(await secrets.get("remoteToken")).toBe("s3cret");
    await secrets.set("remoteToken", "");
    expect(secure.has("remoteToken")).toBe(false);          // clearing the field deletes the keychain item
  });
  it("only the web preview (automated UI tests) falls back to AsyncStorage", async () => {
    os.Platform.OS = "web";
    await secrets.set("remoteToken", "t");
    expect(secure.size).toBe(0);
    expect(store.get("secret.remoteToken")).toBe("t");
  });
  it("storage failures never throw into the UI", async () => {
    expect(await appStore.get("missing")).toBeNull();
    await expect(appStore.set("k", "v")).resolves.toBeUndefined();
  });
});

describe("file picking", () => {
  it("maps picked assets to upload sources that stream from the cached file URI", () => {
    const [a, b] = assetsToSources([{ uri: "file:///cache/a.png", name: "a.png", size: 10, mimeType: "image/png" }, { uri: "file:///cache/x", name: "", size: undefined }]);
    expect(a).toEqual({ name: "a.png", size: 10, type: "image/png", uri: "file:///cache/a.png" });
    expect(b).toMatchObject({ name: "file", type: "application/octet-stream", uri: "file:///cache/x" });
    expect(a!.blob).toBeUndefined();                          // never reads the whole file into JS memory
  });
  it("returns nothing when the user cancels and asks for multiple, cached copies", async () => {
    vi.mocked(DocumentPicker.getDocumentAsync).mockResolvedValueOnce({ canceled: true, assets: null } as never);
    expect(await pickFiles()).toEqual([]);
    vi.mocked(DocumentPicker.getDocumentAsync).mockResolvedValueOnce({ canceled: false, assets: [{ uri: "file:///c/a.pdf", name: "a.pdf", size: 5, mimeType: "application/pdf" }] } as never);
    expect(await pickFiles()).toHaveLength(1);
    expect(DocumentPicker.getDocumentAsync).toHaveBeenLastCalledWith(expect.objectContaining({ multiple: true, copyToCacheDirectory: true }));
  });
});

describe("exporting results", () => {
  it("downloads to the cache under a sanitised name and opens the share sheet", async () => {
    const r = await saveAndShare("https://c/v1/artifacts/x/download?sig=1", "../../evil name?.webp", "image/webp");
    expect(r).toBe("shared");
    const file = vi.mocked(Sharing.shareAsync).mock.calls[0]!;
    expect(file[0]).toBe("file:///cache/x");
    expect(file[1]).toMatchObject({ mimeType: "image/webp" });
  });
});

describe("runtime model", () => {
  it("a phone has no local runtime: only a remote one, and none until an address is set", () => {
    expect(buildRuntimes({ ...MOBILE_DEFAULTS, remoteUrl: "" })).toEqual([]);
    const rts = buildRuntimes({ ...MOBILE_DEFAULTS, remoteUrl: "https://c.example", remoteToken: "t" });
    expect(rts).toHaveLength(1);
    expect(rts[0]!.config).toMatchObject({ location: "remote", baseUrl: "https://c.example" });
    expect(MOBILE_DEFAULTS.allowRemote).toBe(false);        // uploading is opt-in
    expect(MOBILE_DEFAULTS.localUrl).toBe("");
  });
});
