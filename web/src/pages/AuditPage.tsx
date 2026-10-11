import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Loading, useLoad, when } from "../util";

export default function AuditPage() {
  const [action, setAction] = useState("");
  const log = useLoad(() => api<any[]>(`/api/audit?limit=300${action ? `&action=${action}` : ""}`), [action]);
  return (
    <>
      <h1>Audit trail</h1>
      <p className="muted">Append-only: every sign-in, case run, AI read, decision, refusal, code, and grant. <a href="/api/audit/export">Download the full log as JSON</a>.</p>
      <div className="field" style={{ maxWidth: 320 }}>
        <label htmlFor="act">Show</label>
        <select id="act" value={action} onChange={(e) => setAction(e.target.value)}>
          <option value="">Everything</option>
          {["case_run", "decision_proposed", "decision_approved", "decision_refused", "restriction_placed", "restriction_removed",
            "restriction_removal_refused", "ai_extraction", "items_received", "change_applied", "user_created", "role_grant_approved",
            "role_grant_refused", "signed_in"].map((a) => <option key={a} value={a}>{a.replace(/_/g, " ")}</option>)}
        </select>
      </div>
      {!log.data ? <Loading /> : (
        <div className="scroll"><table>
          <thead><tr><th>When</th><th>Who</th><th>Action</th><th>On</th><th>Detail</th></tr></thead>
          <tbody>{log.data.map((e) => (
            <tr key={e.id}><td className="small">{when(e.at)}</td><td>{e.actor}<br /><span className="muted small">{e.role}</span></td>
              <td>{e.action.replace(/_/g, " ")}</td>
              <td>{e.case_id ? <Link to={`/cases/${e.case_id}`}>case #{e.case_id}</Link> : <span className="mono">{e.target_type} {e.target_id}</span>}</td>
              <td className="mono">{Object.entries(e.detail).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`).join(" · ")}</td></tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  );
}
