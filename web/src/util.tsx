import { useCallback, useEffect, useState } from "react";

export const STATUS: Record<string, [string, string]> = {
  ACTIVE: ["Active", "clear"], OPEN: ["Open", "clear"], APPLIED: ["Applied", "clear"],
  PENDING_ITEMS: ["Items requested", "refer"], RETURNED: ["Returned", "refer"],
  PENDING_REVIEW: ["Compliance review", "refer"], PENDING_SANCTIONS: ["Sanctions review", "escalate"],
  PENDING_EDD: ["EDD pending", "escalate"], PENDING_CONFIRMATION: ["Confirmation", "escalate"],
  PENDING_VERIFICATION: ["Verification", "refer"], REDIRECTED: ["Redirected", "refer"],
  DECLINED: ["Declined", "block"], BLOCKED: ["Blocked", "block"], REJECTED: ["Rejected", "block"],
};

export function Status({ status, stamp }: { status: string; stamp?: boolean }) {
  const [label, tone] = STATUS[status] ?? [status, "quiet"];
  return <span className={`${stamp ? "stamp" : "pill"} t-${tone}`} style={stamp ? { color: `var(--${tone})` } : undefined}>{label}</span>;
}

export function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(load, deps);
  const reload = useCallback(() => {
    setLoading(true);
    run().then((d) => { setData(d); setError(null); })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [run]);
  useEffect(() => { reload(); }, [reload]);
  return { data, error, loading, reload, setData };
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export function slaText(hours: number | null): string {
  if (hours === null) return "";
  if (hours < 0) return `${Math.abs(Math.round(hours))}h past SLA`;
  return hours < 24 ? `${Math.round(hours)}h left` : `${Math.round(hours / 24)}d left`;
}

export const LANES: Record<string, string> = {
  "Custody operations": "--lane-ops", "AI assist services": "--lane-ai", "Rules and controls engine": "--lane-rules",
  "AML compliance": "--lane-aml", "Sanctions team": "--lane-sanctions", "Ledger and account services": "--lane-ledger",
};

export function Loading({ what = "Loading" }: { what?: string }) {
  return <p className="muted">{what}…</p>;
}
