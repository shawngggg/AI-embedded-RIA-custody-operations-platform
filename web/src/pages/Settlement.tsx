import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { day, Entries, qty, usd, type LedgerEntry } from "../r2";
import { Loading, useLoad } from "../util";

/** Release 2: the settlement team's daily cycle (map 12) and reconciliation (map 13), on the three-layer ledger. */

const VIEWER = "https://shawngggg.github.io/AI-embedded-RIA-custody-operations-platform/";

type Check = { check: string; ok: boolean; differences: any[] };
type Step = { step: string; outcome: string; detail: string[]; rule_ids: string[] };
type Receipt = { id: number; received_on: string; channel: string; amount: number; sender: string; memo: string;
  status: string; account_id: string | null; candidates: { account: string; name: string; score: number; why: string[] }[];
  decided_by: string | null; note: string | null; age_days: number; return_due: boolean };
type SettlementView = { as_of: string; can_act: boolean; checks: Check[];
  last_run: { business_date: string; run_at: string; run_by: string; steps: Step[]; net: number } | null;
  trial_balance: { account: string; name: string; type: string; cash: number; securities_value: number; positions: number }[];
  masters: { master: string; firm: string; accounts: number; cash: number; pending_cash: number; restricted_cash: number;
    securities_value: number }[];
  receipts: Receipt[]; today_flows: { channel: string; net: number; direction: string }[];
  in_transit: { case: number; type: string; account: string; expected: string | null; overdue: boolean }[];
  protection: Protection };
type Line = { line: string; amount: number; accounts?: number };
type Protection = {
  reserve: { as_of: string; credits: Line[]; debits: Line[]; total_credits: number; gross_debits: number; debit_reduction: number;
    total_debits: number; required: number; on_deposit: number; excess: number; deficit: number; deposit_due: string | null;
    empty_lines: string[]; frequency: string };
  possession: { securities: number; in_control: number;
    deficits: { security: string; name: string; customers: number; in_control: number; deficit: number }[] } };
type Nightly = { scheduled: boolean; can_run: boolean; runs: { at: string; on: string; by: string;
  settlement: { reserve_deficit: number; control_deficits: number }; recon: { breaks: number; certified: boolean };
  screening: { parties: number; alerts: number }; nigo: { reminded: number; withdrawn: number } }[] };
type Break = { id: number; location: string; asset: string; security: string; ticker: string | null; difference: number;
  value: number; pattern: string; pattern_label: string; note: string; account_id: string | null; case_id: number | null;
  opened_on: string; age_days: number; status: string; route: string | null; proposal: { text: string; evidence: string[] } | null;
  escalated: string | null; resolved_by: string | null; resolution: string | null };
type ReconView = { as_of: string; can_act: boolean; breaks: Break[];
  last_run: { business_date: string; run_by: string; positions: number; positions_matched: number; breaks: number;
    certifiable: boolean; certification: string; certified_by: string | null; certified_at: string | null;
    stale_in_transit: number[]; auto_fixed: number[] } | null;
  locations: { gl: string; location: string; positions: number; books_value: number; street_value: number; differences: number }[] };

const ROUTE: Record<string, string> = { auto_fix: "Fixed automatically", ai_suggest: "Drafted fix: a person approves",
  human_only: "Worked by hand" };

export default function Settlement() {
  const [tab, setTab] = useState<"cycle" | "protection" | "recon" | "ledger">("cycle");
  return (
    <>
      <h1>Settlement and reconciliation</h1>
      <p className="muted lead">The daily cycle settles the day's money at three layers (client accounts, RIA master accounts, and the
        general ledger) and checks they agree. Reconciliation then matches the books to DTC, the transfer agents, the sponsors, and the
        settlement bank. Both run on synthetic data, with simulators standing in for the bank and the street.</p>
      <div className="tabs" role="tablist">
        {([["cycle", "Cash settlement"], ["protection", "Customer protection"], ["recon", "Reconciliation"],
          ["ledger", "General ledger"]] as [typeof tab, string][])
          .map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}
      </div>
      {tab === "cycle" && <><EndOfDay /><Cycle /></>}
      {tab === "protection" && <CustomerProtection />}
      {tab === "recon" && <Recon />}
      {tab === "ledger" && <Ledger />}
      <p className="small muted" style={{ marginTop: 18 }}>The process: <a href={`${VIEWER}#cs`} target="_blank" rel="noreferrer">cash
        settlement</a> and <a href={`${VIEWER}#pr`} target="_blank" rel="noreferrer">position reconciliation</a> maps.</p>
    </>
  );
}

