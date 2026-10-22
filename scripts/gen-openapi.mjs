// Regenerates docs/api/openapi.json from the FastAPI app (the single source of truth for the contract).
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const py = process.env.EZ_PYTHON ?? "python";
const out = execFileSync(py, ["-m", "ezconverter", "openapi"], { cwd: root, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
writeFileSync(resolve(root, "docs/api/openapi.json"), out);
console.log("wrote docs/api/openapi.json");
