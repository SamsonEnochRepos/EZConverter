/**
 * Sandboxed preload. Exposes the smallest possible, frozen API to the page via contextBridge:
 * the sidecar address/token chosen at launch, platform info, and one validated IPC call.
 * Nothing from Node or Electron itself is reachable from the renderer.
 */
import { contextBridge, ipcRenderer } from "electron";

function runtimeFromArgv(): { url: string; token: string } | undefined {
  const arg = process.argv.find((a) => a.startsWith("--ez-runtime="));
  if (!arg) return undefined;
  try {
    const j = JSON.parse(Buffer.from(arg.slice("--ez-runtime=".length), "base64").toString("utf8")) as { url?: unknown; token?: unknown };
    return typeof j.url === "string" && typeof j.token === "string" ? { url: j.url, token: j.token } : undefined;
  } catch { return undefined; }
}

contextBridge.exposeInMainWorld("ezDesktop", Object.freeze({
  localRuntime: runtimeFromArgv(),
  platform: process.platform,
  getAppInfo: (): Promise<{ version: string; platform: string }> => ipcRenderer.invoke("ez:app-info"),
  openExternal: (url: string): Promise<void> => ipcRenderer.invoke("ez:open-external", url),
}));
