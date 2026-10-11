import { useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { Loading, useLoad } from "../util";

export default function Reviews() {
  const { user, refreshCounts } = useAuth();
  const list = useLoad(() => api<any[]>("/api/reviews"), []);
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [outcome, setOutcome] = useState<Record<number, { outcome: string; new_tier: string }>>({});
  const can = ["periodic_review", "ops_supervisor"].includes(user.role);
  const act = (p: Promise<any>, ok: string) => p.then(() => { setMsg({ ok }); list.reload(); refreshCounts(); }).catch((e) => setMsg({ error: e.message }));
  const rows = (list.data ?? []).slice().sort((a, b) => Number(b.overdue) - Number(a.overdue) || Number(b.due) - Number(a.due));

  return (
    <>
      <h1>Rolling reviews</h1>
      <p className="muted">Reviews come due by risk tier (demo policy: high risk every 6 months, others every 12). A missed refresh deadline places the no-new-activity code until the review completes.</p>
      {msg.ok && <p className="ok">{msg.ok}</p>}{msg.error && <p className="error">{msg.error}</p>}
      {can && <div className="actions"><button className="secondary" onClick={() => act(api("/api/reviews/enforce-deadlines", { body: {} }), "Deadlines checked")}>Check refresh deadlines</button></div>}
      {!list.data ? <Loading /> : (
        <div className="scroll" style={{ marginTop: 12 }}><table>
          <thead><tr><th>Subject</th><th>Risk</th><th>Next review</th><th>Status</th><th>Action</th></tr></thead>
          <tbody>{rows.map((r) => {
            const o = outcome[r.id] ?? { outcome: "no_change", new_tier: r.risk_tier };
            return (
              <tr key={r.id}>
                <td>{r.subject_name}<br /><span className="mono muted">{r.subject_id}</span></td>
                <td>{r.risk_tier}</td>
                <td>{r.next_review}{r.trigger !== "scheduled" ? <><br /><span className="small muted">early: {r.trigger.replace(/_/g, " ")}</span></> : null}</td>
                <td>{r.overdue ? <span className="pill t-escalate">Refresh overdue</span> : r.status === "restricted" ? <span className="pill t-block">Restricted</span>
                  : r.due ? <span className="pill t-refer">Due</span> : <span className="pill t-quiet">{r.status}</span>}
                  {r.response_due && r.status !== "complete" ? <><br /><span className="small muted">response due {r.response_due}</span></> : null}</td>
                <td style={{ minWidth: 240 }}>
                  {can && (r.status === "scheduled" || r.status === "complete") && r.due &&
                    <button className="small" onClick={() => act(api(`/api/reviews/${r.id}/request`, { body: {} }), "Refresh requested from the RIA")}>Request refresh</button>}
                  {user.role === "periodic_review" && (r.status === "requested" || r.status === "restricted") && (
                    <div className="row">
                      <select value={o.outcome} onChange={(e) => setOutcome({ ...outcome, [r.id]: { ...o, outcome: e.target.value } })}>
                        <option value="no_change">No change</option><option value="profile_changed">Profile changed</option>
                        <option value="risk_increased">Risk increased</option><option value="exit">Exit</option></select>
                      <select value={o.new_tier} onChange={(e) => setOutcome({ ...outcome, [r.id]: { ...o, new_tier: e.target.value } })}>
                        <option>LOW</option><option>MEDIUM</option><option>HIGH</option></select>
                      <button className="small" onClick={() => act(api(`/api/reviews/${r.id}/complete`, { body: o }), "Review complete")}>Complete</button>
                    </div>)}
                </td>
              </tr>
            );
          })}</tbody>
        </table></div>
      )}
    </>
  );
}
