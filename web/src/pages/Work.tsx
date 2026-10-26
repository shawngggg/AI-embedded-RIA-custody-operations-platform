import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type CaseSummary, type Decision } from "../api";
import { useAuth } from "../auth";
import { Loading, slaText, Status, useLoad, when } from "../util";

type WorkData = {
  greeting_name: string; team_label: string | null;
  queues: { key: string; label: string; count: number; breached: number }[];
  needs_you: CaseSummary[]; needs_you_total: number; breached: number; assigned_to_me?: number;
  to_approve: Decision[]; my_proposals: Decision[];
  risk_events_open: number; risk_events_mine: number; restricted_targets: number;
  messages_unread?: number; reviews_due?: number; reviews_overdue?: number; grants_waiting?: number;
};

// Workbenches for teams without a queue of their own: release 2's settlement page, and the planned release 3 pages
const WORKBENCH: [string, string, string, number | null][] = [
  ["settlement", "/ops/settlement", "Settlement and reconciliation", null],
  ["planned_trading", "/ops/trading", "Trade operations", 3], ["planned_billing", "/ops/billing", "Billing operations", 3]];

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

/** Everyone's landing page in the workbench: what needs this person now. */
export default function Work() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const w = useLoad(() => api<WorkData>("/api/work"), []);
  const can = user.capabilities ?? {};
  if (!w.data) return w.error ? <p className="error">{w.error}</p> : <Loading />;
  const d = w.data;
  const today = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
  return (
    <>
      <h1>{greeting()}, {d.greeting_name}</h1>
      <p className="muted">{user.role_label}{d.team_label ? ` · ${d.team_label} team` : ""} · {today}</p>

      <div className="grid tiles work-tiles">
        {can.queues && <Link className="tile" to="/queues">
          <div className="label">Needs you</div><div className="value">{d.needs_you_total}</div>
          <div className={`sub ${d.breached ? "alert" : ""}`}>{d.assigned_to_me ? `${d.assigned_to_me} assigned to you · ` : ""}{d.breached ? `${d.breached} past SLA` : "Nothing past SLA"}</div>
        </Link>}
        {can.approvals && <Link className="tile" to="/approvals">
          <div className="label">Waiting for your approval</div><div className="value">{d.to_approve.length}</div>
          <div className="sub">Maker-checker decisions</div></Link>}
        {d.my_proposals.length > 0 && <div className="tile">
          <div className="label">Your proposals</div><div className="value">{d.my_proposals.length}</div>
          <div className="sub">Waiting for a checker</div></div>}
        {user.role === "periodic_review" && <Link className="tile" to="/reviews">
          <div className="label">Reviews due</div><div className="value">{d.reviews_due ?? 0}</div>
          <div className={`sub ${d.reviews_overdue ? "alert" : ""}`}>{d.reviews_overdue ? `${d.reviews_overdue} overdue` : "None overdue"}</div></Link>}
        {can.messages && d.messages_unread !== undefined && <Link className="tile" to="/messages">
          <div className="label">Unread messages</div><div className="value">{d.messages_unread}</div>
          <div className="sub">Team channels</div></Link>}
        <Link className="tile" to="/risk-events">
          <div className="label">Open risk events</div><div className="value">{d.risk_events_open}</div>
          <div className="sub">{d.risk_events_open ? "In your area" : "None in your area"}</div></Link>
        {can.restricted_accounts && <Link className="tile" to="/restricted">
          <div className="label">Restricted accounts</div><div className="value">{d.restricted_targets}</div>
          <div className="sub">Accounts and firms with an active code</div></Link>}
        {!can.queues && WORKBENCH.filter(([cap]) => can[cap]).map(([cap, to, label, release]) => (
          <Link key={cap} className="tile" to={to}>
            <div className="label">Your team's workbench</div><div className="value small-value">{label}</div>
            <div className="sub">{release ? `Planned for release ${release}: a preview and the plan` : "The daily cycle and the breaks"}</div></Link>))}
        {user.role === "platform_admin" && <Link className="tile" to="/admin">
          <div className="label">Grants to approve</div><div className="value">{d.grants_waiting ?? 0}</div>
          <div className="sub">Requested by another administrator</div></Link>}
      </div>

      {can.ai_review && <AiQa />}

      {d.queues.length > 0 && (
        <>
          <h2>Needs you</h2>
          <p className="muted small">From the queues you work: {d.queues.map((q) => `${q.label} (${q.count})`).join(", ")}. Yours first, then
            the unassigned, oldest SLA first; a case a colleague claimed stays with them.</p>
          {d.needs_you.length ? (
            <div className="scroll"><table>
              <thead><tr><th>Case</th><th>Queue</th><th>Status</th><th>Owner</th><th>Waiting since</th><th>SLA</th></tr></thead>
              <tbody>{d.needs_you.map((c) => (
                <tr key={c.id} className="link" onClick={() => navigate(`/cases/${c.id}`)}>
                  <td><Link to={`/cases/${c.id}`} onClick={(e) => e.stopPropagation()}>#{c.id} {c.title}</Link></td>
                  <td className="small">{c.queue_label}</td><td><Status status={c.status} /></td>
                  <td className="small">{c.assigned_to ? (c.assigned_to.id === user.id ? "You" : c.assigned_to.name) : <span className="muted">Unassigned</span>}</td>
                  <td className="small">{when(c.queue_entered_at)}</td>
                  <td className={c.sla.breached ? "error" : ""}>{slaText(c.sla.hours_left)}</td>
                </tr>))}</tbody>
            </table></div>
          ) : <div className="empty">Your queues are clear.</div>}
          {d.needs_you_total > d.needs_you.length && <p className="small"><Link to="/queues">All {d.needs_you_total} in your queues</Link></p>}
        </>
      )}

      {can.approvals && d.to_approve.length > 0 && (
        <>
          <h2>Waiting for your approval</h2>
          <div className="scroll"><table>
            <thead><tr><th>Decision</th><th>Case</th><th>Proposed by</th><th></th></tr></thead>
            <tbody>{d.to_approve.map((x) => (
              <tr key={x.id}><td>{x.label}: <strong>{x.value_label}</strong><div className="muted small">{x.reference}</div></td>
                <td><Link to={`/cases/${x.case_id}`}>#{x.case_id} {x.case_title ?? ""}</Link></td>
                <td className="small">{x.proposed_by}, {when(x.proposed_at)}</td>
                <td><Link to="/approvals">Review</Link></td></tr>))}</tbody>
          </table></div>
        </>
      )}

      {d.my_proposals.length > 0 && (
        <>
          <h2>Your proposals waiting for a checker</h2>
          <ul className="plain">{d.my_proposals.map((x) => (
            <li key={x.id}>{x.label}: {x.value_label} on <Link to={`/cases/${x.case_id}`}>#{x.case_id}</Link> <span className="muted small">{when(x.proposed_at)}</span></li>))}</ul>
        </>
      )}
    </>
  );
}


type QaItem = { id: number; case_id: number; case: string; label: string; suggested: string | null; quote: string;
  reviewed_by: string; outcome: string; final: string | null; reason: string | null };

/** The AI review QA sample: values a colleague reviewed, for a second person to check against the package. */
function AiQa() {
  const q = useLoad(() => api<{ items: QaItem[]; every: number }>("/api/ai-qa"), []);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  if (!q.data || q.data.items.length === 0) return null;
  const decide = (i: QaItem, verdict: string) => api<{ items: QaItem[]; every: number }>(`/api/ai-qa/${i.id}`, { body: { verdict, note: notes[i.id] ?? "" } })
    .then((r) => { q.setData(r); setMsg({ ok: verdict === "agree" ? "Agreed." : "Disagreement logged as a risk event for your team." }); })
    .catch((e: Error) => setMsg({ error: e.message }));
  return (
    <div className="panel">
      <h2 style={{ marginTop: 0 }}>Second review: AI-read values ({q.data.items.length})</h2>
      <p className="small muted">1 in {q.data.every} values a colleague reviewed comes to a second person. Open the case's package and check
        the first review: agree, or say what the package shows.</p>
      {msg.ok && <p className="ok">{msg.ok}</p>}{msg.error && <p className="error">{msg.error}</p>}
      {q.data.items.map((i) => (
        <div key={i.id} className="evidence" style={{ marginBottom: 8 }}>
          <div className="small"><Link to={`/cases/${i.case_id}`}>{i.case}</Link> · {i.label}: the AI read <strong>{i.suggested ?? "nothing"}</strong>;
            {" "}{i.reviewed_by} {i.outcome} it{i.final ? ` as ${i.final}` : ""}{i.reason ? ` (${i.reason})` : ""}.</div>
          {i.quote && <div className="small muted">Package: “{i.quote}”</div>}
          <div className="row" style={{ alignItems: "end", marginTop: 6 }}>
            <div className="field"><label htmlFor={`qa${i.id}`}>If you disagree, what the package shows</label>
              <input id={`qa${i.id}`} type="text" value={notes[i.id] ?? ""} onChange={(e) => setNotes({ ...notes, [i.id]: e.target.value })} /></div>
            <button className="small" onClick={() => decide(i, "agree")}>Agree</button>
            <button className="small secondary" onClick={() => decide(i, "disagree")}>Disagree</button>
          </div>
        </div>))}
    </div>
  );
}
