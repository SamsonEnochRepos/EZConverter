import { STATE_LABEL, formatBytes } from "@ezconverter/shared-domain";
import { Badge, Button, Card, FileTile, Icon } from "@ezconverter/ui";
import { useEffect, useState } from "react";
import { type HistoryEntry, loadHistory, saveHistory, useApp } from "../state";

export function History() {
  const { catalog } = useApp();
  const [items, setItems] = useState<HistoryEntry[] | null>(null);
  useEffect(() => { void loadHistory().then(setItems); }, []);
  if (!items) return null;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-extrabold tracking-tight">History</h1>
        {items.length > 0 && <Button variant="danger" icon="trash" onClick={() => { void saveHistory([]); setItems([]); }}>Clear history</Button>}
      </div>
      <p className="text-sm text-muted">Stored only on this device. Converted files themselves are deleted from the converter automatically; history keeps just the record.</p>
      {items.length === 0 ? <Card className="grid justify-items-center gap-3 p-10 text-center text-muted"><span className="grid size-14 place-items-center rounded-full bg-accentsoft text-accent"><Icon name="clock" size={26} /></span>Nothing here yet. Finished conversions will be listed.</Card> : (
        <ul className="space-y-2" data-testid="history-list">
          {items.map((e) => (
            <li key={e.id}><Card className="flex flex-wrap items-center gap-3.5 p-4">
              <FileTile ext={e.target} size="sm" tone={e.state === "FAILED" ? "bad" : "accent"} />
              <div className="min-w-0 flex-1 basis-48">
                <p className="truncate font-bold">{e.name}</p>
                <p className="text-xs text-muted">{e.source.toUpperCase()} → {catalog.formats.get(e.target)?.name ?? e.target.toUpperCase()} · {new Date(e.at).toLocaleString()}{e.size ? ` · ${formatBytes(e.size)}` : ""}</p>
                {e.error && <p className="mt-1 text-xs text-bad">{e.error}</p>}
              </div>
              <Badge tone={e.state === "SUCCEEDED" ? "good" : e.state === "FAILED" ? "bad" : "neutral"}>{STATE_LABEL[e.state as keyof typeof STATE_LABEL] ?? e.state}</Badge>
            </Card></li>
          ))}
        </ul>
      )}
    </div>
  );
}
