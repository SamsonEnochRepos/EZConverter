import { app, BrowserWindow, dialog, ipcMain, net, protocol, session, shell } from "electron";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { APP_ORIGIN, APP_SCHEME, buildCsp, isAllowedDownloadUrl, isAllowedNavigation, isTrustedSender, resolveWithin, validateNoPayload, validateOpenExternal } from "./security";
import { Sidecar } from "./sidecar";

protocol.registerSchemesAsPrivileged([{ scheme: APP_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true } }]);

const repoRoot = join(__dirname, "..", "..", "..");
const webRoot = app.isPackaged ? join(process.resourcesPath, "web") : join(__dirname, "..", "..", "web", "dist");
let sidecar: Sidecar | undefined;
let win: BrowserWindow | undefined;

function serveApp(csp: string): void {
  protocol.handle(APP_SCHEME, async (req) => {
    const u = new URL(req.url);
    const file = u.host === "app" ? resolveWithin(webRoot, u.pathname) : null;
    if (!file) return new Response("Not found", { status: 404 });
    const res = await net.fetch(pathToFileURL(file).toString());
    const headers = new Headers(res.headers);
    headers.set("Content-Security-Policy", csp);
    headers.set("X-Content-Type-Options", "nosniff");
    headers.set("Cross-Origin-Opener-Policy", "same-origin");
    return new Response(res.body, { status: res.status, headers });
  });
}

function hardenSession(sidecarOrigin: string): void {
  const ses = session.defaultSession;
  ses.setPermissionRequestHandler((_wc, _perm, cb) => cb(false));        // no camera, mic, geolocation, notifications…
  ses.setPermissionCheckHandler(() => false);
  ses.on("will-download", (event, item) => {
    if (!isAllowedDownloadUrl(item.getURL(), sidecarOrigin)) { event.preventDefault(); return; }
    // No setSavePath: Electron shows the native "Save as…" dialog, so the user picks where results go.
  });
}

function handleIpc(): void {
  const guard = <T>(fn: (payload: unknown) => T) => (e: Electron.IpcMainInvokeEvent, payload?: unknown): T => {
    if (!isTrustedSender(e.senderFrame?.url)) throw new Error("Untrusted sender");
    return fn(payload);
  };
  ipcMain.handle("ez:app-info", guard((p) => { validateNoPayload(p); return { version: app.getVersion(), platform: process.platform }; }));
  ipcMain.handle("ez:open-external", guard(async (p) => { await shell.openExternal(validateOpenExternal(p)); }));
}

async function createWindow(runtime: { url: string; token: string }): Promise<void> {
  win = new BrowserWindow({
    width: 1100, height: 860, minWidth: 420, minHeight: 600, show: false, backgroundColor: "#0b0f17",
    title: "EZConverter",
    webPreferences: {
      preload: join(__dirname, "preload.js"),
      contextIsolation: true, sandbox: true, nodeIntegration: false, nodeIntegrationInWorker: false, nodeIntegrationInSubFrames: false,
      webSecurity: true, allowRunningInsecureContent: false, webviewTag: false, spellcheck: false, devTools: !app.isPackaged,
      additionalArguments: [`--ez-runtime=${Buffer.from(JSON.stringify(runtime)).toString("base64")}`],
    },
  });
  win.once("ready-to-show", () => win?.show());
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  win.webContents.on("will-navigate", (e, url) => { if (!isAllowedNavigation(url)) e.preventDefault(); });
  win.webContents.on("will-attach-webview", (e) => e.preventDefault());
  await win.loadURL(`${APP_ORIGIN}/index.html`);
}

async function boot(): Promise<void> {
  const dataDir = join(app.getPath("userData"), "converter-data");
  sidecar = new Sidecar({ dataDir, repoRoot, resourcesPath: app.isPackaged ? process.resourcesPath : undefined, log: (m) => console.error(`[converter] ${m}`) });
  let info;
  try { info = await sidecar.start(); }
  catch (e) {
    dialog.showErrorBox("EZConverter can't start its converter", (e as Error).message);
    app.quit();
    return;
  }
  serveApp(buildCsp(info.origin));
  hardenSession(info.origin);
  handleIpc();
  await createWindow({ url: info.url, token: info.token });
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on("second-instance", () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });
  app.on("web-contents-created", (_e, contents) => {
    contents.on("will-navigate", (e, url) => { if (!isAllowedNavigation(url)) e.preventDefault(); });
    contents.setWindowOpenHandler(() => ({ action: "deny" }));
  });
  void app.whenReady().then(boot);
  app.on("window-all-closed", () => { app.quit(); });
  let quitting = false;
  app.on("before-quit", (e) => {
    if (quitting || !sidecar) return;
    e.preventDefault(); quitting = true;
    void sidecar.stop().finally(() => app.exit(0));
  });
}
