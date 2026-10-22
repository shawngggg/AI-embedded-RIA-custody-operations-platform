import { Fragment, useState, type ReactNode } from "react";
import { api } from "../api";
import { Loading, useLoad } from "../util";

/**
 * Read-only previews of the release 2 and 3 workbenches, from the synthetic book (app/routers/preview.py). Operations
 * sees its whole area; an RIA its own firm's accounts; a client their own account.
 */
export type PreviewKey = "transfers" | "trading" | "billing" | "settlement";

type Row = Record<string, any>;
type Data = Row & { area: PreviewKey; release: number; as_of: string; label: string; assumptions: string[] };

const usd = (n: number | null | undefined, signed = false) =>
  n === null || n === undefined ? "" : `${signed && n > 0 ? "+" : ""}${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const qty = (n: number | null | undefined) =>
  n === null || n === undefined ? "" : n.toLocaleString(undefined, { maximumFractionDigits: 3 });
const day = (iso: string | null | undefined) =>
  iso ? new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "";
const pct = (n: number) => `${(n * 100).toFixed(2)}%`;

const ROUTE: Record<string, string> = {
  acats_in_kind: "ACATS in kind", loi: "LOI to the sponsor", liquidate: "Liquidate first",
  product_acceptance: "Product acceptance", review: "Manual review",
};
const RELEASE_TONE: Record<string, string> = { full: "clear", partial: "refer", hold: "escalate" };

function tone(status: string): string {
  const s = status.toLowerCase();
  if (/blocked|rejected|can't|escalated|fail|off by/.test(s)) return "block";
  if (/returned|held|hold|suspense|short|refund|waiting|open|partial|researching|allocating|dk/.test(s)) return "refer";
  if (/completed|settled|posted|filled|matches|priced|approved|re-registered|sent|allocated/.test(s)) return "clear";
  return "quiet";
}

const Pill = ({ children, t }: { children: ReactNode; t?: string }) =>
  <span className={`pill t-${t ?? tone(String(children))}`}>{children}</span>;

function Tile({ label, value, sub, alert }: { label: string; value: ReactNode; sub?: string; alert?: boolean }) {
  return <div className="tile"><div className="label">{label}</div><div className="value">{value}</div>
    {sub && <div className={`sub ${alert ? "alert" : ""}`}>{sub}</div>}</div>;
}

function Tabs<T extends string>({ tabs, on, set }: { tabs: [T, string][]; on: T; set: (t: T) => void }) {
  return <div className="tabs" role="tablist">{tabs.map(([k, label]) =>
    <button key={k} role="tab" aria-selected={on === k} className={on === k ? "on" : ""} onClick={() => set(k)}>{label}</button>)}</div>;
}

const Empty = ({ what }: { what: string }) => <div className="empty">{what}</div>;

export default function Preview({ which, audience }: { which: PreviewKey; audience: "portal" | "ops" }) {
  const p = useLoad(() => api<Data>(`/api/preview/${which}`), [which]);
  if (!p.data) return p.error ? <p className="error">{p.error}</p> : <Loading what="Loading the preview" />;
  const d = p.data;
  const ops = audience === "ops";
  return (
    <section className="preview" aria-label="Preview">
      <div className="preview-banner">
        <strong>Preview</strong>
        <span>{d.label} Business day {day(d.as_of)}.</span>
      </div>
      {d.area === "transfers" && <Transfers d={d} ops={ops} />}
      {d.area === "trading" && <Trading d={d} ops={ops} />}
      {d.area === "billing" && <Billing d={d} ops={ops} />}
      {d.area === "settlement" && <Settlement d={d} />}
      <details className="small preview-notes">
        <summary>What's synthetic here</summary>
        <ul>{d.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
      </details>
    </section>
  );
}

// ------------------------------------------------------------------ transfers and money movement
function Transfers({ d, ops }: { d: Data; ops: boolean }) {
  const [tab, setTab] = useState<"transfers" | "money">("transfers");
  const [open, setOpen] = useState<string | null>(null);
  const [exceptionsOnly, setExceptionsOnly] = useState(false);
  const transfers: Row[] = d.transfers;
  const moves: Row[] = d.movements;
  const running = transfers.filter((t) => t.direction === "Incoming" && !/^Completed|^Re-registered/.test(t.status));
  const exception = (m: Row) => !["Settled", "Sent"].includes(m.status) && !m.status.startsWith("Pending");
  const exceptions = moves.filter(exception);
  const shown = exceptionsOnly ? exceptions : moves;
  return (
    <>
      <div className="grid tiles">
        <Tile label="Incoming, in progress" value={running.length} sub="ACATS and LOI" />
        <Tile label="Completed lately" value={transfers.filter((t) => /^Completed|^Re-registered/.test(t.status)).length} sub="Last 15 business days" />
        <Tile label="Outgoing" value={transfers.filter((t) => t.direction === "Outgoing").length} />
        <Tile label="Money movement exceptions" value={exceptions.length} sub={exceptions.length ? "Returns, holds, suspense" : "None"} alert={exceptions.length > 0} />
      </div>
      <Tabs tabs={[["transfers", `Transfers (${transfers.length})`], ["money", `Money movement (${moves.length})`]]} on={tab} set={setTab} />
      {tab === "transfers" && (transfers.length === 0 ? <Empty what="No transfers in the last 15 business days." /> :
        <div className="scroll"><table>
          <thead><tr><th>Requested</th><th>Transfer</th><th>Account</th><th>Other firm</th><th>Business days</th><th>Status</th><th>Release</th></tr></thead>
          <tbody>{transfers.map((t) => (
            <Fragment key={t.id}>
              <tr className="link" onClick={() => setOpen(open === t.id ? null : t.id)} aria-expanded={open === t.id}>
                <td>{day(t.requested)}</td>
                <td><span className="mono">{t.id}</span><div className="small muted">{t.direction} · {t.type}</div></td>
                <td><span className="mono">{t.account}</span><div className="small">{t.client}</div></td>
                <td>{t.other_firm}</td><td>{t.business_days}</td>
                <td>{t.status}</td>
                <td><Pill t={RELEASE_TONE[t.release]}>{t.release}</Pill></td>
              </tr>
              {open === t.id && <tr className="detail"><td colSpan={7}>
                {t.note && <p style={{ marginTop: 0 }}>{t.note}</p>}
                <table><thead><tr><th>Position</th><th>Quantity</th><th>Route</th><th>Why</th></tr></thead>
                  <tbody>{t.positions.map((x: Row, i: number) => (
                    <tr key={i}><td>{x.security}</td><td>{qty(x.quantity)}</td><td>{ROUTE[x.route] ?? x.route}</td>
                      <td>{x.reason}{x.action ? <div className="small muted">{x.action}</div> : null}</td></tr>))}</tbody>
                </table>
                <p className="small muted" style={{ marginBottom: 0 }}>Routes come from the platform's transferability rules.</p>
              </td></tr>}
            </Fragment>))}</tbody>
        </table></div>)}
      {tab === "money" && <>
        {ops && <label className="small inline-check"><input type="checkbox" checked={exceptionsOnly}
          onChange={(e) => setExceptionsOnly(e.target.checked)} /> Exceptions only</label>}
        {shown.length === 0 ? <Empty what="No money movement in the last 10 business days." /> :
          <div className="scroll"><table>
            <thead><tr><th>Date</th><th>Account</th><th>Type</th><th style={{ textAlign: "right" }}>Amount</th><th>Rail</th><th>Status</th><th>Note</th></tr></thead>
            <tbody>{shown.map((m) => (
              <tr key={m.id}>
                <td>{day(m.date)}</td>
                <td>{m.account ? <><span className="mono">{m.account}</span><div className="small">{m.client}</div></> : <span className="muted">No match</span>}</td>
                <td>{m.type}{m.counterparty && <div className="small muted">{m.counterparty}</div>}</td>
                <td className="num">{usd(m.direction === "In" ? m.amount : -m.amount, true)}</td>
                <td>{m.rail}</td>
                <td>{exception(m) ? <Pill>{m.status}</Pill> : m.status}</td>
                <td className="small">{m.note}</td>
              </tr>))}</tbody>
          </table></div>}
      </>}
    </>
  );
}

// ------------------------------------------------------------------ trading
function Trading({ d, ops }: { d: Data; ops: boolean }) {
  type T = "orders" | "fails" | "holdings" | "accounts" | "positions";
  const tabs: [T, string][] = ops
    ? [["orders", "Blotter"], ["fails", `Fails (${d.fails.length})`], ["holdings", "Holdings by security"], ["accounts", "Accounts"]]
    : [["orders", "Orders"], ["positions", "Positions"]];
  const [tab, setTab] = useState<T>("orders");
  const [open, setOpen] = useState<string | null>(null);
  const orders: Row[] = [...d.orders].sort((a, b) => b.trade_date.localeCompare(a.trade_date));
  const allocs: Row[] = d.allocations;
  const settling = allocs.filter((x) => x.settle_date === d.as_of && x.quantity);
  const settleNet = settling.reduce((n, x) => n + (x.side === "Sell" ? x.amount : -x.amount), 0);
  const today = orders.filter((o) => o.trade_date === d.as_of);
  const working = orders.filter((o) => /partial|open|waiting/i.test(o.status));
  return (
    <>
      <div className="grid tiles">
        <Tile label="Orders today" value={today.length} sub={`${orders.length - today.length} yesterday`} />
        <Tile label="Working" value={working.length} sub="Partial fills, open limits, NAV pending" />
        <Tile label="Settling today, net" value={usd(settleNet, true)}
          sub={`${settleNet >= 0 ? "Receive" : "Pay"}: ${settling.length} allocations from yesterday (T+1)`} />
        {ops ? <Tile label="Open fails" value={d.fails.length} sub={d.fails.length ? "Oldest first in the Fails tab" : "None"} alert={d.fails.length > 0} />
          : <Tile label="Positions" value={d.positions.length} />}
      </div>
      <Tabs tabs={tabs} on={tab} set={setTab} />
      {tab === "orders" && (orders.length === 0 ? <Empty what="No orders today or yesterday." /> :
        <div className="scroll"><table>
          <thead><tr><th>Trade date</th><th>Order</th><th>Entered by</th><th>Side</th><th>Security</th><th style={{ textAlign: "right" }}>Quantity</th><th style={{ textAlign: "right" }}>Price</th><th>Status</th><th>Settles</th></tr></thead>
          <tbody>{orders.map((o) => {
            const mine = allocs.filter((x) => x.order === o.order);
            return (
              <Fragment key={o.order}>
                <tr className="link" onClick={() => setOpen(open === o.order ? null : o.order)} aria-expanded={open === o.order}>
                  <td>{day(o.trade_date)}</td>
                  <td><span className="mono">{o.order}</span>{ops && <div className="small muted">{o.firm}</div>}</td>
                  <td>{o.entered_by}<div className="small muted">{o.venue}</div></td>
                  <td>{o.side}</td>
                  <td><strong>{o.ticker}</strong><div className="small">{o.security}</div></td>
                  <td className="num">{o.quantity === null ? usd(o.amount) : <>{qty(o.filled)} of {qty(o.quantity)}</>}</td>
                  <td className="num">{o.avg_price ? usd(o.avg_price) : o.limit ? `Limit ${usd(o.limit)}` : ""}</td>
                  <td><Pill>{o.status}</Pill></td>
                  <td>{day(o.settle_date)}<div className="small muted">{o.settles_through}</div></td>
                </tr>
                {open === o.order && <tr className="detail"><td colSpan={9}>
                  {mine.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nothing allocated yet.</p> :
                    <table><thead><tr><th>Account</th><th style={{ textAlign: "right" }}>Ordered</th><th style={{ textAlign: "right" }}>Allocated</th><th style={{ textAlign: "right" }}>Price</th><th style={{ textAlign: "right" }}>Amount</th><th>Status</th></tr></thead>
                      <tbody>{mine.map((x) => (
                        <tr key={x.account}><td><span className="mono">{x.account}</span> {x.client}</td><td className="num">{qty(x.ordered)}</td>
                          <td className="num">{qty(x.quantity)}</td><td className="num">{usd(x.price)}</td><td className="num">{usd(x.amount)}</td><td>{x.status}</td></tr>))}</tbody>
                    </table>}
                  {o.accounts > 1 && <p className="small muted" style={{ marginBottom: 0 }}>Every account gets the same average price.</p>}
                </td></tr>}
              </Fragment>);
          })}</tbody>
        </table></div>)}
      {tab === "fails" && (d.fails.length === 0 ? <Empty what="No open fails." /> :
        <div className="scroll"><table>
          <thead><tr><th>Fail</th><th>Account</th><th>Security</th><th style={{ textAlign: "right" }}>Amount</th><th>Settlement date</th><th>Age</th><th>Cause</th><th>Next action</th></tr></thead>
          <tbody>{(d.fails as Row[]).map((f) => (
            <tr key={f.id}>
              <td><Pill t="block">{f.type}</Pill><div className="small mono">{f.id}</div></td>
              <td><span className="mono">{f.account}</span><div className="small">{f.client}</div></td>
              <td>{f.side} {qty(f.quantity)} {f.ticker}</td><td className="num">{usd(f.amount)}</td>
              <td>{day(f.settle_date)}</td><td>{f.age_days} business day{f.age_days === 1 ? "" : "s"}</td>
              <td className="small">{f.cause}</td>
              <td className="small">{f.action}{f.close_out_by && <div><strong>Close out by {day(f.close_out_by)}</strong></div>}</td>
            </tr>))}</tbody>
        </table></div>)}
      {tab === "holdings" && <div className="scroll"><table>
        <thead><tr><th>Security</th><th>CUSIP</th><th>Type</th><th>Held at</th><th style={{ textAlign: "right" }}>Accounts</th><th style={{ textAlign: "right" }}>Quantity</th><th style={{ textAlign: "right" }}>Market value</th></tr></thead>
        <tbody>{(d.holdings as Row[]).map((h) => (
          <tr key={h.ticker}><td><strong>{h.ticker}</strong><div className="small">{h.security}</div></td><td className="mono">{h.cusip}</td>
            <td>{h.asset_type.replace("_", " ")}</td><td>{h.location}</td><td className="num">{h.accounts}</td>
            <td className="num">{qty(h.quantity)}</td><td className="num">{usd(h.market_value)}</td></tr>))}</tbody>
      </table></div>}
      {tab === "accounts" && <div className="scroll"><table>
        <thead><tr><th>Account</th><th>RIA</th><th>Registration</th><th>Model</th><th>Opened</th><th>Codes</th><th style={{ textAlign: "right" }}>Cash</th><th style={{ textAlign: "right" }}>Value</th></tr></thead>
        <tbody>{(d.accounts as Row[]).map((a) => (
          <tr key={a.account}><td><span className="mono">{a.account}</span><div className="small">{a.client}</div></td><td className="small">{a.firm}</td>
            <td>{a.registration}{a.group === "self_directed" ? " (self-directed)" : ""}</td>
            <td>{a.funding_in_transit ? <span className="muted">Transfer in progress</span> : a.model}</td><td>{day(a.opened)}</td>
            <td>{a.codes.map((c: string) => <span key={c} className="code">{c}</span>)}</td>
            <td className="num">{usd(a.cash)}</td><td className="num">{usd(a.value)}</td></tr>))}</tbody>
      </table></div>}
      {tab === "positions" && (d.positions.length === 0 ? <Empty what="No positions yet: they appear when the incoming transfer completes." /> :
        <div className="scroll"><table>
          <thead><tr><th>Account</th><th>Security</th><th style={{ textAlign: "right" }}>Quantity</th><th style={{ textAlign: "right" }}>Price</th><th style={{ textAlign: "right" }}>Market value</th></tr></thead>
          <tbody>{(d.positions as Row[]).map((x) => (
            <tr key={`${x.account}-${x.ticker}`}><td><span className="mono">{x.account}</span><div className="small">{x.client}</div></td>
              <td><strong>{x.ticker}</strong><div className="small">{x.security}</div></td><td className="num">{qty(x.quantity)}</td>
              <td className="num">{usd(x.price)}</td><td className="num">{usd(x.market_value)}</td></tr>))}</tbody>
        </table></div>)}
    </>
  );
}

// ------------------------------------------------------------------ billing
function Billing({ d, ops }: { d: Data; ops: boolean }) {
  const runs: Row[] = d.runs;
  const fees: Row[] = d.fees;
  if (!runs.length) return <Empty what="No fee runs this period." />;
  return (
    <>
      <div className="grid tiles">
        <Tile label="Fee runs" value={runs.length} />
        <Tile label="Waiting for the RIA" value={runs.filter((r) => r.status.startsWith("Waiting")).length} sub="Nothing posts before approval" />
        <Tile label="Fees in these runs" value={usd(runs.reduce((n, r) => n + r.total_fees, 0))} sub="Advisory and custody" />
        <Tile label="Exceptions" value={runs.reduce((n, r) => n + r.exceptions, 0)} alert={runs.some((r) => r.exceptions)}
          sub="Holds, cash short, rejected lines, refunds" />
      </div>
      {runs.map((r) => {
        const rows = fees.filter((f) => f.run === r.run);
        return (
          <div className="panel" key={r.run}>
            <div className="planned-head" style={{ justifyContent: "space-between" }}>
              <h3 style={{ margin: 0 }}>{ops ? `${r.firm} · ` : ""}{r.kind}</h3>
              <Pill>{r.status}</Pill>
            </div>
            <p className="small muted">{r.run} · {r.method} · {day(r.period.split(" to ")[0])} to {day(r.period.split(" to ")[1])} · billed {day(r.bill_date)}</p>
            <p className="small">{r.schedule}</p>
            <dl className="details">
              <div><dt>Accounts</dt><dd>{r.accounts}</dd></div>
              <div><dt>Advisory fees</dt><dd>{usd(r.advisory_fees)}</dd></div>
              <div><dt>Custody fees</dt><dd>{usd(r.custody_fees)}</dd></div>
              <div><dt>Timeline</dt><dd>{r.timeline.map((t: string) => <div key={t} className="small">{t}</div>)}</dd></div>
            </dl>
            {r.note && <p className="small muted">{r.note}</p>}
            <details open={r.exceptions > 0 || r.status.startsWith("Waiting")}>
              <summary>Fees by account ({rows.length})</summary>
              <div className="scroll" style={{ marginTop: 10 }}><table>
                <thead><tr><th>Account</th><th style={{ textAlign: "right" }}>Billable value</th><th style={{ textAlign: "right" }}>Rate</th><th style={{ textAlign: "right" }}>Days</th>
                  <th style={{ textAlign: "right" }}>Advisory</th><th style={{ textAlign: "right" }}>Custody</th><th style={{ textAlign: "right" }}>Total</th><th>Status</th></tr></thead>
                <tbody>{rows.map((f) => (
                  <tr key={f.account}>
                    <td><span className="mono">{f.account}</span><div className="small">{f.client}</div></td>
                    <td className="num">{usd(f.billable_value)}{f.tier_basis_value > f.billable_value + 1 &&
                      <div className="small muted">Tier on household {usd(f.tier_basis_value)}</div>}</td>
                    <td className="num">{f.annual_rate ? pct(f.annual_rate) : "None"}</td>
                    <td className="num">{f.days_billed} of {f.period_days}</td>
                    <td className="num">{usd(f.advisory_fee)}</td><td className="num">{usd(f.custody_fee)}</td><td className="num">{usd(f.total_fee)}</td>
                    <td>{f.status === "Posted" || f.status === "Approved" || f.status === "Calculated" ? f.status : <Pill>{f.status}</Pill>}
                      {f.note && <div className="small muted">{f.note}</div>}</td>
                  </tr>))}</tbody>
              </table></div>
            </details>
          </div>);
      })}
    </>
  );
}

// ------------------------------------------------------------------ settlement and reconciliation
function Settlement({ d }: { d: Data }) {
  const s: Row = d.settlement;
  const breaks: Row[] = d.breaks;
  const off = s.checks.filter((c: Row) => c.result !== "Matches");
  return (
    <>
      <div className="grid tiles">
        <Tile label="Net settlement today" value={usd(s.net)} sub={s.net >= 0 ? "The bank receives" : "The bank pays"} />
        <Tile label="Client assets" value={usd(d.summary.assets)} sub={`${d.summary.accounts} accounts, ${usd(d.summary.client_cash)} cash`} />
        <Tile label="Open breaks" value={breaks.length} sub={breaks.length ? `Oldest ${breaks[0].age_days} business days` : "None"} alert={breaks.some((b) => b.age_days > 5)} />
        <Tile label="Ledger checks" value={`${s.checks.length - off.length} of ${s.checks.length}`} sub={off.length ? "One off: see the break it names" : "All match"} alert={off.length > 0} />
      </div>
      <div className="panel">
        <h3>Today's settlement</h3>
        <p className="small muted">Yesterday's trades settle today (T+1), netted by channel, with today's ACH and wires.</p>
        <div className="scroll"><table>
          <thead><tr><th>Channel</th><th>Direction</th><th style={{ textAlign: "right" }}>Net</th></tr></thead>
          <tbody>{s.flows.map((f: Row) => <tr key={f.channel}><td>{f.channel}</td><td>{f.direction}</td><td className="num">{usd(f.net, true)}</td></tr>)}
            <tr><td><strong>Net</strong></td><td>{s.net >= 0 ? "Receive" : "Pay"}</td><td className="num"><strong>{usd(s.net, true)}</strong></td></tr></tbody>
        </table></div>
        <p className="small" style={{ marginBottom: 0 }}>{s.funding}</p>
      </div>
      <div className="panel">
        <h3>Three layers: client accounts, RIA master accounts, general ledger</h3>
        <div className="scroll"><table>
          <thead><tr><th>RIA</th><th style={{ textAlign: "right" }}>Client accounts</th><th style={{ textAlign: "right" }}>Client cash</th><th style={{ textAlign: "right" }}>RIA master account</th><th style={{ textAlign: "right" }}>Difference</th></tr></thead>
          <tbody>{s.layers.map((l: Row) => <tr key={l.firm_crd}><td>{l.firm}</td><td className="num">{l.client_accounts}</td>
            <td className="num">{usd(l.client_cash)}</td><td className="num">{usd(l.ria_master_cash)}</td><td className="num">{usd(l.difference)}</td></tr>)}</tbody>
        </table></div>
        <div className="scroll" style={{ marginTop: 12 }}><table>
          <thead><tr><th>General ledger account</th><th>Side</th><th style={{ textAlign: "right" }}>Balance</th></tr></thead>
          <tbody>{s.gl.map((g: Row) => <tr key={g.account}><td><span className="mono">{g.account}</span> {g.name}</td><td>{g.side}</td><td className="num">{usd(g.balance)}</td></tr>)}</tbody>
        </table></div>
        <ul className="checks">{s.checks.map((c: Row) => (
          <li key={c.check}><Pill t={c.result === "Matches" ? "clear" : "block"}>{c.result}</Pill> <strong>{c.check}</strong>
            <div className="small muted">{c.detail}</div></li>))}</ul>
      </div>
      <div className="panel">
        <h3>Reconciliation breaks</h3>
        <p className="small muted">Books against DTC, the transfer agents, the sponsor, and the settlement bank, oldest first.</p>
        {breaks.length === 0 ? <Empty what="No open breaks." /> : <div className="scroll"><table>
          <thead><tr><th>Break</th><th>Item</th><th style={{ textAlign: "right" }}>Books</th><th style={{ textAlign: "right" }}>Street</th><th style={{ textAlign: "right" }}>Value</th><th>Age</th><th>Owner</th><th>Cause and next action</th></tr></thead>
          <tbody>{breaks.map((b) => (
            <tr key={b.id}>
              <td><span className="mono">{b.id}</span><div className="small muted">{b.kind}</div></td>
              <td className="wide-cell">{b.item}<div className="small muted">{b.location}</div></td>
              <td className="num">{b.kind === "Cash" ? usd(b.books) : qty(b.books)}</td>
              <td className="num">{b.kind === "Cash" ? usd(b.street) : qty(b.street)}</td>
              <td className="num">{usd(b.value)}</td>
              <td>{b.age_days} day{b.age_days === 1 ? "" : "s"}</td>
              <td>{b.owner}</td>
              <td className="wide-cell"><Pill>{b.status}</Pill><div className="small">{b.cause}</div>
                <div className="small"><strong>Next:</strong> {b.next_action}</div></td>
            </tr>))}</tbody>
        </table></div>}
      </div>
    </>
  );
}
