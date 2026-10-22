import { Icon, cx } from "@ezconverter/ui";
import { useEffect, useRef, useState } from "react";
import { type HistoryEntry, loadHistory, saveHistory, useApp } from "./state";
import { Convert } from "./views/Convert";
import { History } from "./views/History";
import { Settings } from "./views/Settings";

type View = "convert" | "history" | "settings";
const viewFromHash = (): View => (["history", "settings"].includes(location.hash.slice(2)) ? (location.hash.slice(2) as View) : "convert");

export function App() {
  const { state, catalog, controller } = useApp();
  const [view, setView] = useState<View>(viewFromHash);
  useEffect(() => { const f = () => setView(viewFromHash()); addEventListener("hashchange", f); return () => removeEventListener("hashchange", f); }, []);
  const go = (v: View) => { location.hash = v === "convert" ? "/" : `/${v}`; };

  // Record finished runs in local history exactly once.
  const recorded = useRef(new Set<string>());
  useEffect(() => {
    if (state.phase !== "finished") return;
    const fresh = state.runs.filter((r) => !recorded.current.has(r.batchId));
    if (!fresh.length) return;
    fresh.forEach((r) => recorded.current.add(r.batchId));
    void loadHistory().then((h) => {
      const entries: HistoryEntry[] = state.items.filter((i) => i.job && fresh.some((r) => r.jobKeys[i.job!.id] === i.key)).map((i) => ({
        id: i.job!.id, at: Date.now(), name: i.source.name, source: i.job!.source_format, target: i.job!.target_format, state: i.job!.state,
        size: i.result?.size, runtimeId: state.runs.find((r) => r.jobKeys[i.job!.id] === i.key)!.runtimeId, outputName: i.result?.name, error: i.job!.error?.message,
      }));
      return saveHistory([...entries, ...h]);
    });
  }, [state.phase, state.runs, state.items]);

  const offline = catalog.statuses.length > 0 && catalog.statuses.every((s) => s.state !== "online");
  const online = catalog.statuses.filter((s) => s.state === "online");
  const nav: [View, string, string][] = [["convert", "Convert", "swap"], ["history", "History", "clock"], ["settings", "Settings", "settings"]];
  return (
    <div className="mx-auto min-h-dvh max-w-[88rem] lg:grid lg:grid-cols-[17.5rem_minmax(0,1fr)]">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-30 focus:rounded-full focus:bg-surface focus:px-4 focus:py-2">Skip to content</a>
      <aside className="px-4 pt-4 sm:px-6 lg:sticky lg:top-0 lg:flex lg:h-dvh lg:flex-col lg:gap-8 lg:px-5 lg:py-6">
        <button type="button" className="flex min-h-11 items-center gap-3 text-lg font-extrabold tracking-tight" onClick={() => go("convert")} aria-label="EZConverter home">
          <span className="grid size-10 place-items-center rounded-2xl bg-accent text-accentink shadow-glow"><Icon name="swap" size={20} /></span>EZConverter
        </button>
        <nav aria-label="Main" className="fixed inset-x-4 bottom-3 z-20 mx-auto flex max-w-sm justify-between gap-1 rounded-full border border-line/70 bg-surface/95 p-1.5 shadow-card backdrop-blur lg:static lg:mx-0 lg:max-w-none lg:flex-col lg:justify-start lg:rounded-none lg:border-0 lg:bg-transparent lg:p-0 lg:shadow-none lg:backdrop-blur-none">
          {nav.map(([v, label, icon]) => (
            <button key={v} type="button" onClick={() => go(v)} aria-current={view === v ? "page" : undefined}
                    className={cx("flex min-h-12 flex-1 items-center justify-center gap-2.5 rounded-full px-4 text-sm font-semibold transition-colors lg:flex-none lg:justify-start lg:rounded-2xl",
                                  view === v ? "bg-accent text-accentink shadow-glow" : "text-muted hover:bg-surface2 hover:text-ink")}>
              <Icon name={icon} size={18} />{label}
            </button>
          ))}
        </nav>
        <div className="mt-auto hidden rounded-card bg-[image:var(--hero)] p-5 text-white shadow-glow lg:block" data-testid="privacy-card">
          <span className="grid size-10 place-items-center rounded-full bg-white/20"><Icon name="lock" size={20} /></span>
          <p className="mt-3 font-bold">Your files stay yours</p>
          <p className="mt-1 text-sm text-white/85">
            {online.some((s) => s.location === "local") ? "Converting on this device. Nothing is uploaded unless you allow it." : online.length ? "Connected to a remote converter. Files upload only with your consent." : "Connect a converter in Settings to begin."}
          </p>
        </div>
      </aside>
      <div className="flex min-h-dvh min-w-0 flex-col px-4 pb-28 pt-4 sm:px-6 lg:px-8 lg:pb-10 lg:pt-6">
        {offline && view !== "settings" && (
          <div role="alert" className="mb-4 rounded-2xl bg-warnsoft p-3.5 text-sm text-warn">
            No converter is reachable. Start the local converter or check the address in <button className="font-semibold underline" onClick={() => go("settings")}>Settings</button>.
          </div>
        )}
        <main id="main" className="flex-1" tabIndex={-1}>
          {view === "convert" && <Convert goSettings={() => go("settings")} />}
          {view === "history" && <History />}
          {view === "settings" && <Settings />}
        </main>
        <div aria-live="polite" className="sr-only" data-testid="live-region">
          {state.phase === "running" ? "Conversion in progress" : state.phase === "finished" ? "Conversion finished" : ""}
        </div>
        <footer className="mt-10 text-center text-xs text-muted">
          {controller && "Files are converted by the engines you configure and deleted automatically after the retention period."}
        </footer>
      </div>
    </div>
  );
}
