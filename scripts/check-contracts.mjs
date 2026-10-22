// Fails when docs/api/openapi.json or packages/contracts/src/schema.d.ts no longer match the backend.
import { execFileSync } from "node:child_process";
import { readFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const py = process.env.EZ_PYTHON ?? "python";
const live = execFileSync(py, ["-m", "ezconverter", "openapi"], { cwd: root, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
let bad = 0;
if (live !== readFileSync(resolve(root, "docs/api/openapi.json"), "utf8")) {
  console.error("docs/api/openapi.json is stale: run `pnpm gen:contracts`"); bad++;
}
const tmp = join(mkdtempSync(join(tmpdir(), "ezc-")), "schema.d.ts");
execFileSync(resolve(root, "packages/contracts/node_modules/.bin/openapi-typescript"), [resolve(root, "docs/api/openapi.json"), "-o", tmp],
  { cwd: resolve(root, "packages/contracts"), stdio: "ignore" });
if (readFileSync(tmp, "utf8") !== readFileSync(resolve(root, "packages/contracts/src/schema.d.ts"), "utf8")) {
  console.error("packages/contracts/src/schema.d.ts is stale: run `pnpm gen:contracts`"); bad++;
}
if (bad) process.exit(1);
console.log("contracts are in sync with the backend");
