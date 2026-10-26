import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { day, Entries, qty, usd, type LedgerEntry } from "../r2";
import { Loading, Status, useLoad } from "../util";

/** Release 2: the asset transfer team's workbench (map 08). Every transfer is a case; this page is the list, the
 *  requests that arrive by mail or from a receiving firm, and a look at any account's three-layer ledger. */

const VIEWER = "https://shawngggg.github.io/AI-embedded-RIA-custody-operations-platform/";

type Row = { id: number; type: string; label: string; account: string; client: string; amount: number | null;
  positions: number; status: string; queue: string | null; firm: string | null; created_at: string;
  submitted: string | null; expected: string | null; requested_by: string | null; exception: string | null };
type Overview = { as_of: string; by_status: Record<string, number>; completed_today: number; can_enter: boolean;
  in_transit: { case: number; title: string; status: string; account: string; type: string; expected: string | null;
    overdue: boolean; items: number }[];
  requesters: Record<string, string>; types: Record<string, string> };
export type AccountRow = { id: string; name: string; status: string; group: string; registration: string; firm: string;
  cash: number; cash_pending: number; cash_restricted: number; value: number;
  sloas?: { id: string; payee: string; institution: string; account_last4: string }[] };
export type AccountDetail = { id: string; name: string; group: string; registration: string; status: string; firm: string;
  value: number; positions: { asset: string; name: string; ticker: string | null; quantity: number;
    buckets: Record<string, number>; price: number; value: number }[];
  bank_links: { id: string; bank: string; last4: string; owner: string; status: string }[];
  entries: any[]; restrictions: any[]; as_of: string };

const WAITING = ["PENDING_RELEASE", "PENDING_CALLBACK", "PENDING_RISK_REVIEW", "PENDING_SANCTIONS"];
const EXCEPTIONS = ["PENDING_FIX", "PENDING_CORRECTION", "OVERDUE", "WAITING_PRODUCT"];

export default function MoneyMovement() {
  const ov = useLoad(() => api<Overview>("/api/money/overview"), []);
  const list = useLoad(() => api<Row[]>("/api/money/transfers"), []);
  const [tab, setTab] = useState<"all" | "transit" | "enter" | "ledger">("all");
  const [filter, setFilter] = useState<"open" | "all" | "exceptions">("open");
  const [q, setQ] = useState("");
  if (!ov.data || !list.data) return ov.error || list.error ? <p className="error">{ov.error || list.error}</p> : <Loading />;
  const o = ov.data;
  const count = (keys: string[]) => keys.reduce((n, k) => n + (o.by_status[k] ?? 0), 0);
  const closed = ["COMPLETED", "REJECTED", "BLOCKED", "RETURNED"];
  const rows = list.data.filter((r) => filter === "all" || (filter === "open" ? !closed.includes(r.status) : EXCEPTIONS.includes(r.status)))
    .filter((r) => !q || `${r.id} ${r.account} ${r.client} ${r.label}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <>
      <h1>Money movement and transfers</h1>
      <p className="muted lead">Wires, ACH, journals, ACATS, and LOI re-registrations. Each request is a case: the rules check
        authority, codes, the flagged list, and transferability; a person releases money under maker-checker; the rail's answers
        settle it at three layers. Business day {day(o.as_of)}.</p>
      <div className="grid tiles">
        <Tile label="Waiting on a person" value={count(WAITING)} sub="Release, callback, risk review, sanctions" />
        <Tile label="Exceptions" value={count(EXCEPTIONS)} sub="Fixes, corrections, overdue" alert={count(EXCEPTIONS) > 0} />
        <Tile label="In transit" value={o.in_transit.length} sub={`${o.in_transit.filter((t) => t.overdue).length} past due`} />
        <Tile label="Completed today" value={o.completed_today} />
      </div>
      <div className="tabs" role="tablist">
        {([["all", `Transfers (${list.data.length})`], ["transit", `In transit (${o.in_transit.length})`],
          ...(o.can_enter ? [["enter", "Enter a request"]] : []), ["ledger", "Account ledger"]] as [typeof tab, string][])
          .map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}
      </div>
      {tab === "all" && <>
        <div className="filters">
          <select value={filter} onChange={(e) => setFilter(e.target.value as any)} aria-label="Show">
            <option value="open">Open</option><option value="exceptions">Exceptions</option><option value="all">All</option>
          </select>
          <input type="search" placeholder="Search account, client, or #" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {rows.length === 0 ? <div className="empty">Nothing here.</div> : <div className="scroll"><table>
          <thead><tr><th>Case</th><th>Type</th><th>Account</th><th className="num">Amount</th><th>Status</th><th>Requested by</th><th>Expected</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id}>
              <td><Link to={`/cases/${r.id}`}>#{r.id}</Link></td>
              <td>{r.label}{r.exception && <div className="small error">{r.exception}</div>}</td>
              <td><span className="mono">{r.account}</span><div className="small">{r.client}</div></td>
              <td className="num">{r.amount ? usd(r.amount) : r.positions ? `${r.positions} position${r.positions === 1 ? "" : "s"}` : ""}</td>
              <td><Status status={r.status} /></td>
              <td className="small">{r.requested_by}</td>
              <td>{day(r.expected)}</td>
            </tr>))}</tbody>
        </table></div>}
      </>}
      {tab === "transit" && (o.in_transit.length === 0 ? <div className="empty">Nothing in transit.</div> : <div className="scroll"><table>
        <thead><tr><th>Case</th><th>Type</th><th>Account</th><th>Status</th><th>Expected</th><th>Suspense items</th></tr></thead>
        <tbody>{o.in_transit.map((t) => (
          <tr key={t.case}><td><Link to={`/cases/${t.case}`}>#{t.case}</Link></td><td>{t.type}</td><td className="mono">{t.account}</td>
            <td><Status status={t.status} /></td><td className={t.overdue ? "error" : ""}>{day(t.expected)}{t.overdue ? " (past due)" : ""}</td>
            <td>{t.items}</td></tr>))}</tbody>
      </table></div>)}
      {tab === "enter" && <EnterRequest requesters={o.requesters} onDone={() => { list.reload(); ov.reload(); }} />}
      {tab === "ledger" && <AccountLedger />}
      <p className="small muted" style={{ marginTop: 18 }}>The process: <a href={`${VIEWER}#tr`} target="_blank" rel="noreferrer">asset and
        money transfers map</a>. The rails are simulated (principle 8): the daily settlement cycle delivers their answers on their dates.</p>
    </>
  );
}

