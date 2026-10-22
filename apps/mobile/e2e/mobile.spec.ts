import { expect, test, type Page } from "@playwright/test";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { join, resolve } from "node:path";
import { BACKEND_PORT, DATA_DIR } from "../playwright.config";

const root = resolve(__dirname, "../../..");
const BACKEND = `http://127.0.0.1:${BACKEND_PORT}`;
const sample = (ext: string, fmt = ext) => {
  const p = join(root, `tests/.fixture-cache/sample.${ext}`);
  if (!existsSync(p)) execFileSync(process.env.EZ_PYTHON ?? "python", ["-c", `import sys; sys.path.insert(0,'tests'); from fixtures.generate import fixture; fixture('${fmt}')`], { cwd: root });
  return p;
};
const uploadsOnServer = () => (existsSync(join(DATA_DIR, "uploads")) ? readdirSync(join(DATA_DIR, "uploads")).length : 0);

async function seed(page: Page, settings: Record<string, unknown>) {
  await page.addInitScript((s) => {
    if (localStorage.getItem("seeded")) return;
    localStorage.setItem("seeded", "1");
    localStorage.setItem("ez.settings.mobile", JSON.stringify(s));
    localStorage.setItem("secret.remoteToken", "phone-token");
  }, { remoteUrl: BACKEND, defaultPolicy: "AUTO", allowRemote: false, theme: "light", largeText: false, reduceMotion: false, ...settings });
}
async function pick(page: Page, path: string) {
  const [chooser] = await Promise.all([page.waitForEvent("filechooser"), page.getByTestId("choose-files").click()]);
  await chooser.setFiles(path);
}

test("shows converter status and the consent card before any upload", async ({ page }) => {
  await seed(page, {});
  await page.goto("/");
  await expect(page.getByTestId("runtime-status")).toContainText(/\d+ verified conversions/);
  await expect(page.getByText("Allow uploading?")).toBeVisible();
});

test("without consent nothing is uploaded, and the reason is explained", async ({ page }) => {
  await seed(page, { allowRemote: false });
  await page.goto("/");
  await expect(page.getByTestId("runtime-status")).toContainText("verified conversions");
  const before = uploadsOnServer();
  await pick(page, sample("png"));
  await expect(page.getByText(/not uploaded anywhere/i)).toBeVisible();
  await expect(page.getByText("No converter available")).toBeVisible();
  expect(uploadsOnServer()).toBe(before);                           // the server never received a byte
});

test("with consent: pick, identify, convert, verify and export a real result", async ({ page }) => {
  await seed(page, { allowRemote: true });
  await page.goto("/");
  await expect(page.getByTestId("runtime-status")).toContainText("verified conversions");
  await pick(page, sample("png"));
  const card = page.getByTestId("file-card");
  await expect(card).toContainText("PNG");
  await page.getByTestId("target-button").click();
  await page.getByRole("menuitem", { name: /WebP/ }).click();
  await expect(page.getByTestId("route-note")).toContainText("uploaded");            // remote use is stated, not hidden
  await expect(page.getByTestId("start-button")).toBeEnabled();
  await page.getByTestId("start-button").click();
  await expect(page.getByTestId("results-summary")).toHaveText("1 converted", { timeout: 60_000 });
  await expect(page.getByTestId("result-name")).toHaveText("sample.webp");
  await expect(page.getByTestId("result-card")).toContainText("Verified");
  const [dl] = await Promise.all([page.waitForEvent("download"), page.getByTestId("share-button").click()]);
  expect(dl.suggestedFilename()).toBe("sample.webp");
  expect(readFileSync((await dl.path())!).subarray(0, 4).toString()).toBe("RIFF");
});

test("Local only is refused on a phone: it explains and uploads nothing, even with consent on", async ({ page }) => {
  await seed(page, { allowRemote: true, defaultPolicy: "LOCAL_ONLY" });
  await page.goto("/");
  await expect(page.getByTestId("runtime-status")).toContainText("verified conversions");
  const before = uploadsOnServer();
  await pick(page, sample("png"));
  await expect(page.getByText(/not uploaded anywhere/i)).toBeVisible();
  await expect(page.getByText(/Local only/).first()).toBeVisible();
  await page.waitForTimeout(500);
  expect(uploadsOnServer()).toBe(before);                           // strictly nothing reached the server
});

test("tabs, settings test-connection and history work on a phone viewport", async ({ page }) => {
  await seed(page, { allowRemote: true });
  await page.goto("/");
  await page.getByTestId("tab-settings").click();
  await page.getByTestId("test-connection").click();
  await expect(page.getByText(/Connected: \d+ verified conversions/)).toBeVisible();
  await expect(page.getByTestId("retention-note")).toContainText("deleted automatically after 24 hours");
  await page.getByTestId("tab-history").click();
  await expect(page.getByText("History").first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
});
