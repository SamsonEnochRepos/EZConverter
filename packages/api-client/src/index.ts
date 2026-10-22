import type {
  BatchOut, BatchRequest, BatchResultOut, CapabilitiesOut, ConversionRequest, DetectionOut, ErrorBody, ExecutionPolicy,
  FileOut, Health, JobOut, JobResultOut, PlanOut, PlanRequest, SignedLink, TargetsOut,
} from "@ezconverter/contracts";

export type {
  BatchOut, BatchRequest, BatchResultOut, CapabilitiesOut, DetectionOut, FileOut, Health, JobOut, JobResultOut, PlanOut,
  SignedLink, TargetsOut,
};

/** A file the user picked. `blob` in browsers/Electron; `uri` for React Native / Expo file-system URIs. */
export interface UploadSource {
  name: string;
  size?: number;
  type?: string;
  blob?: Blob;
  uri?: string;
}

export interface UploadOptions {
  policy: ExecutionPolicy;
  onProgress?: (fraction: number) => void;
  signal?: AbortSignal;
}

export class EZApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number,
    public readonly details: Record<string, unknown> = {},
    public readonly requestId?: string | null,
  ) {
    super(message);
    this.name = "EZApiError";
  }
  get retryable(): boolean {
    return this.status === 429 || this.status >= 500 || this.code === "network_error";
  }
}

export interface ClientConfig {
  baseUrl: string;
  token?: string;
  fetch?: typeof fetch;
  /** Overridable for tests / non-browser platforms. Defaults to the global XMLHttpRequest when present. */
  xhr?: () => XMLHttpRequest;
}

const trim = (u: string) => u.replace(/\/+$/, "");

export class EZClient {
  readonly baseUrl: string;
  private token?: string;
  private readonly _fetch: typeof fetch;
  private readonly _xhr?: () => XMLHttpRequest;

  constructor(cfg: ClientConfig) {
    this.baseUrl = trim(cfg.baseUrl);
    this.token = cfg.token;
    this._fetch = cfg.fetch ?? ((...a) => fetch(...a));
    this._xhr = cfg.xhr ?? (typeof XMLHttpRequest !== "undefined" ? () => new XMLHttpRequest() : undefined);
  }

  setToken(token: string | undefined): void { this.token = token; }

  // ---------------------------------------------------------------- plumbing

