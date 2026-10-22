# Observability

* **Health:** `GET /v1/health` (unauthenticated) returns version, runtime location, `network_isolation` state, retention and limits.
* **Logs:** the service logs to stderr with request ids (`X-Request-ID` is returned on every response and echoed in the error envelope). File contents, tokens and local paths are never logged; engine stderr is trimmed and path-scrubbed before it reaches an API response.
* **Job audit trail:** every state change is recorded in `job_events` (SQLite) with a timestamp, so "why did this job end up here" is answerable after the fact.
* **Suggested alerts** (export logs to your stack): sustained `rate_limited`, any `worker_lost`, `resource_limit` spikes, queue depth (count of `QUEUED` jobs older than N minutes), disk usage of `EZ_DATA_DIR`.
* **Not implemented:** Prometheus/OpenTelemetry exporters. See docs/STATUS.md.
