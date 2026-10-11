import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Decision } from "../api";
import { useAuth } from "../auth";
import { Loading, useLoad, when } from "../util";

export default function Approvals() {
  const { refreshCounts } = useAuth();
  const list = useLoad(() => api<Decision[]>("/api/decisions"), []);
  const [msg, setMsg] = useState<string | null>(null);
  const decide = (id: number, approve: boolean) =>
    api(`/api/decisions/${id}/${approve ? "approve" : "reject"}`, { body: {} })
      .then(() => { setMsg(approve ? "Approved; the case ran again." : "Rejected."); list.reload(); refreshCounts(); })
      .catch((e) => setMsg(e.message));
  return (
    <>
      <h1>Approvals</h1>
      <p className="muted">Maker-checker: every decision proposed by one person waits here for a second person with the approving role.</p>
      {msg && <p className="ok">{msg}</p>}
      {!list.data ? <Loading /> : list.data.length ? (
        <div className="scroll"><table>
          <thead><tr><th>Case</th><th>Decision</th><th>Proposed by</th><th>Needs</th><th></th></tr></thead>
          <tbody>{list.data.map((d) => (
            <tr key={d.id}>
              <td><Link to={`/cases/${d.case_id}`}>#{d.case_id} {d.case_title}</Link></td>
              <td>{d.label}: <strong>{d.value_label}</strong><br /><span className="muted small">ref {d.reference}</span></td>
              <td>{d.proposed_by}<br /><span className="muted small">{when(d.proposed_at)}</span></td>
              <td>{d.approver_roles.join(" or ").replace(/_/g, " ")}</td>
              <td>{d.can_approve ? <div className="actions" style={{ marginTop: 0 }}>
                <button className="small" onClick={() => decide(d.id, true)}>Approve</button>
                <button className="small secondary" onClick={() => decide(d.id, false)}>Reject</button></div>
                : <span className="muted small">Not yours to approve</span>}</td>
            </tr>
          ))}</tbody>
        </table></div>
      ) : <div className="empty">No decisions waiting for approval.</div>}
    </>
  );
}
