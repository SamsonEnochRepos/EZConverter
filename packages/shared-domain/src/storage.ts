/** Minimal async key-value contract implemented per platform (localStorage, AsyncStorage/SecureStore, Electron store). */
export interface KeyValueStore {
  get(key: string): Promise<string | null>;
  set(key: string, value: string): Promise<void>;
  remove(key: string): Promise<void>;
}

export class MemoryStore implements KeyValueStore {
  private m = new Map<string, string>();
  async get(k: string) { return this.m.get(k) ?? null; }
  async set(k: string, v: string) { this.m.set(k, v); }
  async remove(k: string) { this.m.delete(k); }
}

export async function getJSON<T>(store: KeyValueStore, key: string, fallback: T): Promise<T> {
  try { const raw = await store.get(key); return raw ? (JSON.parse(raw) as T) : fallback; } catch { return fallback; }
}
export const setJSON = (store: KeyValueStore, key: string, value: unknown) => store.set(key, JSON.stringify(value));

export interface AppSettings {
  allowRemote: boolean;
  defaultPolicy: "AUTO" | "LOCAL_ONLY" | "REMOTE_ALLOWED";
  theme: "system" | "light" | "dark";
  largeText: boolean;
  reduceMotion: boolean;
  localUrl: string;
  localToken: string;
  remoteUrl: string;
  remoteToken: string;
}

export const DEFAULT_SETTINGS: AppSettings = {
  allowRemote: false, defaultPolicy: "AUTO", theme: "system", largeText: false, reduceMotion: false,
  localUrl: "http://127.0.0.1:8787", localToken: "", remoteUrl: "", remoteToken: "",
};
