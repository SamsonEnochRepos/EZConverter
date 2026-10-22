import type { ButtonHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";

export const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");

const PATHS: Record<string, string> = {
  upload: "M12 16V4m0 0l-4 4m4-4l4 4M4 16v3a1 1 0 001 1h14a1 1 0 001-1v-3",
  file: "M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8l-5-5zM14 3v5h5",
  x: "M6 6l12 12M18 6L6 18",
  check: "M5 13l4 4L19 7",
  alert: "M12 9v4m0 4h.01M10.3 3.9L2.4 18a2 2 0 001.7 3h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z",
  download: "M12 4v12m0 0l-4-4m4 4l4-4M4 20h16",
  retry: "M4 4v6h6M20 20v-6h-6M5.6 15A8 8 0 0018.4 9M18.4 9L20 10M5.6 15L4 14",
  trash: "M4 7h16M10 11v6m4-6v6M6 7l1 12a2 2 0 002 2h6a2 2 0 002-2l1-12M9 7V4h6v3",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z",
  cloud: "M7 18a4 4 0 010-8 5 5 0 019.6-1A4.5 4.5 0 0117 18H7z",
  device: "M5 5h14v10H5zM2 19h20",
  clock: "M12 7v5l3 2m6-2a9 9 0 11-18 0 9 9 0 0118 0z",
  settings: "M12 15a3 3 0 100-6 3 3 0 000 6zM19 12a7 7 0 00-.1-1.2l2-1.5-2-3.4-2.3.9a7 7 0 00-2-1.2L14 3h-4l-.6 2.6a7 7 0 00-2 1.2l-2.3-.9-2 3.4 2 1.5a7 7 0 000 2.4l-2 1.5 2 3.4 2.3-.9a7 7 0 002 1.2L10 21h4l.6-2.6a7 7 0 002-1.2l2.3.9 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z",
  arrow: "M5 12h14m-6-6l6 6-6 6",
  swap: "M7 4L3 8l4 4M3 8h14M17 12l4 4-4 4M21 16H7",
  plus: "M12 5v14M5 12h14",
  home: "M4 11l8-7 8 7v8a1 1 0 01-1 1h-4v-6H9v6H5a1 1 0 01-1-1v-8z",
  layers: "M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5M3 17l9 5 9-5",
  diagonal: "M7 17L17 7M8 7h9v9",
  sparkle: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3zM19 16l.7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7L19 16z",
  lock: "M7 11V8a5 5 0 0110 0v3M6 11h12a1 1 0 011 1v8a1 1 0 01-1 1H6a1 1 0 01-1-1v-8a1 1 0 011-1z",
};

export function Icon({ name, size = 18, className }: { name: keyof typeof PATHS | string; size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}>
      <path d={PATHS[name] ?? PATHS.file} />
    </svg>
  );
}

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger" | "hero"; icon?: string };
export function Button({ variant = "secondary", icon, className, children, ...rest }: BtnProps) {
  const base = "inline-flex items-center justify-center gap-2 rounded-full px-5 py-2.5 text-sm font-semibold transition-all active:scale-[0.98] disabled:opacity-50 disabled:cursor-not-allowed disabled:active:scale-100 min-h-11";
  const v = {
    primary: "bg-accent text-accentink shadow-glow hover:brightness-110 disabled:shadow-none",
    secondary: "bg-surface text-ink border border-line hover:bg-accentsoft hover:border-accent/40",
    ghost: "text-muted hover:text-ink hover:bg-surface2",
    danger: "bg-badsoft text-bad hover:brightness-95",
    hero: "bg-white text-[#0a4fd6] shadow-[0_8px_24px_rgb(5_20_80/30%)] hover:bg-white/90",
  }[variant];
  return <button type="button" className={cx(base, v, className)} {...rest}>{icon && <Icon name={icon} size={16} />}{children}</button>;
}

export function Select({ label, className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement> & { label: string }) {
  return (
    <label className="flex min-w-0 max-w-full flex-col gap-1.5 text-xs font-semibold text-muted">
      {label}
      <select className={cx("ez-select min-h-11 min-w-0 max-w-full truncate rounded-full border border-line bg-surface px-4 text-sm font-medium text-ink", className)} {...rest}>{children}</select>
    </label>
  );
}

export function Card({ children, className, ...rest }: { children: ReactNode; className?: string } & React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cx("rounded-card border border-line/70 bg-surface shadow-card", className)} {...rest}>{children}</div>;
}

