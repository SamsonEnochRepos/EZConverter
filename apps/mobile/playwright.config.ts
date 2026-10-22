import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig, devices } from "@playwright/test";

const root = resolve(__dirname, "../..");
const py = process.env.EZ_PYTHON ?? "python";
const chrome = ["/opt/pw-browsers/chromium-1194/chrome-linux/chrome"].find(existsSync);
export const BACKEND_PORT = 8792, WEB_PORT = 4174;
export const DATA_DIR = resolve(__dirname, "test-results/e2e-data");

export default defineConfig({
  testDir: "./e2e", timeout: 120_000, expect: { timeout: 30_000 }, workers: 1, reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${WEB_PORT}`, ...devices["Pixel 7"], launchOptions: { args: ["--no-sandbox"], ...(chrome ? { executablePath: chrome } : {}) }, acceptDownloads: true, trace: "retain-on-failure" },
  webServer: [
    {
      // A REMOTE-location runtime, like a phone's cloud converter: it refuses LOCAL_ONLY/AUTO and requires explicit consent.
      command: `${py} -m ezconverter serve --port ${BACKEND_PORT}`, cwd: root, url: `http://127.0.0.1:${BACKEND_PORT}/v1/health`, reuseExistingServer: false, timeout: 60_000,
      env: { PATH: process.env.PATH ?? "", EZ_DATA_DIR: DATA_DIR, EZ_LOCATION: "remote", EZ_AUTH_MODE: "token", EZ_API_KEYS: "phone-token:phone",
             EZ_ALLOWED_ORIGINS: `http://127.0.0.1:${WEB_PORT}`, EZ_RATE_LIMIT_PER_MIN: "100000" },
    },
    { command: `${py} -m http.server ${WEB_PORT} --bind 127.0.0.1 -d dist-web`, cwd: __dirname, url: `http://127.0.0.1:${WEB_PORT}`, reuseExistingServer: false, timeout: 30_000 },
  ],
});
