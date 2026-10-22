/**
 * Pure security helpers for the Electron shell. Nothing here touches Electron APIs, so every rule is unit-tested.
 *
 * Threat model summary: the renderer is a web page that processes attacker-influenced file *names and metadata*
 * (never file contents), talks to a loopback sidecar with a per-launch random token, and must not be able to reach
 * Node, the filesystem, arbitrary hosts via navigation, or the user's OS beyond the narrow IPC below.
 */
import { isAbsolute, normalize, resolve, sep } from "node:path";

export const APP_SCHEME = "ezc";
export const APP_ORIGIN = `${APP_SCHEME}://app`;

/** Maps an `ezc://app/...` request path to a file inside `root`, or null when it would escape it. */
export function resolveWithin(root: string, requestPath: string): string | null {
  let decoded: string;
  try { decoded = decodeURIComponent(requestPath); } catch { return null; }
  if (decoded.includes("\0") || decoded.includes("\\")) return null;
  const rel = normalize(decoded.replace(/^\/+/, "") || "index.html");
  if (isAbsolute(rel) || rel === ".." || rel.startsWith(`..${sep}`)) return null;
  const abs = resolve(root, rel);
  const base = resolve(root);
  return abs === base || abs.startsWith(base + sep) ? abs : null;
}

export function buildCsp(sidecarOrigin: string): string {
  return [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    `connect-src 'self' ${sidecarOrigin} https:`,
    "object-src 'none'", "base-uri 'none'", "frame-src 'none'", "form-action 'none'", "frame-ancestors 'none'",
  ].join("; ");
}

/**
 * True only for ezc://app/... Compared by protocol + host rather than `URL.origin`, because `.origin` is the
 * string "null" for non-special schemes in some URL implementations but `ezc://app` in Chromium.
 */
function isAppUrl(raw: string): boolean {
  try {
    const u = new URL(raw);
    return u.protocol === `${APP_SCHEME}:` && u.hostname === "app" && u.port === "" && !u.username && !u.password;
  } catch { return false; }
}

/** The renderer may only ever be at the app origin; any other navigation is blocked. */
export function isAllowedNavigation(url: string): boolean { return isAppUrl(url); }

/** Links opened in the user's browser: https only, no credentials, no loopback/private targets. */
export function isAllowedExternalUrl(raw: unknown): raw is string {
  if (typeof raw !== "string" || raw.length > 2048) return false;
  let u: URL;
  try { u = new URL(raw); } catch { return false; }
  if (u.protocol !== "https:" || u.username || u.password) return false;
  const h = u.hostname.toLowerCase();
  return !(h === "localhost" || h.endsWith(".localhost") || /^(127\.|10\.|192\.168\.|169\.254\.)/.test(h) || /^172\.(1[6-9]|2\d|3[01])\./.test(h) || h === "[::1]");
}

/** Downloads may only originate from the local sidecar or an https converter the user configured. */
export function isAllowedDownloadUrl(url: string, sidecarOrigin: string): boolean {
  try {
    const u = new URL(url);
    return u.origin === sidecarOrigin || u.protocol === "https:";
  } catch { return false; }
}

/** IPC callers must be the app's own page, never an embedded frame or a navigated-away document. */
export function isTrustedSender(frameUrl: string | undefined): boolean {
  return !!frameUrl && isAppUrl(frameUrl);
}

export function parseReadyLine(line: string): { host: string; port: number } | null {
  const m = /^EZCONVERTER_READY (\{.*\})$/.exec(line.trim());
  if (!m) return null;
  try {
    const j = JSON.parse(m[1]!) as { host?: unknown; port?: unknown };
    if (typeof j.port === "number" && j.port > 0 && j.port < 65536 && (j.host === "127.0.0.1" || j.host === "::1")) return { host: j.host, port: j.port };
  } catch { /* fallthrough */ }
  return null;
}

// ---- IPC payload validation (one function per channel; unknown shapes are rejected, never coerced)

export class IpcValidationError extends Error {}

export function validateOpenExternal(payload: unknown): string {
  if (!isAllowedExternalUrl(payload)) throw new IpcValidationError("ez:open-external expects an https URL to a public host");
  return payload;
}

export function validateNoPayload(payload: unknown): void {
  if (payload !== undefined && payload !== null) throw new IpcValidationError("this channel takes no arguments");
}

export const IPC_CHANNELS = ["ez:app-info", "ez:open-external"] as const;
export type IpcChannel = (typeof IPC_CHANNELS)[number];