/** The nightly batch: the scheduler runs it; the operations supervisor can run it by hand. */
function EndOfDay() {
  const v = useLoad(() => api<Nightly>("/api/nightly"), []);
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [busy, setBusy] = useState(false);
  if (!v.data) return null;
  const last = v.data.runs[0];
  return (
    <div className="panel">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ margin: 0 }}>End of day</h3>
        {v.data.can_run && <button className="secondary" disabled={busy} onClick={() => {
          setBusy(true);
          api("/api/nightly/run", { body: {} }).then(() => { setMsg({ ok: "End of day run: cycle, reconciliation, screening, NIGO aging." }); v.reload(); })
            .catch((e: Error) => setMsg({ error: e.message })).finally(() => setBusy(false));
        }}>Run end of day now</button>}
      </div>
      <p className="small muted">Every night: the settlement cycle (with the Rule 15c3-3 checks), reconciliation, ongoing sanctions screening,
        and NIGO aging. {v.data.scheduled ? "Cloud Scheduler runs it." : "No scheduler is connected yet, so it runs by hand."}</p>
      {msg.ok && <p className="ok">{msg.ok}</p>}{msg.error && <p className="error">{msg.error}</p>}
      {last ? <p className="small">Last run {day(last.on)} by {last.by}: reserve {last.settlement.reserve_deficit ? `short ${usd(last.settlement.reserve_deficit)}` : "covered"},
        {" "}{last.settlement.control_deficits} possession-or-control deficit{last.settlement.control_deficits === 1 ? "" : "s"},
        {" "}{last.recon.breaks} open break{last.recon.breaks === 1 ? "" : "s"}{last.recon.certified ? " (certified)" : ""},
        {" "}{last.screening.alerts} screening alert{last.screening.alerts === 1 ? "" : "s"} from {last.screening.parties} parties,
        {" "}{last.nigo.reminded} reminder{last.nigo.reminded === 1 ? "" : "s"} and {last.nigo.withdrawn} withdrawn.</p>
        : <p className="small muted">No end-of-day run yet.</p>}
    </div>
  );
}

