import { useCallback, useEffect, useState } from "react";

export const STATUS: Record<string, [string, string]> = {
  ACTIVE: ["Active", "clear"], OPEN: ["Open", "clear"], APPLIED: ["Applied", "clear"],
  PENDING_AI_REVIEW: ["AI review", "refer"], PENDING_ITEMS: ["Items requested", "refer"], RETURNED: ["Returned", "refer"],
  PENDING_REVIEW: ["Compliance review", "refer"], PENDING_ALERT_REVIEW: ["PEP or media alert", "escalate"],
  PENDING_FRAUD_REVIEW: ["Fraud review", "block"],
  PENDING_SANCTIONS: ["Sanctions review", "escalate"],
  PENDING_EDD: ["EDD pending", "escalate"], PENDING_CONFIRMATION: ["Confirmation", "escalate"],
  PENDING_VERIFICATION: ["Verification", "refer"], REDIRECTED: ["Redirected", "refer"],
  PENDING_ACCEPTANCE: ["Principal acceptance", "refer"], OPEN_RESTRICTED: ["Open, restricted", "refer"],
  PENDING_REWORK: ["Rework", "escalate"],
  DECLINED: ["Declined", "block"], BLOCKED: ["Blocked", "block"], REJECTED: ["Rejected", "block"],
  // The portal's own words (app/portal_view.py): every review state is "In review"
  IN_REVIEW: ["In review", "refer"], CONFIRMING: ["Confirming with you", "refer"], SUBMITTED: ["Submitted", "quiet"],
  PENDING: ["Opening", "quiet"],
  // Release 2: money movement and transfers
  PENDING_RELEASE: ["Waiting for release", "refer"], PENDING_CALLBACK: ["Callback", "escalate"],
  PENDING_RISK_REVIEW: ["Risk review", "escalate"], PENDING_FIX: ["Fix before sending", "refer"],
  PENDING_CORRECTION: ["Correction", "escalate"], OVERDUE: ["Overdue", "block"],
  AWAITING_SETTLEMENT: ["In transit", "quiet"], AWAITING_REREGISTRATION: ["LOI out", "quiet"],
  WAITING_PRODUCT: ["Product acceptance", "refer"], COMPLETED: ["Completed", "clear"],
  IN_TRANSIT: ["In transit", "quiet"], WITHDRAWN: ["Withdrawn", "quiet"], ON_HOLD: ["Temporary hold", "refer"], BANK_RETURNED: ["Returned by the bank", "refer"], CLOSED: ["Closed", "quiet"],
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

export type Sla = { due: string | null; breached: boolean; hours_left: number | null; business_hours_left?: number | null;
  waiting_on?: string; since?: string | null; next_reminder?: string | null; withdraw_on?: string | null };

const shortDate = (d: string) => new Date(`${d}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });

/** The clock in plain words: business hours left on the team's SLA, or, while a case waits on the RIA or the client,
 * when the next reminder goes and when the request is withdrawn (app/sla.py). */
export function slaText(sla: Sla): string {
  if (sla.waiting_on) {
    const parts = [`Waiting on ${sla.waiting_on}`];
    if (sla.next_reminder) parts.push(`reminder ${shortDate(sla.next_reminder)}`);
    if (sla.withdraw_on) parts.push(`withdrawn ${shortDate(sla.withdraw_on)}`);
    return parts.join(" · ");
  }
  const hours = sla.hours_left;
  if (hours === null) return "";
  if (hours < 0) return `${Math.abs(Math.round(hours))}h past SLA`;
  const left = sla.business_hours_left ?? hours;
  return left < 24 ? `${Math.round(left)} business hours left` : `${Math.round(left / 24)} business days left`;
}

export const LANES: Record<string, string> = {
  "Custody operations": "--lane-ops", "AI assist services": "--lane-ai", "Rules and controls engine": "--lane-rules",
  "AML compliance": "--lane-aml", "Sanctions team": "--lane-sanctions", "Ledger and account services": "--lane-ledger",
  "RIA service team": "--lane-principal",
};

export function Loading({ what = "Loading" }: { what?: string }) {
  return <p className="muted">{what}…</p>;
}
