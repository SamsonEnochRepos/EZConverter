import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { configure, samplePath } from "./helpers";

test.beforeEach(async ({ page }) => { await configure(page); });

test("single file: select, inspect, convert, verify and download a real result", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("runtime-status")).toContainText("This device");
  await expect(page.getByTestId("runtime-status")).toContainText(/\d+ conversions/);

  await page.getByTestId("file-input").setInputFiles(samplePath("png"));
  const row = page.getByTestId("file-row");
  await expect(row).toContainText("PNG");                                         // identified from content
  await expect(row.getByTestId("target-select")).toBeEnabled();
  await row.getByTestId("target-select").selectOption("jpeg");
  await expect(row).toContainText("Lossy");
  await expect(row.getByRole("list", { name: "Notes about this conversion" })).toContainText(/transparent/i);

  await row.getByTestId("settings-toggle").click();
  await row.getByLabel("max width").fill("48");
  // editing a setting must immediately make the item un-startable until the new plan arrives (no stale-options race)
  await expect(page.getByTestId("start-button")).toBeDisabled();
  await expect(page.getByTestId("start-summary")).toContainText("1 of 1 ready");

  await page.getByTestId("start-button").click();
  await expect(page.getByTestId("results-summary")).toHaveText("1 converted", { timeout: 60_000 });
  const result = page.getByTestId("result-row");
  await expect(result.getByTestId("result-name")).toHaveText("sample.jpg");
  await expect(result).toContainText("Verified");

  const [download] = await Promise.all([page.waitForEvent("download"), result.getByTestId("download-button").click()]);
  expect(download.suggestedFilename()).toBe("sample.jpg");
  const bytes = readFileSync((await download.path())!);
  expect([...bytes.subarray(0, 3)]).toEqual([0xff, 0xd8, 0xff]);                  // a real JPEG
  // the resize option really took effect: JPEG SOF holds height then width, source is 96x64 -> 48x32
  const sof = bytes.indexOf(Buffer.from([0xff, 0xc0])) >= 0 ? bytes.indexOf(Buffer.from([0xff, 0xc0])) : bytes.indexOf(Buffer.from([0xff, 0xc2]));
  expect([bytes.readUInt16BE(sof + 5), bytes.readUInt16BE(sof + 7)]).toEqual([32, 48]);   // [height, width]
});

test("batch: mixed valid, unsupported and duplicate files report independent outcomes and a ZIP", async ({ page }) => {
  await page.goto("/");
  const file = (p: string) => ({ name: p.split("/").pop()!, mimeType: "application/octet-stream", buffer: readFileSync(p) });
  await page.getByTestId("file-input").setInputFiles([
    file(samplePath("json")), file(samplePath("csv")), file(samplePath("png")), file(samplePath("mp3")),
    { name: "mystery.dat", mimeType: "application/octet-stream", buffer: Buffer.from(Array.from({ length: 1500 }, (_, i) => (i * 7) % 256)) },
    { name: "copy-of-sample.json", mimeType: "application/json", buffer: readFileSync(samplePath("json")) },
  ]);
  const rows = page.getByTestId("file-row");
  await expect(rows).toHaveCount(6);
  await expect(page.getByTestId("start-summary")).not.toContainText("Reading", { timeout: 60_000 });
  await expect(rows.filter({ hasText: "mystery.dat" })).toHaveAttribute("data-status", "ineligible");
  await expect(rows.filter({ hasText: "copy-of-sample.json" })).toHaveAttribute("data-status", "duplicate");

  await rows.filter({ hasText: "sample.json" }).first().getByTestId("target-select").selectOption("yaml");
  await rows.filter({ hasText: "sample.csv" }).getByTestId("target-select").selectOption("yaml");   // both -> sample.yaml: name collision
  await rows.filter({ hasText: "sample.png" }).getByTestId("target-select").selectOption("webp");
  await rows.filter({ hasText: "sample.mp3" }).getByTestId("target-select").selectOption("opus");
  await expect(page.getByTestId("start-button")).toContainText("Convert 4 files");
  await expect(page.getByTestId("start-summary")).toContainText("2 will be skipped");

  await page.getByTestId("start-button").click();
  await expect(page.getByTestId("results-summary")).toContainText("4 converted", { timeout: 90_000 });
  await expect(page.getByTestId("results-summary")).toContainText("2 skipped");
  await expect(page.getByTestId("result-row")).toHaveCount(4);
  const names = await page.getByTestId("result-name").allTextContents();
  expect(names.sort()).toEqual(["sample.opus", "sample.webp", "sample.yaml", "sample.yaml"]);

  const [zip] = await Promise.all([page.waitForEvent("download"), page.getByTestId("zip-button").click()]);
  const buf = readFileSync((await zip.path())!);
  expect(buf.subarray(0, 2).toString()).toBe("PK");
  const text = buf.toString("latin1");
  expect(text).toContain("manifest.json");
  expect(text).toContain("sample (1).yaml");                                    // collision resolved, not overwritten
  expect(text).not.toContain("mystery");

  await page.getByTestId("delete-now").click();
  await expect(page.getByTestId("dropzone")).toBeVisible();
});

test("an impossible conversion is explained, not offered", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("file-input").setInputFiles(samplePath("mp3"));
  const sel = page.getByTestId("file-row").getByTestId("target-select");
  await expect(sel).toBeEnabled({ timeout: 30_000 });
  const options = await sel.locator("option").allTextContents();
  expect(options.join("|")).toMatch(/Ogg|Opus|FLAC/);
  expect(options.join("|")).not.toMatch(/Word|PDF|PNG/);
});

test("Local only mode: a conversion the device cannot do uploads nothing", async ({ page }) => {
  await configure(page, { remoteUrl: "http://127.0.0.1:9", remoteToken: "x" });    // unreachable cloud
  await page.goto("/");
  await page.getByTestId("file-input").setInputFiles(samplePath("png"));
  await expect(page.getByTestId("file-row")).toContainText("PNG", { timeout: 30_000 });
  await page.getByTestId("policy-select").selectOption("LOCAL_ONLY");
  await expect(page.getByTestId("file-row").getByTestId("route-note")).toHaveCount(0);
  await page.getByTestId("file-row").getByTestId("target-select").selectOption("jpeg");
  await expect(page.getByTestId("route-note")).toContainText("not uploaded");
});

test("settings and history pages work and persist", async ({ page }) => {
  await page.goto("/#/settings");
  await expect(page.getByTestId("runtime-local")).toContainText("Online");
  await page.getByTestId("runtime-local").getByRole("button", { name: "Test connection" }).click();
  await expect(page.getByTestId("runtime-local")).toContainText(/Connected: \d+ verified conversions/);
  await expect(page.getByTestId("retention-note")).toContainText("deleted automatically after 24 hours");
  await page.getByLabel("Theme").selectOption("dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.goto("/#/history");
  await expect(page.getByRole("heading", { name: "History" })).toBeVisible();
});

test("an unreachable converter is reported plainly", async ({ page }) => {
  await configure(page, { localUrl: "http://127.0.0.1:9" });
  await page.addInitScript(() => localStorage.setItem("ez.settings", JSON.stringify({ localUrl: "http://127.0.0.1:9", localToken: "x" })));
  await page.goto("/");
  await expect(page.getByRole("alert").first()).toContainText("No converter is reachable");
});