/** Rule 15c3-3: the reserve formula and the possession-or-control check, from the books. */
function CustomerProtection() {
  const v = useLoad(() => api<SettlementView>("/api/settlement"), []);
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [dep, setDep] = useState({ amount: "", note: "" });
  if (!v.data) return v.error ? <p className="error">{v.error}</p> : <Loading />;
  const { reserve: r, possession: p } = v.data.protection;
  const deposit = () => api<SettlementView>("/api/settlement/reserve/deposit", { body: { amount: Number(dep.amount), note: dep.note } })
    .then((x) => { v.setData(x); setMsg({ ok: `Deposited ${usd(Number(dep.amount))} to the reserve account.` }); setDep({ amount: "", note: "" }); })
    .catch((e: Error) => setMsg({ error: e.message }));
  return (
    <>
      <p className="muted">The reserve formula nets what the custodian owes customers in cash against what they owe it; the difference
        sits in the special reserve bank account for the exclusive benefit of customers. {r.frequency}. A deficit is deposited no later
        than one hour after banks open on the second business day (CP-001).</p>
      {msg.ok && <p className="ok">{msg.ok}</p>}{msg.error && <p className="error">{msg.error}</p>}
      <div className="grid tiles" style={{ marginBottom: 12 }}>
        <div className="tile"><div className="label">Required reserve</div><div className="value">{usd(r.required)}</div><div className="sub">As of {day(r.as_of)}</div></div>
        <div className="tile"><div className="label">On deposit</div><div className="value">{usd(r.on_deposit)}</div><div className="sub">GL 1030</div></div>
        <div className="tile"><div className="label">{r.deficit ? "Deficit" : "Excess"}</div><div className="value">{usd(r.deficit || r.excess)}</div>
          <div className={`sub ${r.deficit ? "alert" : ""}`}>{r.deficit ? `Deposit by ${r.deposit_due}` : "Covered"}</div></div>
      </div>
      <div className="panel">
        <h3>The computation</h3>
        <div className="scroll"><table>
          <thead><tr><th>Line</th><th className="num">Amount</th></tr></thead>
          <tbody>
            {r.credits.map((c) => <tr key={c.line}><td>Credit: {c.line}{c.accounts !== undefined ? ` (${c.accounts} accounts)` : ""}</td><td className="num">{usd(c.amount)}</td></tr>)}
            <tr><td><strong>Total credits</strong></td><td className="num"><strong>{usd(r.total_credits)}</strong></td></tr>
            {r.debits.map((d) => <tr key={d.line}><td>Debit: {d.line}{d.accounts !== undefined ? ` (${d.accounts} accounts)` : ""}</td><td className="num">{usd(d.amount)}</td></tr>)}
            <tr><td>Less the 2% reduction of debits</td><td className="num">{usd(-r.debit_reduction)}</td></tr>
            <tr><td><strong>Total debits</strong></td><td className="num"><strong>{usd(r.total_debits)}</strong></td></tr>
            <tr><td><strong>Required: credits less debits</strong></td><td className="num"><strong>{usd(r.required)}</strong></td></tr>
          </tbody>
        </table></div>
        <p className="small muted">Empty on this book: {r.empty_lines.join("; ")}.</p>
        {v.data.can_act && (
          <div className="row" style={{ alignItems: "end" }}>
            <div className="field" style={{ flex: "0 1 180px" }}><label htmlFor="rd">Deposit (USD)</label>
              <input id="rd" type="number" min={0} step={1000} value={dep.amount} onChange={(e) => setDep({ ...dep, amount: e.target.value })} /></div>
            <div className="field"><label htmlFor="rn">Why</label>
              <input id="rn" type="text" placeholder="Covers the deficit in today's computation" value={dep.note} onChange={(e) => setDep({ ...dep, note: e.target.value })} /></div>
            <button disabled={!Number(dep.amount) || dep.note.trim().length < 10} onClick={deposit}>Deposit to the reserve account</button>
          </div>)}
      </div>
      <div className="panel">
        <h3>Possession or control</h3>
        <p className="small muted">The stock record for customers' fully paid securities at DTC, the fund transfer agents, and the sponsors,
          against what the street confirms there (CP-002). {p.in_control} of {p.securities} securities are fully in control.</p>
        {p.deficits.length === 0 ? <div className="empty">No deficits.</div> : <div className="scroll"><table>
          <thead><tr><th>Security</th><th className="num">Books</th><th className="num">Street confirms</th><th className="num">Deficit</th></tr></thead>
          <tbody>{p.deficits.map((d) => <tr key={d.security}><td>{d.name}<div className="small muted mono">{d.security}</div></td>
            <td className="num">{qty(d.customers)}</td><td className="num">{qty(d.in_control)}</td><td className="num error">{qty(d.deficit)}</td></tr>)}</tbody>
        </table></div>}
        <p className="small muted">A deficit is a financial risk event for the settlement area; reconciliation traces it (see Reconciliation).</p>
      </div>
    </>
  );
}

function Checks({ checks }: { checks: Check[] }) {
  return <ul className="checks r2-checks">{checks.map((c) => (
    <li key={c.check}><span className={`pill t-${c.ok ? "clear" : "block"}`}>{c.ok ? "Balanced" : "Out of balance"}</span> {c.check}
      {!c.ok && <div className="small error">{JSON.stringify(c.differences.slice(0, 3))}</div>}</li>))}</ul>;
}

