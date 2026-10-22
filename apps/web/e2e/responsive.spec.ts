import { expect, test } from "@playwright/test";
import { configure, samplePath } from "./helpers";

test("phone viewport: no horizontal scroll and touch-sized controls through the whole flow", async ({ page }) => {
  await configure(page);
  await page.goto("/");
  const noOverflow = async () => expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await noOverflow();
  await page.getByTestId("file-input").setInputFiles(samplePath("docx"));
  const sel = page.getByTestId("file-row").getByTestId("target-select");
  await expect(sel).toBeEnabled({ timeout: 30_000 });
  await sel.selectOption("pdf");
  await noOverflow();
  const heights = await page.getByRole("button").evaluateAll((els) => els.filter((e) => (e as HTMLElement).offsetParent && e.tagName !== "INPUT").map((e) => e.getBoundingClientRect().height));
  expect(Math.min(...heights)).toBeGreaterThanOrEqual(40);                         // touch targets
  await page.getByTestId("start-button").click();
  await expect(page.getByTestId("results-summary")).toHaveText("1 converted", { timeout: 90_000 });
  await noOverflow();
});
