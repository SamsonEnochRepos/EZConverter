import { useId, useRef, useState } from "react";
import { Icon, cx } from "./primitives";

export interface DropzoneProps {
  onFiles: (files: File[]) => void;
  disabled?: boolean;
  compact?: boolean;
  hint?: string;
}

/** Drag-and-drop target that is also a real, keyboard-operable file picker. */
export function Dropzone({ onFiles, disabled, compact, hint }: DropzoneProps) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const id = useId();
  const take = (list: FileList | null) => { const files = list ? Array.from(list) : []; if (files.length && !disabled) onFiles(files); };
  const button = (
    <label htmlFor={id}
           className={cx("inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-full bg-accent px-6 py-2.5 text-sm font-semibold text-accentink shadow-glow transition-all hover:brightness-110 focus-within:outline-2 has-[:focus-visible]:outline", disabled && "pointer-events-none")}
           tabIndex={disabled ? -1 : 0}
           onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.current?.click(); } }}>
      <Icon name={compact ? "plus" : "upload"} size={16} />Choose files
    </label>
  );
  return (
    <div
      onDragOver={(e) => { e.preventDefault(); if (!disabled) setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); take(e.dataTransfer.files); }}
      className={cx("rounded-card border-2 border-dashed text-center shadow-card transition-colors", over ? "border-accent bg-accentsoft" : "border-accent/35 bg-surface",
                    compact ? "p-4" : "p-8 sm:p-12", disabled && "opacity-60")}
      data-testid="dropzone"
    >
      <input ref={input} id={id} data-testid="file-input" type="file" multiple className="sr-only" disabled={disabled}
             onChange={(e) => { take(e.target.files); e.target.value = ""; }} />
      {compact ? (
        <div className="flex flex-wrap items-center justify-center gap-3 sm:justify-between">
          <p className="flex items-center gap-2 text-sm font-semibold"><span className="grid size-9 place-items-center rounded-full bg-accentsoft text-accent"><Icon name="upload" size={18} /></span>Add more files</p>
          {button}
        </div>
      ) : (
        <div className="mx-auto flex max-w-md flex-col items-center gap-3">
          <span className="grid size-16 place-items-center rounded-full bg-accent text-accentink shadow-glow ring-8 ring-accentsoft"><Icon name="upload" size={28} /></span>
          <p className="mt-2 text-lg font-bold sm:text-xl">Drop files here to convert</p>
          <p className="text-sm text-muted">{hint ?? "Documents, images, audio, video, archives and data files. Mix as many as you like."}</p>
          <div className="mt-1">{button}</div>
        </div>
      )}
    </div>
  );
}