function Tile({ label, value, sub, alert }: { label: string; value: React.ReactNode; sub?: string; alert?: boolean }) {
  return <div className="tile"><div className="label">{label}</div><div className="value">{value}</div>
    {sub && <div className={`sub ${alert ? "alert" : ""}`}>{sub}</div>}</div>;
}

/** Operations enters only what arrived another way: a client-signed request by mail, or a receiving firm's ACATS request. */
function EnterRequest({ requesters, onDone }: { requesters: Record<string, string>; onDone: () => void }) {
  const accounts = useLoad(() => api<AccountRow[]>("/api/money/accounts"), []);
  const [f, setF] = useState({ requester: "signed_form", account_id: "", type: "wire_out", amount: "", owner_name: "",
    institution: "", account_last4: "", client_signed: true, receiving_firm: "" });
  const [msg, setMsg] = useState<{ ok?: string; error?: string; id?: number }>({});
  const set = (k: string, v: any) => setF({ ...f, [k]: v });
  const submit = () => {
    const body: any = { account_id: f.account_id, requester: f.requester };
    if (f.requester === "receiving_firm") {
      Object.assign(body, { type: "acats_out", counterparty: { institution: f.receiving_firm } });
    } else {
      Object.assign(body, { type: f.type, amount: Number(f.amount), client_signed: f.client_signed,
        counterparty: { owner_name: f.owner_name || undefined, institution: f.institution || undefined,
          account_last4: f.account_last4 || undefined } });
    }
    api<{ case: any }>("/api/money/transfers", { body }).then((r) => {
      setMsg({ ok: `Case #${r.case.id} opened: ${r.case.status.replace(/_/g, " ").toLowerCase()}.`, id: r.case.id }); onDone();
    }).catch((e) => setMsg({ error: e.message }));
  };
  return (
    <div className="panel" style={{ maxWidth: 640 }}>
      <p className="small muted">Operations doesn't originate money movement. It enters what arrived another way, and the same
        checks run as for any request.</p>
      <label>What arrived
        <select value={f.requester} onChange={(e) => set("requester", e.target.value)}>
          {Object.entries(requesters).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select></label>
      <label>Account
        <select value={f.account_id} onChange={(e) => set("account_id", e.target.value)}>
          <option value="">Pick an account</option>
          {(accounts.data ?? []).map((a) => <option key={a.id} value={a.id}>{a.id} {a.name} ({usd(a.cash)} cash)</option>)}
        </select></label>
      {f.requester === "receiving_firm" ? (
        <label>Receiving firm<input value={f.receiving_firm} onChange={(e) => set("receiving_firm", e.target.value)} /></label>
      ) : <>
        <label>Request
          <select value={f.type} onChange={(e) => set("type", e.target.value)}>
            <option value="wire_out">Wire out</option><option value="ach_withdrawal">ACH withdrawal</option>
          </select></label>
        <label>Amount (USD)<input inputMode="decimal" value={f.amount} onChange={(e) => set("amount", e.target.value)} /></label>
        {f.type === "wire_out" && <>
          <label>Payee<input value={f.owner_name} onChange={(e) => set("owner_name", e.target.value)} /></label>
          <label>Receiving bank<input value={f.institution} onChange={(e) => set("institution", e.target.value)} /></label>
          <label>Account (last 4)<input maxLength={4} value={f.account_last4} onChange={(e) => set("account_last4", e.target.value)} /></label>
        </>}
        <label className="inline-check"><input type="checkbox" checked={f.client_signed} onChange={(e) => set("client_signed", e.target.checked)} />
          The client signed it</label>
      </>}
      <div className="actions"><button disabled={!f.account_id} onClick={submit}>Open the case</button></div>
      {msg.ok && <p className="ok">{msg.ok} <Link to={`/cases/${msg.id}`}>Open it</Link></p>}
      {msg.error && <p className="error">{msg.error}</p>}
    </div>
  );
}

/** Any account's balances by bucket and its ledger entries at all three layers. */
export function AccountLedger({ portal }: { portal?: boolean }) {
  const accounts = useLoad(() => api<AccountRow[]>("/api/money/accounts"), []);
  const [id, setId] = useState("");
  const detail = useLoad(() => id ? api<AccountDetail>(`/api/money/accounts/${id}`) : Promise.resolve(null as any), [id]);
  const d = detail.data as AccountDetail | null;
  const cash = useMemo(() => d?.positions.find((p) => p.asset === "USD"), [d]);
  const stmts = useLoad(() => id ? api<{ id: number; period_start: string; period_end: string; sha256: string }[]>(`/api/statements?account_id=${id}`)
    : Promise.resolve([]), [id]);
  return (
    <>
      <label style={{ maxWidth: 420, display: "block" }}>Account
        <select value={id} onChange={(e) => setId(e.target.value)}>
          <option value="">Pick an account</option>
          {(accounts.data ?? []).map((a) => <option key={a.id} value={a.id}>{a.id} {a.name}</option>)}
        </select></label>
      {id && !d && <Loading />}
      {d && <>
        <div className="grid tiles" style={{ marginTop: 12 }}>
          <Tile label="Total value" value={usd(d.value)} sub={`${d.registration}${d.group === "self_directed" ? ", self-directed" : ""}`} />
          <Tile label="Cash: settled" value={usd(cash?.buckets.settled ?? 0)} />
          <Tile label="Cash: pending or restricted" value={usd((cash?.buckets.pending ?? 0) + (cash?.buckets.restricted ?? 0))}
            sub="In transit, or returnable ACH funds on hold" />
          <Tile label="Bank links" value={d.bank_links.length} sub={d.bank_links.map((b) => `${b.bank} ...${b.last4} (${b.status})`).join("; ")} />
        </div>
        {d.restrictions.length > 0 && <p>{d.restrictions.map((r: any, i: number) => portal
          ? <span key={i} className="pill t-refer">{r.reason}: {r.limits.join(", ")}</span>
          : <span key={i} className="code">{r.code}</span>)}</p>}
        <h3>Positions</h3>
        <div className="scroll"><table>
          <thead><tr><th>Holding</th><th className="num">Settled</th><th className="num">Pending</th><th className="num">Restricted</th><th className="num">Value</th></tr></thead>
          <tbody>{d.positions.map((p) => (
            <tr key={p.asset}><td>{p.ticker ? <strong>{p.ticker} </strong> : null}{p.name}</td>
              <td className="num">{p.asset === "USD" ? usd(p.buckets.settled ?? 0) : qty(p.buckets.settled ?? 0)}</td>
              <td className="num">{p.asset === "USD" ? usd(p.buckets.pending ?? 0) : qty(p.buckets.pending ?? 0)}</td>
              <td className="num">{p.asset === "USD" ? usd(p.buckets.restricted ?? 0) : qty(p.buckets.restricted ?? 0)}</td>
              <td className="num">{usd(p.value)}</td></tr>))}</tbody>
        </table></div>
        <h3>{portal ? "Activity" : "Ledger entries"}</h3>
        {portal ? <Activity entries={d.entries} /> : <Entries entries={d.entries as LedgerEntry[]} compact />}
        <h3>Quarterly statements</h3>
        {(stmts.data ?? []).length === 0 ? <div className="empty">No statements yet.</div> : <ul className="small">{(stmts.data ?? []).map((st) => (
          <li key={st.id}><a href={`/api/statements/${st.id}/file`} target="_blank" rel="noreferrer">{st.period_start} to {st.period_end}</a>
            {!portal && <span className="muted mono"> · SHA-256 {st.sha256.slice(0, 12)}…</span>}</li>))}</ul>}
      </>}
    </>
  );
}

function Activity({ entries }: { entries: { date: string; description: string; legs: { asset: string; bucket: string; amount: number }[] }[] }) {
  if (!entries.length) return <div className="empty">No activity yet.</div>;
  return <div className="scroll"><table>
    <thead><tr><th>Date</th><th>Activity</th><th>Change</th></tr></thead>
    <tbody>{entries.map((e, i) => (
      <tr key={i}><td>{day(e.date)}</td><td>{e.description}</td>
        <td className="small">{e.legs.map((l, j) => <div key={j}>{l.asset === "USD" ? usd(l.amount, true) : `${l.amount > 0 ? "+" : ""}${qty(l.amount)} ${l.asset}`}
          {l.bucket !== "settled" ? ` (${l.bucket})` : ""}</div>)}</td></tr>))}</tbody>
  </table></div>;
}
