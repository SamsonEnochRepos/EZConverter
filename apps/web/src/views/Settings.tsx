import { EZClient } from "@ezconverter/api-client";
import { type AppSettings, FAMILY_LABEL } from "@ezconverter/shared-domain";
import { Alert, Badge, Button, Card, Icon, Select } from "@ezconverter/ui";
import { useState } from "react";
import { type RuntimeStatus, useApp } from "../state";

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return <label className="flex flex-col gap-1.5 text-sm font-semibold">{label}{children}{hint && <span className="text-xs font-normal text-muted">{hint}</span>}</label>;
}
const input = "min-h-11 rounded-2xl border border-line bg-surface px-4 text-sm font-normal";

function RuntimeForm({ kind, settings, set }: { kind: "local" | "remote"; settings: AppSettings; set: (p: Partial<AppSettings>) => void }) {
  const { catalog, refreshCatalog } = useApp();
  const [test, setTest] = useState<{ ok: boolean; text: string }>();
  const url = kind === "local" ? settings.localUrl : settings.remoteUrl;
  const token = kind === "local" ? settings.localToken : settings.remoteToken;
  const status: RuntimeStatus | undefined = catalog.statuses.find((s) => s.location === kind);
  const run = async () => {
    setTest(undefined);
    try {
      const c = new EZClient({ baseUrl: url, token: token || undefined });
      const h = await c.health();
      const caps = await c.capabilities();
      setTest({ ok: true, text: `Connected: ${caps.capabilities.length} verified conversions, files kept ${h.retention_hours} h, up to ${h.max_upload_mb} MB each.` });
    } catch (e) { setTest({ ok: false, text: (e as Error).message }); }
  };
  return (
    <Card className="space-y-4 p-5" data-testid={`runtime-${kind}`}>
      <div className="flex items-center gap-3"><span className="grid size-10 place-items-center rounded-full bg-accentsoft text-accent"><Icon name={kind === "local" ? "device" : "cloud"} /></span><h2 className="font-bold">{kind === "local" ? "This device" : "Cloud converter (optional)"}</h2>
        {status && <Badge tone={status.state === "online" ? "good" : status.state === "unauthorized" ? "warn" : "bad"}>{status.state === "online" ? "Online" : status.state === "unauthorized" ? "Needs token" : "Offline"}</Badge>}</div>
      <p className="text-sm text-muted">{kind === "local" ? "The converter running on your own machine. Files never leave it." : "A converter on a server. Files are uploaded to it only when you allow remote processing."}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Address"><input className={input} value={url} placeholder="http://127.0.0.1:8787" onChange={(e) => set(kind === "local" ? { localUrl: e.target.value } : { remoteUrl: e.target.value })} autoComplete="off" /></Field>
        <Field label="Access token" hint="Stored on this device only."><input className={input} type="password" value={token} onChange={(e) => set(kind === "local" ? { localToken: e.target.value } : { remoteToken: e.target.value })} autoComplete="off" /></Field>
      </div>
      <div className="flex flex-wrap items-center gap-3"><Button onClick={() => void run()}>Test connection</Button><Button variant="ghost" onClick={refreshCatalog}>Refresh capabilities</Button></div>
      {test && <Alert tone={test.ok ? "good" : "bad"}>{test.text}</Alert>}
      {status?.state === "online" && status.caps && (
        <details className="text-sm"><summary className="min-h-11 cursor-pointer py-2 font-medium">Engines on this converter</summary>
          <ul className="mt-1 space-y-1">{status.caps.engines.map((e) => <li key={e.engine_id} className="flex items-center gap-2"><Badge tone={e.available ? "good" : "bad"}>{e.available ? "ready" : "unavailable"}</Badge><span className="font-medium">{e.engine_id}</span><span className="text-xs text-muted">{e.version ?? e.reason ?? ""}</span></li>)}</ul>
          <p className="mt-2 text-xs text-muted">Families: {[...new Set(status.caps.formats.map((f) => FAMILY_LABEL[f.family] ?? f.family))].join(", ")}</p></details>
      )}
    </Card>
  );
}

export function Settings() {
  const { settings, setSettings, catalog } = useApp();
  const health = catalog.statuses.find((s) => s.health)?.health;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-extrabold tracking-tight">Settings</h1>
      <RuntimeForm kind="local" settings={settings} set={setSettings} />
      <RuntimeForm kind="remote" settings={settings} set={setSettings} />

      <Card className="space-y-4 p-5">
        <h2 className="flex items-center gap-2 font-bold"><Icon name="lock" size={18} className="text-accent" />Privacy &amp; processing</h2>
        <Select label="Default processing mode" value={settings.defaultPolicy} onChange={(e) => setSettings({ defaultPolicy: e.target.value as AppSettings["defaultPolicy"] })} data-testid="default-policy">
          <option value="AUTO">Automatic — use this device when it can, ask before using the cloud</option>
          <option value="LOCAL_ONLY">Local only — never upload anything</option>
          <option value="REMOTE_ALLOWED">Allow remote converters</option>
        </Select>
        <label className="flex min-h-11 items-start gap-3 text-sm"><input type="checkbox" className="mt-0.5 size-5 accent-[var(--accent)]" checked={settings.allowRemote} onChange={(e) => setSettings({ allowRemote: e.target.checked })} data-testid="allow-remote" />
          <span><span className="font-medium">Allow uploading to the cloud converter when this device can't do a conversion.</span><br /><span className="text-muted">Used only in Automatic mode. “Local only” always overrides this and never uploads.</span></span></label>
        <p className="text-sm text-muted" data-testid="retention-note">{health ? `Uploaded files and results are deleted automatically after ${health.retention_hours} hours. You can delete them sooner from the results screen.` : "Connect a converter to see its retention period."}</p>
      </Card>

      <Card className="space-y-4 p-5">
        <h2 className="flex items-center gap-2 font-bold"><Icon name="sparkle" size={18} className="text-accent" />Appearance &amp; accessibility</h2>
        <Select label="Theme" value={settings.theme} onChange={(e) => setSettings({ theme: e.target.value as AppSettings["theme"] })}>
          <option value="system">Match system</option><option value="light">Light</option><option value="dark">Dark</option>
        </Select>
        <label className="flex min-h-11 items-center gap-3 text-sm"><input type="checkbox" className="size-5 accent-[var(--accent)]" checked={settings.largeText} onChange={(e) => setSettings({ largeText: e.target.checked })} />Larger text</label>
        <label className="flex min-h-11 items-center gap-3 text-sm"><input type="checkbox" className="size-5 accent-[var(--accent)]" checked={settings.reduceMotion} onChange={(e) => setSettings({ reduceMotion: e.target.checked })} />Reduce motion</label>
      </Card>
    </div>
  );
}