  private headers(extra?: Record<string, string>): Record<string, string> {
    return { ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}), ...extra };
  }

  private async toError(res: Response): Promise<EZApiError> {
    let body: { error?: ErrorBody } = {};
    try { body = await res.json(); } catch { /* non-JSON error body */ }
    const e = body.error;
    return new EZApiError(e?.code ?? "http_error", e?.message ?? `Request failed (${res.status}).`, res.status, e?.details ?? {}, e?.request_id);
  }

  private async request<T>(method: string, path: string, opts: { json?: unknown; headers?: Record<string, string>; signal?: AbortSignal } = {}): Promise<T> {
    let res: Response;
    try {
      res = await this._fetch(this.baseUrl + path, {
        method,
        headers: this.headers({ ...(opts.json !== undefined ? { "Content-Type": "application/json" } : {}), ...opts.headers }),
        body: opts.json !== undefined ? JSON.stringify(opts.json) : undefined,
        signal: opts.signal,
      });
    } catch (e) {
      if ((e as Error).name === "AbortError") throw e;
      throw new EZApiError("network_error", `Could not reach ${this.baseUrl}. Check that the converter is running.`, 0);
    }
    if (!res.ok) throw await this.toError(res);
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  // ---------------------------------------------------------------- meta & capabilities

  health(signal?: AbortSignal) { return this.request<Health>("GET", "/v1/health", { signal }); }
  capabilities(q: { source?: string; target?: string; includeUnavailable?: boolean } = {}) {
    const p = new URLSearchParams();
    if (q.source) p.set("source", q.source);
    if (q.target) p.set("target", q.target);
    if (q.includeUnavailable) p.set("include_unavailable", "true");
    const qs = p.toString();
    return this.request<CapabilitiesOut>("GET", `/v1/capabilities${qs ? `?${qs}` : ""}`);
  }
  targets(source: string) { return this.request<TargetsOut>("GET", `/v1/targets?source=${encodeURIComponent(source)}`); }

  // ---------------------------------------------------------------- files

  /** Uploads the raw bytes (never multipart) so a refused policy is rejected before the body is read. */
  upload(src: UploadSource, opts: UploadOptions): Promise<FileOut> {
    return this.sendBody<FileOut>(`/v1/files?filename=${encodeURIComponent(src.name)}`, src, opts);
  }
  inspect(src: UploadSource, opts: UploadOptions): Promise<DetectionOut> {
    return this.sendBody<DetectionOut>(`/v1/files/inspect?filename=${encodeURIComponent(src.name)}`, src, opts);
  }
  getFile(id: string) { return this.request<FileOut>("GET", `/v1/files/${id}`); }
  fileTargets(id: string) { return this.request<TargetsOut>("GET", `/v1/files/${id}/targets`); }
  deleteFile(id: string) { return this.request<void>("DELETE", `/v1/files/${id}`); }

  private sendBody<T>(path: string, src: UploadSource, opts: UploadOptions): Promise<T> {
    const headers = this.headers({ "X-EZ-Execution-Policy": opts.policy, "Content-Type": "application/octet-stream" });
    if (this._xhr) return this.xhrSend<T>(path, src, headers, opts);
    if (!src.blob) return Promise.reject(new EZApiError("invalid_request", "This platform needs a Blob to upload.", 0));
    return this.fetchSend<T>(path, src.blob, headers, opts);
  }

  private async fetchSend<T>(path: string, blob: Blob, headers: Record<string, string>, opts: UploadOptions): Promise<T> {
    let res: Response;
    try {
      res = await this._fetch(this.baseUrl + path, { method: "POST", headers, body: blob, signal: opts.signal });
    } catch (e) {
      if ((e as Error).name === "AbortError") throw e;
      throw new EZApiError("network_error", `Could not reach ${this.baseUrl}. Check that the converter is running.`, 0);
    }
    opts.onProgress?.(1);
    if (!res.ok) throw await this.toError(res);
    return (await res.json()) as T;
  }

  private xhrSend<T>(path: string, src: UploadSource, headers: Record<string, string>, opts: UploadOptions): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const xhr = this._xhr!();
      xhr.open("POST", this.baseUrl + path);
      for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
      xhr.responseType = "text";
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) opts.onProgress?.(e.loaded / e.total); };
      xhr.onerror = () => reject(new EZApiError("network_error", `Could not reach ${this.baseUrl}. Check that the converter is running.`, 0));
      xhr.onabort = () => reject(new DOMException("Aborted", "AbortError"));
      xhr.onload = () => {
        let body: any = {};
        try { body = JSON.parse(xhr.responseText); } catch { /* ignore */ }
        if (xhr.status >= 200 && xhr.status < 300) { opts.onProgress?.(1); resolve(body as T); return; }
        const e = body?.error;
        reject(new EZApiError(e?.code ?? "http_error", e?.message ?? `Upload failed (${xhr.status}).`, xhr.status, e?.details ?? {}, e?.request_id));
      };
      if (opts.signal) {
        if (opts.signal.aborted) { reject(new DOMException("Aborted", "AbortError")); return; }
        opts.signal.addEventListener("abort", () => xhr.abort(), { once: true });
      }
      // React Native's XHR accepts { uri, type, name } as a body and streams the file from disk.
      xhr.send((src.blob ?? { uri: src.uri, type: src.type ?? "application/octet-stream", name: src.name }) as any);
    });
  }

  // ---------------------------------------------------------------- conversions & jobs

  plan(body: PlanRequest) { return this.request<PlanOut>("POST", "/v1/conversions/plan", { json: body }); }
  convert(body: ConversionRequest, idempotencyKey?: string) {
    return this.request<JobOut>("POST", "/v1/conversions", { json: body, headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined });
  }
  jobs(limit = 50) { return this.request<JobOut[]>("GET", `/v1/jobs?limit=${limit}`); }
  job(id: string, signal?: AbortSignal) { return this.request<JobOut>("GET", `/v1/jobs/${id}`, { signal }); }
  cancelJob(id: string) { return this.request<JobOut>("POST", `/v1/jobs/${id}/cancel`); }
  retryJob(id: string) { return this.request<JobOut>("POST", `/v1/jobs/${id}/retry`); }
  deleteJob(id: string) { return this.request<void>("DELETE", `/v1/jobs/${id}`); }
  jobResult(id: string) { return this.request<JobResultOut>("GET", `/v1/jobs/${id}/result`); }

  // ---------------------------------------------------------------- batches

  createBatch(body: BatchRequest, idempotencyKey?: string) {
    return this.request<BatchOut>("POST", "/v1/batches", { json: body, headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined });
  }
  batch(id: string, signal?: AbortSignal) { return this.request<BatchOut>("GET", `/v1/batches/${id}`, { signal }); }
  cancelBatch(id: string) { return this.request<BatchOut>("POST", `/v1/batches/${id}/cancel`); }
  retryFailed(id: string) { return this.request<BatchOut>("POST", `/v1/batches/${id}/retry-failed`); }
  batchResult(id: string) { return this.request<BatchResultOut>("GET", `/v1/batches/${id}/result`); }
  deleteBatch(id: string) { return this.request<void>("DELETE", `/v1/batches/${id}`); }

  // ---------------------------------------------------------------- downloads

  /** Short-lived, artifact-bound URL that a browser/OS downloader can open without an Authorization header. */
  async signedUrl(pathOrArtifactId: string): Promise<string> {
    const id = pathOrArtifactId.includes("/") ? pathOrArtifactId.split("/").filter(Boolean)[2]! : pathOrArtifactId;
    const link = await this.request<SignedLink>("POST", `/v1/artifacts/${id}/link`);
    return this.baseUrl + link.url;
  }
  /** Authenticated download into memory (used for small files and for ZIP archives on mobile). */
  async downloadBlob(path: string): Promise<Blob> {
    const res = await this._fetch(this.baseUrl + path, { headers: this.headers() });
    if (!res.ok) throw await this.toError(res);
    return res.blob();
  }
  batchArchivePath(id: string) { return `/v1/batches/${id}/archive`; }
  /** Builds the ZIP (successful outputs + manifest.json) and returns a short-lived download URL. */
  async batchArchiveLink(id: string): Promise<string> {
    const link = await this.request<SignedLink>("POST", `/v1/batches/${id}/archive/link`);
    return this.baseUrl + link.url;
  }
  deleteRequest(path: string) { return this.request<void>("DELETE", path); }
}
