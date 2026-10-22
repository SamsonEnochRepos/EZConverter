import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig, devices } from "@playwright/test";

const root = resolve(import.meta.dirname, "../..");
const py = process.env.EZ_PYTHON ?? "python";
// Use the pre-installed Chromium when present (the sandbox forbids downloading browsers).
const chrome = ["/opt/pw-browsers/chromium-1194/chrome-linux/chrome"].find(existsSync);
const BACKEND_PORT = 8791, WEB_PORT = 4173;

export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
    launchOptions: chrome ? { executablePath: chrome, args: ["--no-sandbox"] } : { args: ["--no-sandbox"] },
    acceptDownloads: true,
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], launchOptions: chrome ? { executablePath: chrome, args: ["--no-sandbox"] } : {} } },
    { name: "mobile-viewport", use: { ...devices["Pixel 7"], launchOptions: chrome ? { executablePath: chrome, args: ["--no-sandbox"] } : {} }, testMatch: /responsive\.spec\.ts/ },
  ],
  webServer: [
    {
      command: `${py} -m ezconverter serve --port ${BACKEND_PORT}`,
      cwd: root,
      url: `http://127.0.0.1:${BACKEND_PORT}/v1/health`,
      reuseExistingServer: false,
      env: { PATH: process.env.PATH ?? "", EZ_DATA_DIR: resolve(root, "apps/web/test-results/e2e-data"), EZ_AUTH_MODE: "token", EZ_API_KEYS: "e2e-token:e2e",
             EZ_ALLOWED_ORIGINS: `http://127.0.0.1:${WEB_PORT}`, EZ_RATE_LIMIT_PER_MIN: "100000" },
      timeout: 60_000,
    },
    { command: `pnpm exec vite preview --port ${WEB_PORT} --strictPort --host 127.0.0.1`, cwd: import.meta.dirname, url: `http://127.0.0.1:${WEB_PORT}`, reuseExistingServer: false, timeout: 60_000 },
  ],
});
