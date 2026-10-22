import { describe, expect, it } from "vitest";
import { join } from "node:path";
import {
  IpcValidationError, buildCsp, isAllowedDownloadUrl, isAllowedExternalUrl, isAllowedNavigation, isTrustedSender, parseReadyLine,
  resolveWithin, validateNoPayload, validateOpenExternal,
} from "../src/security";
import { resolveSidecarCommand } from "../src/sidecar";

const root = "/opt/app/web";

describe("ezc:// path resolution", () => {
  it("serves files inside the web root and defaults to index.html", () => {
    expect(resolveWithin(root, "/")).toBe(join(root, "index.html"));
    expect(resolveWithin(root, "/assets/index-abc.js")).toBe(join(root, "assets/index-abc.js"));
    expect(resolveWithin(root, "/a/./b/../c.css")).toBe(join(root, "a/c.css"));
  });
  it.each([
    "/../../etc/passwd", "/..%2f..%2fetc/passwd", "/%2e%2e/%2e%2e/etc/passwd", "/assets/../../secret", "/a%00b", "/..\\..\\x", "/%5c..%5csecret", "/%ZZ",
    "//etc/passwd/../../..", "/assets/%2e%2e%2f%2e%2e%2fsecret",
  ])("rejects traversal attempt %s", (p) => {
    const r = resolveWithin(root, p);
    expect(r === null || r.startsWith(root + "/")).toBe(true);
    if (r) expect(r).not.toContain("..");
  });
  it("never resolves outside the root for any input", () => {
    for (const p of ["/..", "/../", "/../web2/x", "/.%2e/x", "/%2e%2e"]) {
      const r = resolveWithin(root, p);
      expect(r === null || r === root || r.startsWith(root + "/")).toBe(true);
    }
  });
});

describe("navigation, links and downloads", () => {
  it("allows only the app origin for navigation", () => {
    expect(isAllowedNavigation("ezc://app/index.html")).toBe(true);
    for (const u of ["https://evil.example/", "file:///etc/passwd", "http://127.0.0.1:8787/", "ezc://other/", "ezc://app@evil.example/", "ezc://app:99/", "ezc://user:pw@app/", "javascript:alert(1)", "data:text/html,x", "about:blank", ""]) expect(isAllowedNavigation(u)).toBe(false);
  });
  it("opens only public https links externally", () => {
    expect(isAllowedExternalUrl("https://example.com/docs")).toBe(true);
    for (const u of ["http://example.com", "file:///etc/passwd", "javascript:alert(1)", "https://user:pw@example.com", "https://localhost/x", "https://127.0.0.1/", "https://192.168.1.5/", "https://10.0.0.1/", "https://172.20.1.1/", "https://169.254.169.254/latest", "ssh://x", "x".repeat(3000), 42, null, undefined, {}, ["https://a.com"]])
      expect(isAllowedExternalUrl(u as unknown)).toBe(false);
  });
  it("restricts downloads to the sidecar or https converters", () => {
    const origin = "http://127.0.0.1:5000";
    expect(isAllowedDownloadUrl("http://127.0.0.1:5000/v1/artifacts/x/download?sig=1", origin)).toBe(true);
    expect(isAllowedDownloadUrl("https://cloud.example/v1/artifacts/x/download", origin)).toBe(true);
    for (const u of ["http://127.0.0.1:6000/x", "http://evil.example/x", "file:///etc/passwd", "ftp://x/y", "not a url"]) expect(isAllowedDownloadUrl(u, origin)).toBe(false);
  });
  it("builds a CSP that forbids scripts, frames and plugins from elsewhere", () => {
    const csp = buildCsp("http://127.0.0.1:5000");
    expect(csp).toContain("script-src 'self'");
    expect(csp).toContain("connect-src 'self' http://127.0.0.1:5000 https:");
    for (const d of ["object-src 'none'", "frame-src 'none'", "base-uri 'none'", "frame-ancestors 'none'"]) expect(csp).toContain(d);
    expect(csp).not.toMatch(/script-src[^;]*unsafe-(inline|eval)/);
  });
});

describe("IPC validation", () => {
  it("trusts only frames served from the app origin", () => {
    expect(isTrustedSender("ezc://app/index.html")).toBe(true);
    for (const u of [undefined, "", "https://evil.example/", "file:///x.html", "ezc://evil/", "http://127.0.0.1:8787/", "ezc://app.evil.example/"]) expect(isTrustedSender(u)).toBe(false);
  });
  it("accepts a valid open-external payload and rejects every other shape", () => {
    expect(validateOpenExternal("https://example.com")).toBe("https://example.com");
    for (const bad of [undefined, null, 1, {}, [], "http://example.com", "file:///etc/passwd", { url: "https://example.com" }, "https://127.0.0.1"])
      expect(() => validateOpenExternal(bad)).toThrow(IpcValidationError);
  });
  it("rejects payloads on channels that take none", () => {
    expect(() => validateNoPayload(undefined)).not.toThrow();
    expect(() => validateNoPayload({ a: 1 })).toThrow(IpcValidationError);
    expect(() => validateNoPayload("x")).toThrow(IpcValidationError);
  });
});

describe("sidecar handshake and discovery", () => {
  it("parses only well-formed loopback READY lines", () => {
    expect(parseReadyLine('EZCONVERTER_READY {"host": "127.0.0.1", "port": 4321, "location": "local"}')).toEqual({ host: "127.0.0.1", port: 4321 });
    for (const l of ['EZCONVERTER_READY {"host": "0.0.0.0", "port": 1}', 'EZCONVERTER_READY {"host": "evil.example", "port": 80}', 'EZCONVERTER_READY {"host":"127.0.0.1","port":0}',
      'EZCONVERTER_READY {"host":"127.0.0.1","port":70000}', "EZCONVERTER_READY nonsense", "INFO: started", "", 'EZCONVERTER_READY {"host":"127.0.0.1","port":"80"}']) expect(parseReadyLine(l)).toBeNull();
  });
  it("prefers an explicit binary, then the packaged one, then the active conda env", () => {
    const o = { dataDir: "/d", repoRoot: "/repo" };
    expect(resolveSidecarCommand(o, { EZ_SIDECAR_BIN: "/x/bin" }).cmd).toBe("/x/bin");
    expect(resolveSidecarCommand(o, { EZ_PYTHON: "/py" })).toEqual({ cmd: "/py", args: ["-m", "ezconverter", "serve", "--sidecar", "--port", "0"] });
    expect(resolveSidecarCommand(o, { CONDA_PREFIX: "/conda/EZ" }).cmd).toBe(process.platform === "win32" ? "/conda/EZ/python.exe" : "/conda/EZ/bin/python");
    expect(resolveSidecarCommand(o, {}).cmd).toBe("python");
  });
});
