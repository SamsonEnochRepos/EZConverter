export interface Preset {
  id: string;
  name: string;
  description: string;
  /** Applies to files whose format belongs to this family (as reported by /v1/capabilities). */
  family: "image" | "audio" | "video" | "document" | "spreadsheet" | "presentation" | "archive" | "data";
  target: string;
  options: Record<string, unknown>;
  builtIn: boolean;
}

export const BUILT_IN_PRESETS: Preset[] = [
  { id: "img-web", name: "Web photo", description: "WebP, at most 1600 px wide, quality 80", family: "image", target: "webp", options: { max_width: 1600, quality: 80 }, builtIn: true },
  { id: "img-share", name: "Share-friendly JPEG", description: "JPEG, at most 1080 px, quality 82", family: "image", target: "jpeg", options: { max_width: 1080, max_height: 1080, quality: 82 }, builtIn: true },
  { id: "img-png", name: "Lossless PNG", description: "PNG, original size", family: "image", target: "png", options: {}, builtIn: true },
  { id: "aud-mp3", name: "MP3 128 kbps", description: "Small, plays everywhere", family: "audio", target: "mp3", options: { bitrate_kbps: 128 }, builtIn: true },
  { id: "aud-flac", name: "Lossless FLAC", description: "Archive-quality audio", family: "audio", target: "flac", options: {}, builtIn: true },
  { id: "aud-voice", name: "Voice memo (Opus)", description: "Opus 48 kbps, mono", family: "audio", target: "opus", options: { bitrate_kbps: 48, channels: 1 }, builtIn: true },
  { id: "vid-720", name: "Web video 720p", description: "MP4 (H.264), 720p, balanced quality", family: "video", target: "mp4", options: { max_height: 720, crf: 26, mode: "reencode" }, builtIn: true },
  { id: "vid-webm", name: "WebM for the web", description: "WebM (VP9), 720p", family: "video", target: "webm", options: { max_height: 720, crf: 34 }, builtIn: true },
  { id: "doc-pdf", name: "Share as PDF", description: "PDF from a Word/ODT document", family: "document", target: "pdf", options: {}, builtIn: true },
  { id: "pres-pdf", name: "Slides to PDF", description: "PDF from a presentation", family: "presentation", target: "pdf", options: {}, builtIn: true },
  { id: "sheet-csv", name: "Sheet to CSV", description: "First sheet as CSV", family: "spreadsheet", target: "csv", options: {}, builtIn: true },
  { id: "arc-zip", name: "Repack as ZIP", description: "ZIP that opens everywhere", family: "archive", target: "zip", options: {}, builtIn: true },
];

export function presetsFor(family: string | undefined, custom: Preset[] = []): Preset[] {
  return [...BUILT_IN_PRESETS, ...custom].filter((p) => !family || p.family === family);
}

/** Keeps only options the preset's target actually exposes, so a stale preset can't send unknown options. */
export function sanitizeOptions(options: Record<string, unknown>, allowed: string[]): Record<string, unknown> {
  return Object.fromEntries(Object.entries(options).filter(([k]) => allowed.includes(k)));
}
