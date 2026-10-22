import { type ChildProcess, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, mkdirSync } from "node:fs";
import { delimiter, join } from "node:path";
import { APP_ORIGIN, parseReadyLine } from "./security";

export interface SidecarInfo { url: string; token: string; origin: string }
export interface SidecarOptions { dataDir: string; repoRoot: string; resourcesPath?: string; log?: (m: string) => void }

/** Locates the converter: explicit override, then a packaged binary, then the active conda env (EZ), then `python` on PATH. */
export function resolveSidecarCommand(o: SidecarOptions, env: NodeJS.ProcessEnv = process.env): { cmd: string; args: string[] } {
  const args = ["serve", "--sidecar", "--port", "0"];
  if (env.EZ_SIDECAR_BIN) return { cmd: env.EZ_SIDECAR_BIN, args };
  if (o.resourcesPath) {
    const bin = join(o.resourcesPath, "sidecar", process.platform === "win32" ? "ezconverter-server.exe" : "ezconverter-server");
    if (existsSync(bin)) return { cmd: bin, args };
  }
  const py = env.EZ_PYTHON ?? (env.CONDA_PREFIX ? join(env.CONDA_PREFIX, process.platform === "win32" ? "python.exe" : "bin/python") : "python");
  return { cmd: py, args: ["-m", "ezconverter", ...args] };
}

export class Sidecar {
  private child?: ChildProcess;
  private stopping = false;
  private restarts = 0;
  info?: SidecarInfo;
  constructor(private o: SidecarOptions) {}

  /** Starts the converter bound to loopback with a fresh random token that only this app instance knows. */
  async start(): Promise<SidecarInfo> {
    const token = randomBytes(32).toString("base64url");
    const { cmd, args } = resolveSidecarCommand(this.o);
    // Packaged builds run from the (user-owned) data dir, never from the install directory, so no stray .env is picked up.
    mkdirSync(this.o.dataDir, { recursive: true });
    const engineDir = this.o.resourcesPath ? join(this.o.resourcesPath, "engines") : undefined;
    const bundledEngines = engineDir !== undefined && existsSync(engineDir);
    const child = spawn(cmd, args, {
      cwd: this.o.resourcesPath ? this.o.dataDir : this.o.repoRoot,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
      env: {   // minimal environment: no inherited secrets
        PATH: bundledEngines ? `${engineDir}${delimiter}${process.env.PATH ?? ""}` : process.env.PATH ?? "",
        ...(process.env.SYSTEMROOT ? { SYSTEMROOT: process.env.SYSTEMROOT } : {}),
        ...(bundledEngines ? { EZ_ENGINE_DIR: engineDir } : {}), HOME: process.env.HOME ?? "", LANG: process.env.LANG ?? "C.UTF-8",
        EZ_DATA_DIR: this.o.dataDir, EZ_AUTH_MODE: "token", EZ_API_KEYS: `${token}:desktop`, EZ_LOCATION: "local",
        EZ_ALLOWED_ORIGINS: APP_ORIGIN, EZ_MAX_CONCURRENCY: "2",
        ...(process.env.EZ_EXTRA_ENGINES ? { EZ_EXTRA_ENGINES: process.env.EZ_EXTRA_ENGINES } : {}),
      },
    });
    this.child = child;
    child.stderr?.on("data", (b: Buffer) => this.o.log?.(b.toString().trimEnd()));
    const ready = await new Promise<{ host: string; port: number }>((resolve, reject) => {
      const t = setTimeout(() => reject(new Error("The converter did not start within 30 seconds.")), 30_000);
      let buf = "";
      child.stdout?.on("data", (b: Buffer) => {
        buf += b.toString();
        for (const line of buf.split("\n")) { const r = parseReadyLine(line); if (r) { clearTimeout(t); resolve(r); return; } }
      });
      child.once("error", (e) => { clearTimeout(t); reject(e); });
      child.once("exit", (code) => { clearTimeout(t); reject(new Error(`The converter exited during startup (code ${code}).`)); });
    });
    const url = `http://${ready.host === "::1" ? "[::1]" : ready.host}:${ready.port}`;
    this.info = { url, token, origin: url };
    child.on("exit", (code) => { if (!this.stopping) this.o.log?.(`converter exited unexpectedly (${code})`); });
    return this.info;
  }

  async stop(): Promise<void> {
    this.stopping = true;
    const c = this.child;
    if (!c || c.exitCode !== null) return;
    await new Promise<void>((res) => {
      const t = setTimeout(() => { c.kill("SIGKILL"); res(); }, 4000);
      c.once("exit", () => { clearTimeout(t); res(); });
      c.kill("SIGTERM");
    });
  }
}
