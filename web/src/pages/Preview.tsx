import { Fragment, useState, type ReactNode } from "react";
import { api } from "../api";
import { Loading, useLoad } from "../util";

/**
 * Read-only previews of the release 2 and 3 workbenches, from the synthetic book (app/routers/preview.py). Operations
 * sees its whole area; an RIA its own firm's accounts; a client their own account.
 */
export type PreviewKey = "trading" | "billing";

type Row = Record<string, any>;
type Data = Row & { area: PreviewKey; release: number; as_of: string; label: string; assumptions: string[] };

const usd = (n: number | null | undefined, signed = false) =>
  n === null || n === undefined ? "" : `${signed && n > 0 ? "+" : ""}${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const qty = (n: number | null | undefined) =>
  n === null || n === undefined ? "" : n.toLocaleString(undefined, { maximumFractionDigits: 3 });
const day = (iso: string | null | undefined) =>
  iso ? new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "";
const pct = (n: number) => `${(n * 100).toFixed(2)}%`;


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
      {d.area === "trading" && <Trading d={d} ops={ops} />}
      {d.area === "billing" && <Billing d={d} ops={ops} />}
      <details className="small preview-notes">
        <summary>What's synthetic here</summary>
        <ul>{d.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
      </details>
    </section>
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

