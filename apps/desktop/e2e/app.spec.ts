import { type ElectronApplication, type Page, _electron as electron, expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const root = resolve(__dirname, "../../..");
const sample = (ext: string, fmt: string) => {
  const p = join(root, `tests/.fixture-cache/sample.${ext}`);
  if (!existsSync(p)) execFileSync(process.env.EZ_PYTHON ?? "python", ["-c", `import sys; sys.path.insert(0,'tests'); from fixtures.generate import fixture; fixture('${fmt}')`], { cwd: root });
  return p;
};

let app: ElectronApplication;
let page: Page;
let userData: string;

test.beforeAll(async () => {
  userData = mkdtempSync(join(tmpdir(), "ez-electron-"));
  app = await electron.launch({
    // --no-sandbox is needed ONLY because this CI container runs as root; the renderer keeps `sandbox: true`.
    args: ["--no-sandbox", `--user-data-dir=${userData}`, "."],
    cwd: resolve(__dirname, ".."),
    env: { ...process.env, EZ_PYTHON: process.env.EZ_PYTHON ?? "python" } as Record<string, string>,
  });
  page = await app.firstWindow();
  await page.waitForLoadState("domcontentloaded");
});
test.afterAll(async () => { await app?.close().catch(() => undefined); });

test("window is hardened: isolation, sandbox, no Node, no remote content", async () => {
  const prefs = await app.evaluate(({ BrowserWindow }) => {
    const p = (BrowserWindow.getAllWindows()[0]!.webContents as unknown as { getLastWebPreferences(): Record<string, unknown> }).getLastWebPreferences();
    return { contextIsolation: p.contextIsolation, sandbox: p.sandbox, nodeIntegration: p.nodeIntegration, webSecurity: p.webSecurity, webviewTag: p.webviewTag, nodeIntegrationInWorker: p.nodeIntegrationInWorker };
  });
  expect(prefs).toMatchObject({ contextIsolation: true, sandbox: true, nodeIntegration: false, webSecurity: true, webviewTag: false, nodeIntegrationInWorker: false });
  expect(page.url()).toBe("ezc://app/index.html");
  expect(await page.title()).toBe("EZConverter");

  const leaked = await page.evaluate(() => ({
    require: typeof (window as any).require, process: typeof (window as any).process, Buffer: typeof (window as any).Buffer,
    module: typeof (window as any).module, ipcRenderer: typeof (window as any).ipcRenderer, electron: typeof (window as any).electron,
  }));
  expect(leaked).toEqual({ require: "undefined", process: "undefined", Buffer: "undefined", module: "undefined", ipcRenderer: "undefined", electron: "undefined" });
});

test("preload exposes only a frozen, minimal bridge", async () => {
  const bridge = await page.evaluate(() => {
    const b = (window as any).ezDesktop;
    let mutated = false;
    try { b.localRuntime = { url: "http://evil", token: "x" }; mutated = b.localRuntime?.url === "http://evil"; } catch { /* frozen */ }
    return { keys: Object.keys(b).sort(), url: b.localRuntime?.url, hasToken: !!b.localRuntime?.token, mutated, frozen: Object.isFrozen(b) };
  });
  expect(bridge.keys).toEqual(["getAppInfo", "localRuntime", "openExternal", "platform"]);
  expect(bridge.url).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/);       // loopback only
  expect(bridge.hasToken && !bridge.mutated && bridge.frozen).toBe(true);
});

test("IPC: validated payloads only", async () => {
  const info = await page.evaluate(() => (window as any).ezDesktop.getAppInfo());
  expect(info).toMatchObject({ version: expect.any(String), platform: process.platform });
  for (const bad of ["file:///etc/passwd", "http://example.com", "https://127.0.0.1/", "javascript:alert(1)", 42, null, { url: "https://example.com" }]) {
    const err = await page.evaluate((b) => (window as any).ezDesktop.openExternal(b).then(() => "resolved", (e: Error) => e.message), bad as never);
    expect(err, JSON.stringify(bad)).not.toBe("resolved");
  }
});

