import { type AppSettings, DEFAULT_SETTINGS, type KeyValueStore } from "@ezconverter/shared-domain";

/** localStorage-backed store; every access is guarded because storage can be blocked or cleared. */
export const webStore: KeyValueStore = {
  async get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  async set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage unavailable: the app keeps working in memory */ } },
  async remove(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};

/** Desktop injects the sidecar address through the preload bridge; the browser build uses saved settings. */
export interface DesktopBridge { localRuntime?: { url: string; token: string }; saveFile?: (url: string, name: string) => Promise<void> }
declare global { interface Window { ezDesktop?: DesktopBridge } }

export function bootSettings(saved: Partial<AppSettings> | null): AppSettings {
  const merged = { ...DEFAULT_SETTINGS, ...(saved ?? {}) };
  const d = typeof window !== "undefined" ? window.ezDesktop?.localRuntime : undefined;
  return d ? { ...merged, localUrl: d.url, localToken: d.token } : merged;
}