function Cycle() {
  const v = useLoad(() => api<SettlementView>("/api/settlement"), []);
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [apply, setApply] = useState<Record<number, string>>({});
  const [note, setNote] = useState<Record<number, string>>({});
  const [sim, setSim] = useState({ channel: "Fedwire", amount: "", sender: "", memo: "" });
  if (!v.data) return v.error ? <p className="error">{v.error}</p> : <Loading />;
  const d = v.data;
  const act = (path: string, body: unknown, ok: string) => api<SettlementView>(path, { body })
    .then((r) => { v.setData(r); setMsg({ ok }); }).catch((e) => setMsg({ error: e.message }));
  const unapplied = d.receipts.filter((r) => r.status === "unapplied");
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <p className="muted" style={{ margin: 0 }}>Business day {day(d.as_of)}.
          {d.last_run ? ` Last cycle: ${day(d.last_run.business_date)}, run by ${d.last_run.run_by}.` : " No cycle run yet."}</p>
        {d.can_act && <button onClick={() => act("/api/settlement/run", {}, "Cycle run.")}>Run today's cycle</button>}
      </div>
      {msg.ok && <p className="ok">{msg.ok}</p>}{msg.error && <p className="error">{msg.error}</p>}
      <div className="panel"><h3>The three layers</h3><Checks checks={d.checks} /></div>
      {d.last_run && <div className="panel">
        <h3>The last cycle, step by step</h3>
        <ol className="steps">{d.last_run.steps.map((s, i) => (
          <li key={i} style={{ ["--lane" as any]: "var(--lane-ledger)" }}><span className="stepname">{s.step}</span>
            <span className="outcome">{s.outcome}</span>
            {s.detail.length > 0 && <ul>{s.detail.slice(0, 8).map((x, j) => <li key={j}>{x}</li>)}
              {s.detail.length > 8 && <li>and {s.detail.length - 8} more</li>}</ul>}
            {s.rule_ids.length > 0 && <span className="mono muted">{s.rule_ids.join(" · ")}</span>}</li>))}</ol>
      </div>}
      <div className="panel">
        <h3>Today's net funding at the settlement bank</h3>
        {d.today_flows.length === 0 ? <div className="empty">No cash moved through the bank today yet.</div> : <div className="scroll"><table>
          <thead><tr><th>Channel</th><th>Direction</th><th className="num">Net</th></tr></thead>
          <tbody>{d.today_flows.map((f) => <tr key={f.channel}><td>{f.channel}</td><td>{f.direction}</td><td className="num">{usd(f.net, true)}</td></tr>)}</tbody>
        </table></div>}
      </div>
      <div className="panel">
        <h3>Unapplied cash in suspense ({unapplied.length})</h3>
        <p className="small muted">Money that arrived with no request behind it waits in suspense (GL 2190) until it's matched to an
          account. A match at 0.90 or above applies itself; anything else waits for you, and unmatched money goes back after 10
          business days (STL-001, STL-002).</p>
        {unapplied.length === 0 ? <div className="empty">Nothing in suspense.</div> : <div className="scroll"><table>
          <thead><tr><th>Received</th><th>From</th><th className="num">Amount</th><th>Memo</th><th>Best match</th>{d.can_act && <th />}</tr></thead>
          <tbody>{unapplied.map((r) => (
            <tr key={r.id}>
              <td>{day(r.received_on)}<div className="small muted">{r.channel}, {r.age_days} business days{r.return_due ? ": due back" : ""}</div></td>
              <td>{r.sender}</td><td className="num">{usd(r.amount)}</td><td className="small">{r.memo}</td>
              <td className="small">{r.candidates[0] ? <><span className="mono">{r.candidates[0].account}</span> {r.candidates[0].name}
                <div className="muted">{r.candidates[0].score.toFixed(2)}: {r.candidates[0].why.join("; ")}</div></> : <span className="muted">No account matches</span>}</td>
              {d.can_act && <td className="r2-receipt">
                <input placeholder="Account" value={apply[r.id] ?? r.candidates[0]?.account ?? ""} onChange={(e) => setApply({ ...apply, [r.id]: e.target.value })} />
                <input placeholder="Note (why it's this account, or why it goes back)" value={note[r.id] ?? ""} onChange={(e) => setNote({ ...note, [r.id]: e.target.value })} />
                <div className="actions" style={{ marginTop: 4 }}>
                  <button className="small" onClick={() => act(`/api/settlement/receipts/${r.id}/apply`, { account_id: apply[r.id] ?? r.candidates[0]?.account, note: note[r.id] ?? "" }, `Applied to ${apply[r.id] ?? r.candidates[0]?.account}.`)}>Apply</button>
                  <button className="small secondary" onClick={() => act(`/api/settlement/receipts/${r.id}/return`, { note: note[r.id] ?? "" }, "Returned to the sender.")}>Return</button>
                </div></td>}
            </tr>))}</tbody>
        </table></div>}
        {d.can_act && <details style={{ marginTop: 10 }}><summary className="small">Bank simulator: money arrives (demo)</summary>
          <div className="row" style={{ gap: 6, flexWrap: "wrap", marginTop: 8 }}>
            <select value={sim.channel} onChange={(e) => setSim({ ...sim, channel: e.target.value })}><option>Fedwire</option><option>ACH</option></select>
            <input placeholder="Amount" inputMode="decimal" value={sim.amount} onChange={(e) => setSim({ ...sim, amount: e.target.value })} style={{ maxWidth: 120 }} />
            <input placeholder="Sender" value={sim.sender} onChange={(e) => setSim({ ...sim, sender: e.target.value })} style={{ maxWidth: 200 }} />
            <input placeholder="Memo, e.g. FBO ACC-3011" value={sim.memo} onChange={(e) => setSim({ ...sim, memo: e.target.value })} style={{ maxWidth: 240 }} />
            <button className="small" onClick={() => act("/api/settlement/receipts", { ...sim, amount: Number(sim.amount) }, "Money arrived in suspense. Run the cycle to match it.")}>Receive</button>
          </div></details>}
      </div>
      <div className="panel">
        <h3>RIA master accounts</h3>
        <div className="scroll"><table>
          <thead><tr><th>Master</th><th className="num">Accounts</th><th className="num">Cash settled</th><th className="num">Pending</th><th className="num">Restricted</th><th className="num">Securities</th></tr></thead>
          <tbody>{d.masters.map((m) => (
            <tr key={m.master}><td>{m.firm}<div className="small mono muted">{m.master}</div></td><td className="num">{m.accounts}</td>
              <td className="num">{usd(m.cash)}</td><td className="num">{usd(m.pending_cash)}</td><td className="num">{usd(m.restricted_cash)}</td>
              <td className="num">{usd(m.securities_value)}</td></tr>))}</tbody>
        </table></div>
      </div>
    </>
  );
}

