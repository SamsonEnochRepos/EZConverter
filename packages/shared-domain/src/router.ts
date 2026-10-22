/**
 * Execution routing between a local runtime (this device) and remote runtimes (cloud).
 *
 * Invariants (tested):
 *  - LOCAL_ONLY never selects a remote runtime and never falls back to one.
 *  - Remote execution requires either REMOTE_ALLOWED or the user's standing consent (`allowRemote`) under AUTO.
 *  - When a decision cannot be made, the result explains why and states that nothing was uploaded.
 */
import type { EZClient } from "@ezconverter/api-client";
import type { ExecutionPolicy, TargetOut } from "@ezconverter/contracts";

export interface RuntimeConfig { id: string; label: string; location: "local" | "remote"; baseUrl: string; token?: string }
export interface Runtime { config: RuntimeConfig; client: EZClient }
export interface RouterSettings { allowRemote: boolean }

export type ResolvedPolicy = "LOCAL_ONLY" | "REMOTE_ALLOWED";
export type RouteDecision =
  | { ok: true; runtime: Runtime; policy: ResolvedPolicy; usesRemote: boolean; explanation: string; target: TargetOut }
  | { ok: false; code: "local_only_violation" | "remote_consent_required" | "unsupported_conversion" | "no_runtime"; message: string };

interface Probe { ok: boolean; target?: TargetOut; reason?: string }

export class CapabilityProbe {
  private cache = new Map<string, { at: number; list: TargetOut[] }>();
  constructor(private ttlMs = 30_000, private now: () => number = Date.now) {}

  async can(rt: Runtime, source: string, target: string): Promise<Probe> {
    const key = `${rt.config.id}|${source}`;
    let entry = this.cache.get(key);
    if (!entry || this.now() - entry.at >= this.ttlMs) {
      try {
        entry = { at: this.now(), list: (await rt.client.targets(source)).targets };
        this.cache.set(key, entry);
      } catch (e) {
        return { ok: false, reason: `${rt.config.label} is unreachable (${(e as Error).message})` };
      }
    }
    const t = entry.list.find((x) => x.target === target);
    return t ? { ok: true, target: t } : { ok: false, reason: `${rt.config.label} has no way to convert ${source.toUpperCase()} to ${target.toUpperCase()}` };
  }

  invalidate(): void { this.cache.clear(); }
}

export async function routeConversion(
  policy: ExecutionPolicy, runtimes: Runtime[], need: { source: string; target: string }, settings: RouterSettings,
  probe: CapabilityProbe = new CapabilityProbe(),
): Promise<RouteDecision> {
  const locals = runtimes.filter((r) => r.config.location === "local");
  const remotes = runtimes.filter((r) => r.config.location === "remote");
  if (!runtimes.length) return { ok: false, code: "no_runtime", message: "No converter is configured. Add one in Settings." };

  const firstCapable = async (list: Runtime[]) => {
    const reasons: string[] = [];
    for (const rt of list) {
      const p = await probe.can(rt, need.source, need.target);
      if (p.ok && p.target) return { rt, target: p.target, reasons };
      if (p.reason) reasons.push(p.reason);
    }
    return { rt: undefined, target: undefined, reasons };
  };

  const local = await firstCapable(locals);
  const tgt = need.target.toUpperCase();

  if (policy === "LOCAL_ONLY") {
    if (local.rt && local.target)
      return { ok: true, runtime: local.rt, policy: "LOCAL_ONLY", usesRemote: false, target: local.target,
               explanation: "Converted on this device. The file is not uploaded." };
    const remote = await firstCapable(remotes);
    return { ok: false, code: "local_only_violation",
      message: `${tgt} can't be produced on this device${local.reasons.length ? ` (${local.reasons[0]})` : ""}. Because processing is set to “Local only”, nothing was uploaded.` +
        (remote.rt ? ` Switch to “Allow remote” to use ${remote.rt.config.label}.` : "") };
  }

  if (local.rt && local.target)   // privacy first: when this device can do it, it does, for AUTO and REMOTE_ALLOWED alike
    return { ok: true, runtime: local.rt, policy: "LOCAL_ONLY", usesRemote: false, target: local.target,
             explanation: "Converted on this device. The file is not uploaded." };

  const remote = await firstCapable(remotes);
  if (!remote.rt || !remote.target)
    return { ok: false, code: "unsupported_conversion", message: `${need.source.toUpperCase()} → ${tgt} isn't available on any configured converter.` };

  if (policy === "AUTO" && !settings.allowRemote)
    return { ok: false, code: "remote_consent_required",
      message: `This device can't produce ${tgt}, but ${remote.rt.config.label} can. Allow remote processing to upload this file there.` };

  return { ok: true, runtime: remote.rt, policy: "REMOTE_ALLOWED", usesRemote: true, target: remote.target,
           explanation: `The file will be uploaded to ${remote.rt.config.label} to be converted, then deleted after the retention period.` };
}
