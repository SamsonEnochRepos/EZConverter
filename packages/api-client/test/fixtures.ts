import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { UploadSource } from "@ezconverter/api-client";

const root = resolve(__dirname, "../../..");
const cache = resolve(root, "tests/.fixture-cache");

/** Real sample files come from the Python fixture generator (same files the backend conformance suite uses). */
export function sample(ext: string, name?: string): UploadSource {
  const fmt = { jpg: "jpeg", tif: "tiff" }[ext] ?? ext;
  const path = resolve(cache, `sample.${ext}`);
  if (!existsSync(path)) {
    execFileSync(process.env.EZ_PYTHON ?? "python", ["-c", `import sys; sys.path.insert(0, 'tests'); from fixtures.generate import fixture; fixture('${fmt}')`], { cwd: root });
  }
  const buf = readFileSync(path);
  return { name: name ?? `sample.${ext}`, size: buf.length, type: "application/octet-stream", blob: new Blob([buf]) };
}

export function junk(name = "mystery.dat"): UploadSource {
  const buf = Buffer.from(Array.from({ length: 2000 }, (_, i) => (i * 7) % 256));
  return { name, size: buf.length, blob: new Blob([buf]) };
}