function Recon() {
  const v = useLoad(() => api<ReconView>("/api/recon"), []);
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [text, setText] = useState<Record<number, string>>({});
  const [acct, setAcct] = useState<Record<number, string>>({});
  if (!v.data) return v.error ? <p className="error">{v.error}</p> : <Loading />;
  const d = v.data;
  const act = (path: string, body: unknown, ok: string) => api<ReconView>(path, { body })
    .then((r) => { v.setData(r); setMsg({ ok }); }).catch((e) => setMsg({ error: e.message }));
  const open = d.breaks.filter((b) => b.status !== "resolved");
  const done = d.breaks.filter((b) => b.status === "resolved");
  const r = d.last_run;
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <p className="muted" style={{ margin: 0 }}>{r ? `Last run ${day(r.business_date)} by ${r.run_by}: ${r.positions_matched} of ${r.positions} positions match.` : "Not run yet."}</p>
        {d.can_act && <button onClick={() => act("/api/recon/run", {}, "Reconciliation run.")}>Run reconciliation</button>}
      </div>
      {msg.ok && <p className="ok">{msg.ok}</p>}{msg.error && <p className="error">{msg.error}</p>}
      {r && <div className={`panel ${r.certifiable ? "" : "attention"}`}>
        <h3>{r.certifiable ? "Books certified" : "Not certified yet"}</h3>
        <p style={{ margin: 0 }}>{r.certifiable ? `Certified by ${r.certified_by}. Billing can bill this period (REC-004).`
          : `${r.certification}. The books are certified only when nothing is open but expected in-transit items (REC-004).`}</p>
      </div>}
      <div className="panel">
        <h3>Open breaks ({open.length})</h3>
        {open.length === 0 ? <div className="empty">No open breaks.</div> : open.map((b) => (
          <div key={b.id} className="r2-break">
            <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 6 }}>
              <strong>{b.location}: {b.ticker ?? (b.asset === "USD" ? "Cash" : b.asset)} {b.asset === "USD" ? usd(b.difference, true) : `${b.difference > 0 ? "+" : ""}${qty(b.difference)}`}</strong>
              <span>{b.escalated && <span className="pill t-block">Escalated: {b.escalated}</span>} <span className="pill t-quiet">{ROUTE[b.route ?? ""] ?? b.route}</span></span>
            </div>
            <p className="small" style={{ margin: "4px 0" }}>{b.pattern_label}. {b.note}. {usd(b.value)}, open {b.age_days} business days.
              {b.case_id && <> Case <Link to={`/cases/${b.case_id}`}>#{b.case_id}</Link>.</>}</p>
            {b.proposal && <p className="small" style={{ margin: "4px 0" }}><strong>Drafted fix:</strong> {b.proposal.text}</p>}
            {d.can_act && <div className="r2-receipt">
              <textarea placeholder={b.route === "human_only" ? "What you found, and how it's resolved" : "Why you approve the drafted fix"}
                value={text[b.id] ?? ""} onChange={(e) => setText({ ...text, [b.id]: e.target.value })} />
              {(b.pattern === "unexplained" || b.pattern === "late_delivery") && <input placeholder="Book it to an account (optional for unexplained)"
                value={acct[b.id] ?? b.account_id ?? ""} onChange={(e) => setAcct({ ...acct, [b.id]: e.target.value })} />}
              <div className="actions" style={{ marginTop: 4 }}>
                <button className="small" onClick={() => act(`/api/recon/breaks/${b.id}/resolve`,
                  { resolution: text[b.id] ?? "", account_id: (acct[b.id] ?? (b.pattern === "late_delivery" ? b.account_id : "")) || null },
                  "Break resolved.")}>{b.proposal ? "Approve and post the fix" : "Resolve"}</button>
              </div></div>}
          </div>))}
      </div>
      <div className="panel">
        <h3>Books against the street, by location</h3>
        <div className="scroll"><table>
          <thead><tr><th>Location</th><th className="num">Positions</th><th className="num">Books</th><th className="num">Street</th><th className="num">Differences</th></tr></thead>
          <tbody>{d.locations.map((l) => (
            <tr key={l.gl}><td>{l.location}<div className="small mono muted">GL {l.gl}</div></td><td className="num">{l.positions}</td>
              <td className="num">{usd(l.books_value)}</td><td className="num">{usd(l.street_value)}</td>
              <td className="num">{l.differences ? <span className="pill t-refer">{l.differences}</span> : 0}</td></tr>))}</tbody>
        </table></div>
      </div>
      {done.length > 0 && <div className="panel">
        <h3>Resolved</h3>
        <div className="scroll"><table>
          <thead><tr><th>Break</th><th>Resolution</th><th>By</th></tr></thead>
          <tbody>{done.map((b) => <tr key={b.id}><td>{b.location}: {b.ticker ?? "Cash"} {b.asset === "USD" ? usd(b.difference, true) : qty(b.difference)}
            <div className="small muted">{b.pattern_label}</div></td><td className="small">{b.resolution}</td><td className="small">{b.resolved_by}</td></tr>)}</tbody>
        </table></div>
      </div>}
    </>
  );
}

