import { EZClient } from "@ezconverter/api-client";
import type { CapabilitiesOut, CapabilityOut, Health } from "@ezconverter/contracts";
import {
  type AppSettings, BatchController, type BatchState, DEFAULT_SETTINGS, type Runtime, getJSON, setJSON,
} from "@ezconverter/shared-domain";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { bootSettings, webStore } from "./platform";

const SETTINGS_KEY = "ez.settings";

export function buildRuntimes(s: AppSettings): Runtime[] {
  const out: Runtime[] = [];
  if (s.localUrl) out.push({ config: { id: "device", label: "This device", location: "local", baseUrl: s.localUrl, token: s.localToken || undefined }, client: new EZClient({ baseUrl: s.localUrl, token: s.localToken || undefined }) });
  if (s.remoteUrl) out.push({ config: { id: "cloud", label: "Cloud converter", location: "remote", baseUrl: s.remoteUrl, token: s.remoteToken || undefined }, client: new EZClient({ baseUrl: s.remoteUrl, token: s.remoteToken || undefined }) });
  return out;
}

export interface RuntimeStatus { id: string; label: string; location: "local" | "remote"; state: "checking" | "online" | "unauthorized" | "offline"; health?: Health; caps?: CapabilitiesOut; error?: string }
export interface Catalog { statuses: RuntimeStatus[]; formats: Map<string, { name: string; family: string }>; caps: Map<string, CapabilityOut>; loading: boolean }

interface Ctx {
  settings: AppSettings; setSettings: (p: Partial<AppSettings>) => void;
  runtimes: Runtime[]; controller: BatchController; state: BatchState; catalog: Catalog; refreshCatalog: () => void; ready: boolean;
}
const C = createContext<Ctx | null>(null);
export const useApp = (): Ctx => { const v = useContext(C); if (!v) throw new Error("AppProvider missing"); return v; };

export function AppProvider({ children }: { children: ReactNode }) {
  const [settings, setS] = useState<AppSettings>(() => bootSettings(null));
  const [ready, setReady] = useState(false);
  useEffect(() => { void getJSON<Partial<AppSettings> | null>(webStore, SETTINGS_KEY, null).then((v) => { setS(bootSettings(v)); setReady(true); }); }, []);
  const setSettings = useCallback((p: Partial<AppSettings>) => setS((cur) => { const next = { ...cur, ...p }; void setJSON(webStore, SETTINGS_KEY, next); return next; }), []);

  const runtimes = useMemo(() => buildRuntimes(settings), [settings.localUrl, settings.localToken, settings.remoteUrl, settings.remoteToken]);
  const rtRef = useRef(runtimes); rtRef.current = runtimes;
  const setRef = useRef(settings); setRef.current = settings;
  const controller = useMemo(() => new BatchController({ runtimes: () => rtRef.current, settings: () => setRef.current, store: webStore }), []);
  const state = useSyncExternalStore(controller.subscribe, controller.getState);

  const [catalog, setCatalog] = useState<Catalog>({ statuses: [], formats: new Map(), caps: new Map(), loading: true });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setCatalog((c) => ({ ...c, loading: true }));
    void Promise.all(runtimes.map(async (rt): Promise<RuntimeStatus> => {
      const base = { id: rt.config.id, label: rt.config.label, location: rt.config.location };
      try {
        const [health, caps] = await Promise.all([rt.client.health(), rt.client.capabilities()]);
        return { ...base, state: "online", health, caps };
      } catch (e) {
        const code = (e as { code?: string }).code;
        return { ...base, state: code === "unauthenticated" ? "unauthorized" : "offline", error: (e as Error).message };
      }
    })).then((statuses) => {
      if (!live) return;
      const formats = new Map<string, { name: string; family: string }>();
      const caps = new Map<string, CapabilityOut>();
      for (const s of statuses) for (const f of s.caps?.formats ?? []) formats.set(f.id, { name: f.name, family: f.family });
      for (const s of [...statuses].reverse()) for (const c of s.caps?.capabilities ?? []) caps.set(c.id, c);   // local wins over remote
      setCatalog({ statuses, formats, caps, loading: false });
    });
    return () => { live = false; };
  }, [runtimes, tick]);

  // Theme & accessibility switches live on <html> so the CSS tokens react to them.
  useEffect(() => {
    const el = document.documentElement;
    if (settings.theme === "system") el.removeAttribute("data-theme"); else el.setAttribute("data-theme", settings.theme);
    el.dataset.largeText = String(settings.largeText);
    el.dataset.reduceMotion = String(settings.reduceMotion);
  }, [settings.theme, settings.largeText, settings.reduceMotion]);

  // The saved default processing mode applies to files added from now on; without this it would be ignored after load.
  useEffect(() => { if (ready) controller.setDefaultPolicy(settings.defaultPolicy); }, [ready, settings.defaultPolicy, controller]);

  useEffect(() => { if (ready) void controller.resume(); }, [ready, controller]);

  const value = useMemo(() => ({ settings, setSettings, runtimes, controller, state, catalog, refreshCatalog: () => setTick((t) => t + 1), ready }), [settings, setSettings, runtimes, controller, state, catalog, ready]);
  return <C.Provider value={value}>{children}</C.Provider>;
}

// ---------------------------------------------------------------------------------- history

export interface HistoryEntry { id: string; at: number; name: string; source: string; target: string; state: string; size?: number; runtimeId: string; outputName?: string; error?: string }
const HISTORY_KEY = "ez.history";
export const loadHistory = () => getJSON<HistoryEntry[]>(webStore, HISTORY_KEY, []);
export const saveHistory = (h: HistoryEntry[]) => setJSON(webStore, HISTORY_KEY, h.slice(0, 200));