const TONES = { good: "bg-goodsoft text-good", warn: "bg-warnsoft text-warn", bad: "bg-badsoft text-bad", info: "bg-infosoft text-info", neutral: "bg-surface2 text-muted" } as const;
export function Badge({ tone = "neutral", children, title }: { tone?: keyof typeof TONES; children: ReactNode; title?: string }) {
  return <span title={title} className={cx("inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold", TONES[tone])}>{children}</span>;
}

export function Alert({ tone = "info", title, children, action }: { tone?: "info" | "warn" | "bad" | "good"; title?: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div role={tone === "bad" ? "alert" : "status"} className={cx("flex flex-wrap items-start gap-3 rounded-2xl p-3.5 text-sm", TONES[tone])}>
      <Icon name={tone === "good" ? "check" : "alert"} className="mt-0.5 shrink-0" />
      <div className="min-w-0 flex-1">{title && <p className="font-semibold">{title}</p>}{children && <div className={title ? "mt-0.5" : ""}>{children}</div>}</div>
      {action}
    </div>
  );
}

/** A document-style tile carrying the file's format, so a row is recognisable at a glance. */
export function FileTile({ ext, size = "md", tone = "accent" }: { ext?: string; size?: "sm" | "md"; tone?: "accent" | "good" | "bad" }) {
  const label = (ext ?? "").replace(/^\./, "").slice(0, 4).toUpperCase();
  const dim = size === "sm" ? "size-10" : "size-12";
  const color = { accent: "bg-accentsoft text-accent", good: "bg-goodsoft text-good", bad: "bg-badsoft text-bad" }[tone];
  return (
    <span aria-hidden="true" className={cx("relative grid shrink-0 place-items-center rounded-2xl", dim, color)}>
      <Icon name="file" size={size === "sm" ? 22 : 26} className="opacity-90" />
      {label && <span className={cx("absolute -bottom-1.5 left-1/2 -translate-x-1/2 rounded-md bg-accent px-1.5 text-[9px] font-bold leading-[14px] tracking-wide text-accentink", tone === "good" && "!bg-good !text-surface", tone === "bad" && "!bg-bad !text-surface")}>{label}</span>}
    </span>
  );
}

/** `value` null = indeterminate (the engine cannot measure progress); never a fake percentage. */
export function ProgressBar({ value, label }: { value: number | null; label: string }) {
  const pct = value == null ? undefined : Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-valuetext={pct == null ? "In progress" : `${pct}%`}
         className="relative h-2.5 w-full overflow-hidden rounded-full bg-surface2">
      {pct == null
        ? <div className="ez-indeterminate absolute inset-y-0 w-2/5 rounded-full bg-accent" />
        : <div className="h-full rounded-full bg-accent transition-[width] duration-300" style={{ width: `${pct}%` }} />}
    </div>
  );
}

/** Circular progress. Same rule as ProgressBar: `value` null spins and shows no number. */
export function ProgressRing({ value, label, size = 112, children }: { value: number | null; label: string; size?: number; children?: ReactNode }) {
  const pct = value == null ? undefined : Math.round(Math.max(0, Math.min(1, value)) * 100);
  const stroke = 9, r = (size - stroke) / 2, c = 2 * Math.PI * r;
  return (
    <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-valuetext={pct == null ? "In progress" : `${pct}%`}
         className="relative grid shrink-0 place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className={cx("absolute inset-0 -rotate-90", pct == null && "ez-spin")} aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} className="stroke-surface2" />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} strokeLinecap="round" className="stroke-accent transition-[stroke-dashoffset] duration-300"
                strokeDasharray={c} strokeDashoffset={pct == null ? c * 0.72 : c * (1 - pct / 100)} />
      </svg>
      <div className="relative text-center">{children ?? (pct != null && <span className="text-2xl font-bold text-accent">{pct}<span className="text-sm">%</span></span>)}</div>
    </div>
  );
}
