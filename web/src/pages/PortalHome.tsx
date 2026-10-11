import { Link } from "react-router-dom";
import { api, type CaseSummary } from "../api";
import { useAuth } from "../auth";
import { Loading, Status, useLoad, when } from "../util";

export default function PortalHome() {
  const { user } = useAuth();
  const accounts = useLoad(() => api<any[]>("/api/portal/accounts"), []);
  const cases = useLoad(() => api<CaseSummary[]>("/api/cases"), []);
  const client = user.role === "client";
  return (
    <>
      <h1>{client ? "Your self-directed account" : `${user.firm?.name}: accounts and requests`}</h1>
      <p className="muted">{client ? "You trade here and can change your account details. Your RIA can see this account but doesn't manage it."
        : "Submit applications and account changes for your firm's pure RIA accounts. Self-directed accounts are view-only for the RIA; the client changes them."}</p>
      <div className="actions" style={{ marginBottom: 16 }}>
        {!client && <Link to="/portal/new-account"><button>New account application</button></Link>}
        <Link to="/portal/change"><button className={client ? "" : "secondary"}>Request an account change</button></Link>
      </div>
      <h2>Accounts</h2>
      {!accounts.data ? <Loading /> : accounts.data.length ? (
        <div className="scroll"><table>
          <thead><tr><th>Account</th><th>Group</th><th>Status</th><th>Path</th><th>Address on file</th><th>Bank instructions</th></tr></thead>
          <tbody>{accounts.data.map((a) => (
            <tr key={a.id}><td>{a.display_name}<br /><span className="mono muted">{a.id}</span></td>
              <td>{a.group === "self_directed" ? "Self-directed" : "Pure RIA"}</td><td><Status status={a.status} /></td><td>{a.path ?? ""}</td>
              <td className="small">{a.address}</td>
              <td className="small">{a.bank_instructions.map((b: any, i: number) => <div key={i}>{b.name} ··{b.account_last4} ({b.status.replace(/_/g, " ")})</div>)}</td></tr>
          ))}</tbody>
        </table></div>
      ) : <div className="empty">No accounts yet.</div>}
      <h2>Requests</h2>
      {!cases.data ? <Loading /> : cases.data.length ? (
        <div className="scroll"><table>
          <thead><tr><th>Request</th><th>Status</th><th>Updated</th></tr></thead>
          <tbody>{cases.data.map((c) => (
            <tr key={c.id}><td><Link to={`/portal/cases/${c.id}`}>#{c.id} {c.title}</Link></td><td><Status status={c.status} /></td><td>{when(c.updated_at)}</td></tr>
          ))}</tbody>
        </table></div>
      ) : <div className="empty">No requests yet.</div>}
    </>
  );
}