test("navigation, popups and path traversal are blocked; CSP is enforced", async () => {
  const before = page.url();
  await page.evaluate(() => { location.href = "https://example.com/"; });
  await page.waitForTimeout(800);
  expect(page.url()).toBe(before);
  await page.evaluate(() => { location.href = "file:///etc/passwd"; });
  await page.waitForTimeout(500);
  expect(page.url()).toBe(before);

  const popup = await page.evaluate(() => { const w = window.open("https://example.com/"); return w === null; });
  expect(popup).toBe(true);
  expect(app.windows()).toHaveLength(1);

  const probes = await page.evaluate(async () => {
    const status = async (u: string) => { try { return (await fetch(u)).status; } catch { return -1; } };
    return {
      traversal: [await status("ezc://app/../../../../etc/passwd"), await status("ezc://app/%2e%2e/%2e%2e/etc/passwd"), await status("ezc://app/..%2f..%2fpackage.json")],
      other: await status("ezc://other/index.html"),
      csp: (await fetch("ezc://app/index.html")).headers.get("content-security-policy"),
    };
  });
  for (const s of probes.traversal) expect([404, -1]).toContain(s);
  expect([404, -1]).toContain(probes.other);
  expect(probes.csp).toContain("script-src 'self'");
  expect(probes.csp).toContain("object-src 'none'");

  const inline = await page.evaluate(() => { (window as any).__pwned = false; const s = document.createElement("script"); s.textContent = "window.__pwned = true"; document.head.appendChild(s); return (window as any).__pwned; });
  expect(inline).toBe(false);                                       // inline script blocked by CSP

  const perm = await page.evaluate(async () => (await new Promise<string>((res) => navigator.geolocation.getCurrentPosition(() => res("granted"), (e) => res(`denied:${e.code}`)))));
  expect(perm).toMatch(/^denied/);
});

test("the sidecar rejects requests without the launch token and only listens on loopback", async () => {
  const url = await page.evaluate(() => (window as any).ezDesktop.localRuntime.url as string);
  const noAuth = await page.evaluate(async (u) => (await fetch(`${u}/v1/capabilities`)).status, url);
  expect(noAuth).toBe(401);
  const ok = await page.evaluate(async (u) => (await fetch(`${u}/v1/capabilities`, { headers: { Authorization: `Bearer ${(window as any).ezDesktop.localRuntime.token}` } })).status, url);
  expect(ok).toBe(200);
  const badOrigin = await fetch(`${url}/v1/health`, { headers: { Origin: "https://evil.example" } });
  expect(badOrigin.headers.get("access-control-allow-origin")).toBeNull();     // other web pages cannot read the converter's responses
});

test("a real conversion runs end to end inside the desktop shell", async () => {
  await page.reload();                                    // the previous test deliberately provoked blocked navigations
  await expect(page.getByTestId("runtime-status")).toContainText(/\d+ conversions/, { timeout: 30_000 });
  await page.getByTestId("file-input").setInputFiles(sample("png", "png"));
  const row = page.getByTestId("file-row");
  await expect(row).toContainText("PNG");
  await row.getByTestId("target-select").selectOption("webp");
  await expect(page.getByTestId("start-summary")).toContainText("1 of 1 ready");
  await page.getByTestId("start-button").click();
  await expect(page.getByTestId("results-summary")).toHaveText("1 converted", { timeout: 60_000 });
  await expect(page.getByTestId("result-name")).toHaveText("sample.webp");
  await expect(page.getByTestId("result-row")).toContainText("Verified");
});

test("quitting stops the converter process", async () => {
  const url = await page.evaluate(() => (window as any).ezDesktop.localRuntime.url as string);
  await app.close();
  await new Promise((r) => setTimeout(r, 800));
  await expect(fetch(`${url}/v1/health`)).rejects.toThrow();
});
