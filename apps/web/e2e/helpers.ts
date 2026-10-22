import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import { execFileSync } from "node:child_process";

const root = resolve(import.meta.dirname, "../../..");
export const BACKEND = "http://127.0.0.1:8791";

/** Real input files from the same generator the backend's conformance suite uses. */
export function samplePath(fmt: string): string {
  const ext = { jpeg: "jpg", tiff: "tif", tgz: "tar.gz" }[fmt] ?? fmt;
  const p = resolve(root, `tests/.fixture-cache/sample.${ext}`);
  if (!existsSync(p)) execFileSync(process.env.EZ_PYTHON ?? "python", ["-c", `import sys; sys.path.insert(0,'tests'); from fixtures.generate import fixture; fixture('${fmt}')`], { cwd: root });
  return p;
}
export const read = (p: string) => readFileSync(p);

/** Pre-seeds the saved settings so the app talks to the e2e backend (no special code paths in the app). */
export async function configure(page: Page, extra: Record<string, unknown> = {}) {
  await page.addInitScript((s) => { if (!localStorage.getItem("ez.settings")) localStorage.setItem("ez.settings", JSON.stringify(s)); },
    { localUrl: BACKEND, localToken: "e2e-token", ...extra });
}
