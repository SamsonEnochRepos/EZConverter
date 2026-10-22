import { useId } from "react";
import type { OptionOut } from "@ezconverter/contracts";

export interface OptionsFormProps {
  specs: OptionOut[];
  values: Record<string, unknown>;
  errors?: Record<string, string>;
  onChange: (next: Record<string, unknown>) => void;
}

/** Renders a conversion's settings straight from the capability's declared option schema. */
export function OptionsForm({ specs, values, errors, onChange }: OptionsFormProps) {
  const uid = useId();
  if (!specs.length) return <p className="text-sm text-muted">No settings for this conversion.</p>;
  const set = (name: string, v: unknown) => onChange({ ...values, [name]: v });
  return (
    <div className="grid gap-4 sm:grid-cols-2" data-testid="options-form">
      {specs.map((s) => {
        const id = `${uid}-${s.name}`;
        const v = values[s.name] ?? s.default;
        const label = s.name.replace(/_/g, " ");
        const err = errors?.[s.name];
        return (
          <div key={s.name} className="flex flex-col gap-1">
            {s.type === "bool" ? (
              <label className="flex min-h-11 items-center gap-3 text-sm" htmlFor={id}>
                <input id={id} type="checkbox" className="size-5 accent-[var(--accent)]" checked={Boolean(v)} onChange={(e) => set(s.name, e.target.checked)} />
                <span className="capitalize">{label}</span>
              </label>
            ) : (
              <>
                <label htmlFor={id} className="text-xs font-semibold capitalize text-muted">{label}</label>
                {s.type === "enum" ? (
                  <select id={id} className="ez-select min-h-11 rounded-full border border-line bg-surface px-4 text-sm" value={String(v)}
                          onChange={(e) => { const raw = e.target.value; const match = (s.choices ?? []).find((c) => String(c) === raw); set(s.name, match ?? raw); }}>
                    {(s.choices ?? []).map((c) => <option key={String(c)} value={String(c)}>{c === "\t" ? "Tab" : String(c)}</option>)}
                  </select>
                ) : s.type === "string" ? (
                  <input id={id} type="text" className="min-h-11 rounded-2xl border border-line bg-surface px-4 text-sm" value={String(v ?? "")} onChange={(e) => set(s.name, e.target.value)} />
                ) : (
                  <input id={id} type="number" inputMode="decimal" className="min-h-11 rounded-2xl border border-line bg-surface px-4 text-sm"
                         min={s.minimum ?? undefined} max={s.maximum ?? undefined} step={s.type === "int" ? 1 : "any"} value={v === undefined || v === null ? "" : Number(v)}
                         onChange={(e) => { if (e.target.value !== "") set(s.name, Number(e.target.value)); }} aria-invalid={Boolean(err)} />
                )}
              </>
            )}
            <p className="text-xs text-muted" id={`${id}-d`}>{s.description}{s.minimum != null && s.maximum != null && s.type !== "bool" && s.type !== "enum" ? ` (${s.minimum}–${s.maximum})` : ""}</p>
            {err && <p className="text-xs text-bad" role="alert">{err}</p>}
          </div>
        );
      })}
    </div>
  );
}
