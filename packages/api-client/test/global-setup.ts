/**
 * Starts the real Python backend (sidecar mode, token auth) once for the test run.
 * Tests talk to it over HTTP, so these are integration tests of the TypeScript client and controller
 * against the actual conversion engines. They are skipped (loudly) when the Python environment is missing.
 */
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import type { TestProject } from "vitest/node";

let child: ChildProcess | undefined;
let dataDir = "";

export default async function setup(project: TestProject) {
  const root = resolve(__dirname, "../../..");
  const py = process.env.EZ_PYTHON ?? "python";
  if (spawnSync(py, ["--version"]).status !== 0) { console.warn(`[backend] ${py} not found: integration tests will be skipped`); return; }
  dataDir = mkdtempSync(join(tmpdir(), "ez-ts-"));
  child = spawn(py, ["-m", "ezconverter", "serve", "--sidecar", "--port", "0"], {
    cwd: root,
    env: { PATH: process.env.PATH ?? "", EZ_DATA_DIR: dataDir, EZ_AUTH_MODE: "token", EZ_API_KEYS: "test-token:tester,other-token:other", EZ_ALLOWED_ORIGINS: "*",
           EZ_RATE_LIMIT_PER_MIN: "100000", EZ_RETENTION_HOURS: "1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const url = await new Promise<string>((res, rej) => {
    const t = setTimeout(() => rej(new Error("backend did not start")), 30_000);
    child!.stdout!.on("data", (b: Buffer) => {
      const m = /EZCONVERTER_READY (\{.*\})/.exec(b.toString());
      if (m) { clearTimeout(t); const j = JSON.parse(m[1]!); res(`http://127.0.0.1:${j.port}`); }
    });
    child!.on("exit", (c) => rej(new Error(`backend exited early (${c})`)));
  });
  project.provide("backendUrl", url);
  return () => { child?.kill("SIGTERM"); rmSync(dataDir, { recursive: true, force: true }); };
}

declare module "vitest" {
  export interface ProvidedContext { backendUrl: string }
}
