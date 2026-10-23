import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { api, type CaseDetail } from "./api";

/** Release 2 pieces shared by the case page and the money movement pages: amounts, the summary a workflow writes,
 *  the people-steps it opens (app/actions.py), and its ledger entries at all three layers. */

export type ActionField = { name: string; label: string; type: "text" | "textarea" | "select"; options?: [string, string][];
  default?: string };
export type CaseAction = { key: string; label: string; help: string; fields: ActionField[]; demo_only: boolean };
export type LedgerLeg = { layer: string; account: string; asset: string; bucket: string; amount: number };
export type LedgerEntry = { id: number; ref: string; kind: string; description: string; case_id: number | null;
  account_id: string | null; effective_date: string; created_by: string; legs: LedgerLeg[] };
export type R2Case = CaseDetail & { map?: string | null; summary?: Record<string, string>; actions?: CaseAction[];
  ledger_entries?: LedgerEntry[]; linked?: { id: number; title: string; status: string; relation: string }[];
  expected?: string | null };

export const usd = (n: number | null | undefined, signed = false) =>
  n === null || n === undefined ? "" : `${signed && n > 0 ? "+" : ""}${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined,
    { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export const qty = (n: number | null | undefined) =>
  n === null || n === undefined ? "" : n.toLocaleString(undefined, { maximumFractionDigits: 6 });
export const day = (iso: string | null | undefined) =>
  iso ? new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "";

export const LAYER: Record<string, string> = { client: "Client", master: "RIA master", gl: "General ledger" };
export const BUCKET: Record<string, string> = { settled: "Settled", pending: "Pending", restricted: "Restricted" };

export function Summary({ summary }: { summary: Record<string, string> }) {
  const rows = Object.entries(summary);
  if (!rows.length) return null;
  return (
    <dl className="details">
      {rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
    </dl>
  );
}

/** The people-steps open to this person on this case, each a small form. */
export function Actions({ caseId, actions, onDone, onFail }: { caseId: number; actions: CaseAction[];
  onDone: (c: CaseDetail, msg: string) => void; onFail: (e: Error) => void }) {
  const single = actions.length === 1;
  const [open, setOpen] = useState<string | null>(single ? actions[0].key : null);
  const [data, setData] = useState<Record<string, Record<string, string>>>({});
  const [busy, setBusy] = useState(false);
  if (!actions.length) return null;
  const value = (a: CaseAction, f: ActionField) => data[a.key]?.[f.name] ?? f.default ?? (f.options?.[0]?.[0] ?? "");
  const submit = (a: CaseAction) => {
    setBusy(true);
    const body: Record<string, string> = {};
    a.fields.forEach((f) => { body[f.name] = value(a, f); });
    api<{ message: string; case: CaseDetail }>(`/api/cases/${caseId}/actions/${a.key}`, { body: { data: body } })
      .then((r) => { onDone(r.case, r.message); setOpen(null); }).catch(onFail).finally(() => setBusy(false));
  };
  return (
    <div className="decide">
      <h3>What this case needs from you</h3>
      {actions.map((a) => (
        <div key={a.key} className="r2-action">
          {single ? <h4>{a.label}{a.demo_only ? " (demo)" : ""}</h4> : (
            <button className={open === a.key ? "" : "secondary"} onClick={() => setOpen(open === a.key ? null : a.key)}>
              {a.label}{a.demo_only ? " (demo)" : ""}
            </button>)}
          {(single || open === a.key) && (
            <div className="r2-action-form">
              {a.help && <p className="small muted">{a.help}</p>}
              {a.fields.map((f) => (
                <label key={f.name}>{f.label}
                  {f.type === "select" ? (
                    <select value={value(a, f)} onChange={(e) => setData({ ...data, [a.key]: { ...data[a.key], [f.name]: e.target.value } })}>
                      {(f.options ?? []).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </select>
                  ) : f.type === "textarea" ? (
                    <textarea value={value(a, f)} onChange={(e) => setData({ ...data, [a.key]: { ...data[a.key], [f.name]: e.target.value } })} />
                  ) : (
                    <input value={value(a, f)} onChange={(e) => setData({ ...data, [a.key]: { ...data[a.key], [f.name]: e.target.value } })} />
                  )}
                </label>
              ))}
              <div className="actions"><button disabled={busy} onClick={() => submit(a)}>{busy ? "Working…" : a.label}</button></div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/** Ledger entries with their legs: every entry balances, and each layer rolls up to the next. */
export function Entries({ entries, compact }: { entries: LedgerEntry[]; compact?: boolean }) {
  const [open, setOpen] = useState<number | null>(compact ? null : entries[0]?.id ?? null);
  if (!entries.length) return <div className="empty">Nothing posted yet.</div>;
  return (
    <div className="scroll"><table>
      <thead><tr><th>Effective</th><th>Entry</th><th>Posted by</th><th>Legs</th></tr></thead>
      <tbody>{entries.map((e) => (
        <Fragment key={e.id}>
          <tr className="link" onClick={() => setOpen(open === e.id ? null : e.id)} aria-expanded={open === e.id}>
            <td>{day(e.effective_date)}</td>
            <td>{e.description}<div className="small mono muted">{e.ref}{e.case_id ? <> · <Link to={`/cases/${e.case_id}`}>#{e.case_id}</Link></> : null}</div></td>
            <td className="small">{e.created_by}</td><td>{e.legs.length}</td>
          </tr>
          {open === e.id && <tr className="detail"><td colSpan={4}>
            <table className="legs"><thead><tr><th>Layer</th><th>Account</th><th>Asset</th><th>Bucket</th><th className="num">Amount</th></tr></thead>
              <tbody>{e.legs.map((l, i) => (
                <tr key={i}><td>{LAYER[l.layer] ?? l.layer}</td><td className="mono">{l.account}</td>
                  <td className="mono">{l.asset}</td><td>{BUCKET[l.bucket] ?? l.bucket}</td>
                  <td className="num">{l.asset === "USD" ? usd(l.amount, true) : `${l.amount > 0 ? "+" : ""}${qty(l.amount)}`}</td></tr>
              ))}</tbody></table>
          </td></tr>}
        </Fragment>
      ))}</tbody>
    </table></div>
  );
}

export function Linked({ linked }: { linked: { id: number; title: string; status: string; relation: string }[] }) {
  if (!linked.length) return null;
  return <ul className="planned-list">{linked.map((l) => (
    <li key={l.id}>{l.relation === "opened" ? "Opened" : "Part of"} <Link to={`/cases/${l.id}`}>#{l.id} {l.title}</Link> ({l.status})</li>))}</ul>;
}
