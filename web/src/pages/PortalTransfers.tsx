import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { day, usd } from "../r2";
import { Loading, Status, useLoad } from "../util";
import { AccountLedger, type AccountRow } from "./MoneyMovement";

/** Release 2 in the portal: request money movement and transfers, follow each one, and see balances. An RIA works its
 *  firm's pure RIA accounts; a client their own self-directed account (deposits; outgoing money needs the RIA's grant). */

type Row = { id: number; label: string; account: string; client: string; amount: number | null; positions: number;
  status: string; created_at: string; expected: string | null };
type Security = { id: string; ticker: string | null; name: string; asset_type: string; price: number; accepted: boolean };

const RIA_TYPES: [string, string][] = [["ach_deposit", "ACH deposit (pull from the client's bank)"],
  ["ach_withdrawal", "ACH withdrawal (to the client's bank)"], ["wire_out", "Wire out"],
  ["journal", "Journal to another account"], ["acats_in", "ACATS transfer in"]];
const CLIENT_TYPES: [string, string][] = [["ach_deposit", "ACH deposit from your bank"]];

export default function PortalTransfers() {
  const { user } = useAuth();
  const client = user.role === "client";
  const list = useLoad(() => api<Row[]>("/api/money/transfers"), []);
  const [tab, setTab] = useState<"list" | "new" | "balances">("list");
  if (!list.data) return list.error ? <p className="error">{list.error}</p> : <Loading />;
  return (
    <>
      <h1>Transfers</h1>
      <p className="muted lead">{client
        ? "Add money to your self-directed account and follow each request. Moving money out needs a capability your RIA grants, which arrives next; you can always ask by signed request, or have another firm start a transfer."
        : "Request wires, ACH, journals, and ACATS transfers for your firm's accounts, and follow each one until it settles."}</p>
      <div className="tabs" role="tablist">
        {([["list", `Requests (${list.data.length})`], ["new", "New request"], ["balances", "Balances and activity"]] as [typeof tab, string][])
          .map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}
      </div>
      {tab === "list" && (list.data.length === 0 ? <div className="empty">No requests yet.</div> : <div className="scroll"><table>
        <thead><tr><th>Request</th><th>Account</th><th className="num">Amount</th><th>Status</th><th>Expected</th></tr></thead>
        <tbody>{list.data.map((r) => (
          <tr key={r.id}><td><Link to={`/portal/cases/${r.id}`}>{r.label}</Link><div className="small muted">#{r.id}, {day(r.created_at)}</div></td>
            <td><span className="mono">{r.account}</span><div className="small">{r.client}</div></td>
            <td className="num">{r.amount ? usd(r.amount) : r.positions ? `${r.positions} position${r.positions === 1 ? "" : "s"}` : ""}</td>
            <td><Status status={r.status} /></td><td>{r.status === "IN_TRANSIT" ? day(r.expected) : ""}</td></tr>))}</tbody>
      </table></div>)}
      {tab === "new" && <NewRequest client={client} onDone={() => { list.reload(); setTab("list"); }} />}
      {tab === "balances" && <AccountLedger portal />}
    </>
  );
}

function NewRequest({ client, onDone }: { client: boolean; onDone: () => void }) {
  const accounts = useLoad(() => api<AccountRow[]>("/api/money/accounts"), []);
  const securities = useLoad(() => api<Security[]>("/api/money/securities"), []);
  const [f, setF] = useState({ type: "ach_deposit", account_id: "", amount: "", destination: "", owner_name: "",
    institution: "", account_last4: "", prior_firm: "" });
  const [rows, setRows] = useState<{ security_id: string; quantity: string }[]>([{ security_id: "", quantity: "" }]);
  const [msg, setMsg] = useState<{ error?: string }>({});
  const [busy, setBusy] = useState(false);
  const set = (k: string, v: string) => setF({ ...f, [k]: v });
  const open = (accounts.data ?? []).filter((a) => a.status === "OPEN" && (client || a.group === "pure_ria"));
  const submit = () => {
    const body: any = { type: f.type, account_id: f.account_id, idempotency_key: `${f.type}-${f.account_id}-${Date.now()}` };
    if (f.type !== "acats_in") body.amount = Number(f.amount);
    if (f.type === "journal") body.destination = f.destination;
    if (f.type === "wire_out") body.counterparty = { owner_name: f.owner_name, institution: f.institution, account_last4: f.account_last4 };
    if (f.type === "acats_in") {
      body.positions = rows.filter((r) => r.security_id && Number(r.quantity) > 0).map((r) => ({ security_id: r.security_id, quantity: Number(r.quantity) }));
      body.counterparty = { institution: f.prior_firm };
    }
    setBusy(true);
    api<{ case: any }>("/api/money/transfers", { body }).then(() => onDone()).catch((e) => setMsg({ error: e.message }))
      .finally(() => setBusy(false));
  };
  return (
    <div className="panel" style={{ maxWidth: 640 }}>
      <label>Request
        <select value={f.type} onChange={(e) => set("type", e.target.value)}>
          {(client ? CLIENT_TYPES : RIA_TYPES).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select></label>
      <label>Account
        <select value={f.account_id} onChange={(e) => set("account_id", e.target.value)}>
          <option value="">Pick an account</option>
          {open.map((a) => <option key={a.id} value={a.id}>{a.id} {a.name} ({usd(a.cash)} available)</option>)}
        </select></label>
      {f.type !== "acats_in" && <label>Amount (USD)<input inputMode="decimal" value={f.amount} onChange={(e) => set("amount", e.target.value)} /></label>}
      {f.type === "journal" && <label>To account
        <select value={f.destination} onChange={(e) => set("destination", e.target.value)}>
          <option value="">Pick an account</option>
          {open.filter((a) => a.id !== f.account_id).map((a) => <option key={a.id} value={a.id}>{a.id} {a.name}</option>)}
        </select></label>}
      {f.type === "wire_out" && <>
        <label>Payee<input value={f.owner_name} onChange={(e) => set("owner_name", e.target.value)} /></label>
        <label>Receiving bank<input value={f.institution} onChange={(e) => set("institution", e.target.value)} /></label>
        <label>Account (last 4)<input maxLength={4} value={f.account_last4} onChange={(e) => set("account_last4", e.target.value)} /></label>
      </>}
      {f.type === "acats_in" && <>
        <label>Delivering firm<input value={f.prior_firm} onChange={(e) => set("prior_firm", e.target.value)} /></label>
        <p className="small muted">Positions on the prior firm's statement. Each runs through the transferability review: some move in kind,
          some by LOI, and a product the platform can't hold is sold there first.</p>
        {rows.map((r, i) => (
          <div key={i} className="row" style={{ gap: 6, marginBottom: 6 }}>
            <select value={r.security_id} onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, security_id: e.target.value } : x))}>
              <option value="">Security</option>
              {(securities.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.ticker ? `${s.ticker} ` : ""}{s.name}</option>)}
            </select>
            <input placeholder="Quantity" inputMode="decimal" value={r.quantity} style={{ maxWidth: 120 }}
              onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, quantity: e.target.value } : x))} />
          </div>))}
        <button className="small secondary" onClick={() => setRows([...rows, { security_id: "", quantity: "" }])}>Add a position</button>
      </>}
      <div className="actions"><button disabled={busy || !f.account_id} onClick={submit}>{busy ? "Sending…" : "Send the request"}</button></div>
      {msg.error && <p className="error">{msg.error}</p>}
    </div>
  );
}