function Ledger() {
  const v = useLoad(() => api<SettlementView>("/api/settlement"), []);
  const entries = useLoad(() => api<LedgerEntry[]>("/api/ledger/entries?limit=60"), []);
  if (!v.data) return v.error ? <p className="error">{v.error}</p> : <Loading />;
  return (
    <>
      <div className="panel"><h3>Trial balance</h3>
        <p className="small muted">Assets carry a debit balance and liabilities a credit balance (negative here). Securities are shown at
          market value; the ledger itself holds them as quantities.</p>
        <div className="scroll"><table>
          <thead><tr><th>GL account</th><th>Type</th><th className="num">Cash</th><th className="num">Securities</th></tr></thead>
          <tbody>{v.data.trial_balance.map((t) => (
            <tr key={t.account}><td><span className="mono">{t.account}</span> {t.name}</td><td>{t.type}</td>
              <td className="num">{usd(t.cash)}</td><td className="num">{t.positions ? usd(t.securities_value) : ""}</td></tr>))}</tbody>
        </table></div>
      </div>
      <h3>Latest entries</h3>
      {entries.data ? <Entries entries={entries.data} compact /> : <Loading />}
      <p className="small muted">Every entry was validated before it was written: debits equal credits per asset, each client account rolls up
        to its RIA master, and the masters roll up to the control accounts (2100 for cash, 2500 for securities).</p>
    </>
  );
}
