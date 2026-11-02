import { useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { Loading, useLoad } from "../util";

type Draft = { outcome: string; new_tier: string; note: string; basis: string; skip_reason: string };

/** Rolling review. A completion leaves its basis on file (a note, and the refresh or a reason to skip it); a medium-
 *  or high-risk one waits for AML compliance to approve it (walkthrough note 17). A missed refresh deadline is
 *  restricted by the nightly batch (note 16); the button runs that check now. */
export default function Reviews() {
  const { user, refreshCounts } = useAuth();
  const list = useLoad(() => api<any[]>("/api/reviews"), []);
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [drafts, setDrafts] = useState<Record<number, Draft>>({});
  const [checks, setChecks] = useState<Record<number, string>>({});
  const can = ["periodic_review", "ops_supervisor"].includes(user.role);
  const checker = ["aml_compliance", "bsa_officer"].includes(user.role);
  const act = (p: Promise<any>, ok: string) => p.then(() => { setMsg({ ok }); list.reload(); refreshCounts(); }).catch((e) => setMsg({ error: e.message }));
  const rows = (list.data ?? []).slice().sort((a, b) => Number(b.status === "awaiting_approval") - Number(a.status === "awaiting_approval")
    || Number(b.overdue) - Number(a.overdue) || Number(b.due) - Number(a.due));

  return (
    <>
      <h1>Rolling reviews</h1>
      <p className="muted">Reviews come due by risk tier (demo policy: high risk every 6 months, others every 12). A missed refresh deadline
        places the no-new-activity code until the review completes; the nightly batch checks the deadlines every evening. A completion says
        what was checked, and a medium- or high-risk review needs AML compliance's approval.</p>
      {msg.ok && <p className="ok">{msg.ok}</p>}{msg.error && <p className="error">{msg.error}</p>}
      {can && <div className="actions"><button className="secondary" onClick={() => act(api("/api/reviews/enforce-deadlines", { body: {} }), "Deadlines checked")}>Check refresh deadlines now</button></div>}
      {!list.data ? <Loading /> : (
        <div className="scroll" style={{ marginTop: 12 }}><table>
          <thead><tr><th>Subject</th><th>Risk</th><th>Next review</th><th>Status</th><th>Action</th></tr></thead>
          <tbody>{rows.map((r) => {
            const o = drafts[r.id] ?? { outcome: "no_change", new_tier: r.risk_tier, note: "",
              basis: r.status === "scheduled" ? "skipped" : "refresh_received", skip_reason: "" };
            const set = (k: keyof Draft, v: string) => setDrafts({ ...drafts, [r.id]: { ...o, [k]: v } });
            const checked = r.risk_tier !== "LOW" || o.new_tier !== "LOW";
            const p = r.proposal;
            return (
              <tr key={r.id}>
                <td>{r.subject_name}<br /><span className="mono muted">{r.subject_id}</span></td>
                <td>{r.risk_tier}</td>
                <td>{r.next_review}{r.trigger !== "scheduled" ? <><br /><span className="small muted">early: {r.trigger.replace(/_/g, " ")}</span></> : null}</td>
                <td>{r.status === "awaiting_approval" ? <span className="pill t-refer">With AML compliance</span>
                  : r.overdue ? <span className="pill t-escalate">Refresh overdue</span> : r.status === "restricted" ? <span className="pill t-block">Restricted</span>
                  : r.due ? <span className="pill t-refer">Due</span> : <span className="pill t-quiet">{r.status}</span>}
                  {r.response_due && !["complete", "awaiting_approval"].includes(r.status) ? <><br /><span className="small muted">response due {r.response_due}</span></> : null}</td>
                <td style={{ minWidth: 300 }}>
                  {can && (r.status === "scheduled" || r.status === "complete") && r.due &&
                    <button className="small" onClick={() => act(api(`/api/reviews/${r.id}/request`, { body: {} }), "Refresh requested from the RIA")}>Request refresh</button>}
                  {p && <div className="small">
                    <strong>{p.by}</strong> proposes {p.outcome.replace(/_/g, " ")}, {p.new_tier}: {p.note}
                    {p.basis === "skipped" ? <div className="muted">Refresh skipped: {p.skip_reason}</div> : <div className="muted">The RIA's refresh came back.</div>}
                    {checker && <div className="row" style={{ marginTop: 6, alignItems: "end" }}>
                      <div className="field" style={{ flex: 1 }}><label htmlFor={`chk${r.id}`}>What you checked</label>
                        <input id={`chk${r.id}`} type="text" value={checks[r.id] ?? ""} onChange={(e) => setChecks({ ...checks, [r.id]: e.target.value })} /></div>
                      <button className="small" onClick={() => act(api(`/api/reviews/${r.id}/approve`, { body: { approve: true, note: checks[r.id] ?? "" } }), "Approved: review complete")}>Approve</button>
                      <button className="small secondary" onClick={() => act(api(`/api/reviews/${r.id}/approve`, { body: { approve: false, note: checks[r.id] ?? "" } }), "Sent back to periodic review")}>Send back</button>
                    </div>}
                  </div>}
                  {user.role === "periodic_review" && ["scheduled", "requested", "restricted"].includes(r.status) && (r.due || r.status !== "scheduled") && (
                    <div>
                      <div className="row">
                        <select aria-label="Outcome" value={o.outcome} onChange={(e) => set("outcome", e.target.value)}>
                          <option value="no_change">No change</option><option value="profile_changed">Profile changed</option>
                          <option value="risk_increased">Risk increased</option><option value="exit">Exit</option></select>
                        <select aria-label="New risk tier" value={o.new_tier} onChange={(e) => set("new_tier", e.target.value)}>
                          <option>LOW</option><option>MEDIUM</option><option>HIGH</option></select>
                        <select aria-label="Refresh" value={o.basis} onChange={(e) => set("basis", e.target.value)}>
                          {r.status !== "scheduled" && <option value="refresh_received">The refresh came back</option>}
                          <option value="skipped">Skip the refresh (say why)</option></select>
                      </div>
                      <textarea aria-label="What you checked and found" placeholder="What you checked and what you found" rows={2}
                        value={o.note} onChange={(e) => set("note", e.target.value)} style={{ width: "100%", marginTop: 4 }} />
                      {o.basis === "skipped" && <input type="text" aria-label="Why the refresh isn't needed" placeholder="Why the refresh isn't needed this time"
                        value={o.skip_reason} onChange={(e) => set("skip_reason", e.target.value)} style={{ width: "100%", marginTop: 4 }} />}
                      <button className="small" style={{ marginTop: 4 }} onClick={() => act(api(`/api/reviews/${r.id}/complete`, { body: o }),
                        checked ? "Proposed: AML compliance approves a medium- or high-risk review" : "Review complete")}>
                        {checked ? "Propose completion" : "Complete"}</button>
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
