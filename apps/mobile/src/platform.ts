/** Thin adapters over native modules, kept separate so they can be mocked in unit tests. */
import AsyncStorage from "@react-native-async-storage/async-storage";
import type { UploadSource } from "@ezconverter/api-client";
import type { KeyValueStore } from "@ezconverter/shared-domain";
import * as DocumentPicker from "expo-document-picker";
import { File, Paths } from "expo-file-system";
import * as SecureStore from "expo-secure-store";
import * as Sharing from "expo-sharing";
import { Linking, Platform } from "react-native";

/** Non-secret app data (history, settings, active batch ids). */
export const appStore: KeyValueStore = {
  async get(k) { try { return await AsyncStorage.getItem(k); } catch { return null; } },
  async set(k, v) { try { await AsyncStorage.setItem(k, v); } catch { /* storage full or unavailable: keep running in memory */ } },
  async remove(k) { try { await AsyncStorage.removeItem(k); } catch { /* ignore */ } },
};

/**
 * Access tokens live in the OS keychain/keystore (iOS Keychain, Android Keystore-backed storage).
 * The web preview, used only for automated UI smoke tests, has no secure storage and falls back to AsyncStorage.
 */
export const secrets = {
  async get(k: string): Promise<string> {
    try { return (Platform.OS === "web" ? await AsyncStorage.getItem(`secret.${k}`) : await SecureStore.getItemAsync(k)) ?? ""; } catch { return ""; }
  },
  async set(k: string, v: string): Promise<void> {
    try {
      if (Platform.OS === "web") { await AsyncStorage.setItem(`secret.${k}`, v); return; }
      if (v) await SecureStore.setItemAsync(k, v, { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY }); else await SecureStore.deleteItemAsync(k);
    } catch { /* surfaced as an auth error when the token is used */ }
  },
};

export interface PickedAsset { uri: string; name: string; size?: number; mimeType?: string; file?: Blob }

export function assetsToSources(assets: PickedAsset[]): UploadSource[] {
  return assets.map((a) => ({
    name: a.name || "file", size: a.size, type: a.mimeType ?? "application/octet-stream",
    ...(a.file ? { blob: a.file } : { uri: a.uri }),       // web provides a File; native streams from the cached copy by URI
  }));
}

/** System file picker (Files app / Storage Access Framework / iCloud Drive). Copies to cache so the URI stays readable. */
export async function pickFiles(): Promise<UploadSource[]> {
  const res = await DocumentPicker.getDocumentAsync({ multiple: true, copyToCacheDirectory: true, type: "*/*" });
  if (res.canceled) return [];
  return assetsToSources(res.assets as unknown as PickedAsset[]);
}

/** Downloads a result to the app cache and opens the OS share sheet (Save to Files, Drive, Messages, AirDrop…). */
export async function saveAndShare(url: string, name: string, mimeType?: string): Promise<"shared" | "downloaded" | "opened"> {
  if (Platform.OS === "web") { await Linking.openURL(url); return "opened"; }
  const safe = name.replace(/[^\w.\- ]+/g, "_").slice(0, 120) || "result";
  const dest = new File(Paths.cache, safe);
  if (dest.exists) dest.delete();
  const file = await File.downloadFileAsync(url, dest);
  if (await Sharing.isAvailableAsync()) { await Sharing.shareAsync(file.uri, { mimeType, dialogTitle: `Save ${safe}` }); return "shared"; }
  return "downloaded";
}
