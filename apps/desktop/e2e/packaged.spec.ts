import { type Browser, type Page, chromium, expect, test } from "@playwright/test";
import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

/**
 * Drives the PACKAGED application (installer output) rather than the dev tree.
 *   EZ_DESKTOP_EXE=apps/desktop/release/win-unpacked/EZConverter.exe [EZ_SCREENSHOT_DIR=docs/screenshots] pnpm exec playwright test packaged
 * Playwright's Electron launcher needs the `--inspect` fuse, which the release build disables on purpose, so this attaches
 * through Chromium's remote-debugging port instead and leaves the shipped hardening untouched.
 */
const exe = process.env.EZ_DESKTOP_EXE;
const shots = process.env.EZ_SCREENSHOT_DIR;
const root = resolve(__dirname, "../../..");
const PORT = 9333;
const sample = (ext: string, fmt: string) => {
  const p = join(root, `tests/.fixture-cache/sample.${ext}`);
  if (!existsSync(p)) execFileSync(process.env.EZ_PYTHON ?? "python", ["-c", `import sys; sys.path.insert(0,'tests'); from fixtures.generate import fixture; fixture('${fmt}')`], { cwd: root });
  return p;
};

test.skip(!exe, "set EZ_DESKTOP_EXE to the packaged EZConverter executable");
test.describe.configure({ mode: "serial" });

let proc: ChildProcess;
let browser: Browser;
let page: Page;
let runtimeUrl = "";

async function shot(name: string) {
  if (!shots) return;
  mkdirSync(shots, { recursive: true });
  await page.screenshot({ path: join(shots, `desktop-${name}.png`) });
}

test.beforeAll(async () => {
  const userData = mkdtempSync(join(tmpdir(), "ez-packaged-"));
  const env = { ...process.env } as Record<string, string>;
  delete env.ELECTRON_RUN_AS_NODE;
  proc = spawn(exe!, [`--remote-debugging-port=${PORT}`, `--user-data-dir=${userData}`], { env, stdio: "ignore" });
  const end = Date.now() + 60_000;
  for (;;) {
    try { browser = await chromium.connectOverCDP(`http://127.0.0.1:${PORT}`); break; }
    catch (e) { if (Date.now() > end) throw e; await new Promise((r) => setTimeout(r, 500)); }
  }
  const ctx = browser.contexts()[0]!;
  page = ctx.pages().find((p) => p.url().startsWith("ezc://")) ?? (await ctx.waitForEvent("page"));
  await page.waitForLoadState("domcontentloaded");
  await page.setViewportSize({ width: 1180, height: 820 });
  runtimeUrl = await page.evaluate(() => (window as any).ezDesktop.localRuntime.url as string);
});
test.afterAll(async () => { await browser?.close().catch(() => undefined); proc?.kill(); });

test("the packaged renderer is isolated and talks only to its own loopback converter", async () => {
  expect(page.url()).toBe("ezc://app/index.html");
  const leaked = await page.evaluate(() => ({ require: typeof (window as any).require, process: typeof (window as any).process, Buffer: typeof (window as any).Buffer }));
  expect(leaked).toEqual({ require: "undefined", process: "undefined", Buffer: "undefined" });
  const info = await page.evaluate(() => (window as any).ezDesktop.getAppInfo());
  expect(info).toMatchObject({ platform: process.platform, version: expect.any(String) });
  expect(runtimeUrl).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/);
  const csp = await page.evaluate(async () => (await fetch("ezc://app/index.html")).headers.get("content-security-policy"));
  expect(csp).toContain("script-src 'self'");
  const inline = await page.evaluate(() => { (window as any).__pwned = false; const s = document.createElement("script"); s.textContent = "window.__pwned = true"; document.head.appendChild(s); return (window as any).__pwned; });
  expect(inline).toBe(false);
});

test("the bundled converter requires its launch token and advertises the bundled engines", async () => {
  const noAuth = await page.evaluate(async (u) => (await fetch(`${u}/v1/capabilities`)).status, runtimeUrl);
  expect(noAuth).toBe(401);
  const caps = await page.evaluate(async (u) => {
    const r = await fetch(`${u}/v1/capabilities`, { headers: { Authorization: `Bearer ${(window as any).ezDesktop.localRuntime.token}` } });
    return ((await r.json()).capabilities as { id: string }[]).map((c) => c.id);
  }, runtimeUrl);
  expect(caps.length).toBeGreaterThan(200);
  for (const id of ["pillow.png->webp", "ffmpeg.mp3->opus", "pandoc.md->html", "poppler.pdf->png"]) expect(caps, id).toContain(id);
});

test("convert an image and an audio file in the packaged app", async () => {
  await expect(page.getByTestId("runtime-status")).toContainText(/\d+ conversions/, { timeout: 30_000 });
  await shot("1-home");
  await page.getByTestId("file-input").setInputFiles([sample("png", "png"), sample("mp3", "mp3")]);
  const rows = page.getByTestId("file-row");
  await expect(rows).toHaveCount(2);
  await rows.filter({ hasText: "sample.png" }).getByTestId("target-select").selectOption("webp");
  await rows.filter({ hasText: "sample.mp3" }).getByTestId("target-select").selectOption("opus");
  await expect(page.getByTestId("start-summary")).toContainText("2 of 2 ready");
  await shot("2-files");
  await page.getByTestId("start-button").click();
  await expect(page.getByTestId("results-summary")).toHaveText("2 converted", { timeout: 120_000 });
  for (const row of await page.getByTestId("result-row").all()) await expect(row).toContainText("Verified");
  await shot("3-results");
});

test("a downloaded result is a real file", async () => {
  const ok = await page.evaluate(async (u) => {
    const tok = (window as any).ezDesktop.localRuntime.token as string;
    const jobs = await (await fetch(`${u}/v1/jobs`, { headers: { Authorization: `Bearer ${tok}` } })).json();
    const list = (jobs.items ?? jobs.jobs ?? jobs) as { id: string; state: string }[];
    const done = list.find((j) => j.state === "SUCCEEDED");
    if (!done) return "no succeeded job";
    const res = await (await fetch(`${u}/v1/jobs/${done.id}/result`, { headers: { Authorization: `Bearer ${tok}` } })).json();
    const bytes = new Uint8Array(await (await fetch(`${u}${res.download_path}`, { headers: { Authorization: `Bearer ${tok}` } })).arrayBuffer());
    return bytes.length > 100 ? "ok" : "empty";
  }, runtimeUrl);
  expect(ok).toBe("ok");
});

test("quitting the app stops the converter", async () => {
  const cdp = await browser.newBrowserCDPSession();
  await cdp.send("Browser.close").catch(() => undefined);
  await new Promise((r) => setTimeout(r, 2500));
  await expect(fetch(`${runtimeUrl}/v1/health`)).rejects.toThrow();
});
