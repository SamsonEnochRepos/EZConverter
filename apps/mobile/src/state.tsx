import { EZClient } from "@ezconverter/api-client";
import type { CapabilitiesOut, CapabilityOut, Health } from "@ezconverter/contracts";
import { type AppSettings, BatchController, type BatchState, DEFAULT_SETTINGS, type Runtime, getJSON, setJSON } from "@ezconverter/shared-domain";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { AccessibilityInfo, AppState } from "react-native";
import { appStore, secrets } from "./platform";
import { type Theme, useTheme } from "./theme";

const SETTINGS_KEY = "ez.settings.mobile";

/**
 * A phone cannot run the conversion engines, so its only converter is a remote one. `LOCAL_ONLY` therefore refuses with an
 * explanation instead of silently uploading, and AUTO needs the user's consent before the first upload.
 */
export const MOBILE_DEFAULTS: AppSettings = { ...DEFAULT_SETTINGS, localUrl: "", localToken: "", defaultPolicy: "AUTO" };

export function buildRuntimes(s: AppSettings): Runtime[] {
  if (!s.remoteUrl) return [];
  return [{ config: { id: "cloud", label: "Cloud converter", location: "remote", baseUrl: s.remoteUrl, token: s.remoteToken || undefined }, client: new EZClient({ baseUrl: s.remoteUrl, token: s.remoteToken || undefined }) }];
}

export interface Status { state: "checking" | "online" | "unauthorized" | "offline"; health?: Health; caps?: CapabilitiesOut; error?: string }
export interface Catalog { status: Status; formats: Map<string, { name: string; family: string }>; caps: Map<string, CapabilityOut> }
export interface HistoryEntry { id: string; at: number; name: string; source: string; target: string; state: string; size?: number; error?: string }

interface Ctx {
  settings: AppSettings; setSettings: (p: Partial<AppSettings>) => void; theme: Theme; controller: BatchController; state: BatchState; catalog: Catalog;
  refreshCatalog: () => void; ready: boolean; runtimes: Runtime[];
}
const C = createContext<Ctx | null>(null);
export const useApp = (): Ctx => { const v = useContext(C); if (!v) throw new Error("AppProvider missing"); return v; };

export function AppProvider({ children }: { children: ReactNode }) {
  const [settings, setS] = useState<AppSettings>(MOBILE_DEFAULTS);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    void (async () => {
      const saved = await getJSON<Partial<AppSettings>>(appStore, SETTINGS_KEY, {});
      const token = await secrets.get("remoteToken");
      setS({ ...MOBILE_DEFAULTS, ...saved, remoteToken: token, localUrl: "", localToken: "" });
      setReady(true);
    })();
  }, []);
  const setSettings = useCallback((p: Partial<AppSettings>) => setS((cur) => {
    const next = { ...cur, ...p, localUrl: "", localToken: "" };
    const { remoteToken, ...rest } = next;      // the token never goes into the plain store
    void setJSON(appStore, SETTINGS_KEY, rest);
    if ("remoteToken" in p) void secrets.set("remoteToken", remoteToken);
    return next;
  }), []);

  const runtimes = useMemo(() => buildRuntimes(settings), [settings.remoteUrl, settings.remoteToken]);
  const rtRef = useRef(runtimes); rtRef.current = runtimes;
  const setRef = useRef(settings); setRef.current = settings;
  const controller = useMemo(() => new BatchController({ runtimes: () => rtRef.current, settings: () => setRef.current, store: appStore, uploadConcurrency: 2, poll: { minMs: 400, maxMs: 2500 } }), []);
  const state = useSyncExternalStore(controller.subscribe, controller.getState);
  const theme = useTheme(settings.theme);

  const [catalog, setCatalog] = useState<Catalog>({ status: { state: "checking" }, formats: new Map(), caps: new Map() });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    const rt = runtimes[0];
    if (!rt) { setCatalog({ status: { state: "offline", error: "No converter configured" }, formats: new Map(), caps: new Map() }); return; }
    setCatalog((c) => ({ ...c, status: { state: "checking" } }));
    void Promise.all([rt.client.health(), rt.client.capabilities()]).then(([health, caps]) => {
      if (!live) return;
      setCatalog({ status: { state: "online", health, caps }, formats: new Map(caps.formats.map((f) => [f.id, { name: f.name, family: f.family }])), caps: new Map(caps.capabilities.map((c) => [c.id, c])) });
    }).catch((e: { code?: string; message?: string }) => {
      if (live) setCatalog({ status: { state: e.code === "unauthenticated" ? "unauthorized" : "offline", error: e.message }, formats: new Map(), caps: new Map() });
    });
    return () => { live = false; };
  }, [runtimes, tick]);

  // The OS may suspend the app mid-batch. Jobs keep running on the converter; on return we re-attach and catch up.
  useEffect(() => {
    if (!ready) return;
    void controller.resume();
    const sub = AppState.addEventListener("change", (s) => { if (s === "active") { void controller.refresh(); setTick((t) => t + 1); } });
    return () => sub.remove();
  }, [ready, controller]);

  // The saved default processing mode applies to files added from now on; without this it would be ignored after load.
  useEffect(() => { if (ready) controller.setDefaultPolicy(settings.defaultPolicy); }, [ready, settings.defaultPolicy, controller]);

  useEffect(() => { void AccessibilityInfo.isReduceMotionEnabled().then((on) => on && setSettings({ reduceMotion: true })).catch(() => undefined); }, [setSettings]);

  const value = useMemo(() => ({ settings, setSettings, theme, controller, state, catalog, refreshCatalog: () => setTick((t) => t + 1), ready, runtimes }), [settings, setSettings, theme, controller, state, catalog, ready, runtimes]);
  return <C.Provider value={value}>{children}</C.Provider>;
}

const HISTORY_KEY = "ez.history.mobile";
export const loadHistory = () => getJSON<HistoryEntry[]>(appStore, HISTORY_KEY, []);
export const saveHistory = (h: HistoryEntry[]) => setJSON(appStore, HISTORY_KEY, h.slice(0, 200));
