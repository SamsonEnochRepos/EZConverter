import type { Fidelity, JobState } from "@ezconverter/contracts";

export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2)} ${units[i]}`;
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds)) return "—";
  if (seconds < 1) return "under a second";
  if (seconds < 60) return `about ${Math.round(seconds)} s`;
  const m = Math.round(seconds / 60);
  return m < 60 ? `about ${m} min` : `about ${(m / 60).toFixed(1)} h`;
}

export const FIDELITY: Record<Fidelity, { label: string; short: string; tone: "good" | "info" | "warn"; description: string }> = {
  byte_preserving: { label: "Exact copy", short: "Exact", tone: "good", description: "The content is byte-for-byte identical; only the wrapper changes." },
  lossless_reencode: { label: "Lossless", short: "Lossless", tone: "good", description: "Re-encoded without discarding information that was in the source." },
  lossy: { label: "Lossy", short: "Lossy", tone: "warn", description: "Some detail is discarded to make the file smaller or compatible." },
  layout_altering: { label: "Layout may change", short: "Layout", tone: "warn", description: "Content is kept, but pagination, fonts or positioning can differ." },
  semantic: { label: "Content transformed", short: "Transformed", tone: "info", description: "The content is mapped onto a different model (for example, extracting text)." },
};

export const STATE_LABEL: Record<JobState, string> = {
  CREATED: "Created", VALIDATING: "Checking", QUEUED: "Waiting", RUNNING: "Converting", VERIFYING: "Verifying output",
  SUCCEEDED: "Done", FAILED: "Failed", CANCELLED: "Cancelled",
};

/** Maps machine error codes to a short title and a next step for the person looking at the screen. */
export function explainError(code: string): { title: string; action: string } {
  const m: Record<string, { title: string; action: string }> = {
    unsupported_conversion: { title: "Can't convert to that format", action: "Pick one of the formats offered for this file." },
    unsupported_input: { title: "File not recognised", action: "Check that the file is complete and in a supported format." },
    malformed_input: { title: "File looks damaged", action: "Re-export or re-download the original and try again." },
    unsafe_input: { title: "File rejected for safety", action: "The file has a structure that is unsafe to process. Use a different file." },
    file_too_large: { title: "File too large", action: "Try a smaller file or lower the quality first." },
    quota_exceeded: { title: "Temporary storage is full", action: "Delete finished results, then retry." },
    invalid_options: { title: "A setting isn't valid", action: "Adjust the highlighted setting." },
    local_only_violation: { title: "Needs remote processing", action: "Switch the processing mode to allow remote conversion, or choose another format." },
    remote_consent_required: { title: "Remote processing not allowed", action: "Allow remote processing in Settings to upload this file." },
    timeout: { title: "Took too long", action: "Retry, or use a lower quality setting." },
    resource_limit: { title: "Ran out of resources", action: "Retry with a smaller file or lower settings." },
    worker_lost: { title: "Converter stopped unexpectedly", action: "Retry." },
    output_validation_failed: { title: "Result failed validation", action: "The converted file was discarded because it wasn't correct. Try different settings." },
    conversion_failed: { title: "Conversion failed", action: "Try another target format." },
    network_error: { title: "Can't reach the converter", action: "Check that it is running and the address in Settings is correct." },
    unauthenticated: { title: "Not signed in", action: "Check the access token in Settings." },
    rate_limited: { title: "Too many requests", action: "Wait a moment and try again." },
    no_runtime: { title: "No converter available", action: "Start the local converter, or allow remote processing in Settings." },
    cancelled: { title: "Cancelled", action: "" },
  };
  return m[code] ?? { title: "Something went wrong", action: "Try again." };
}

export const FAMILY_LABEL: Record<string, string> = {
  document: "Documents", spreadsheet: "Spreadsheets", presentation: "Presentations", image: "Images", audio: "Audio",
  video: "Video", archive: "Archives", data: "Data",
};

export function extensionOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(i + 1).toLowerCase() : "";
}
