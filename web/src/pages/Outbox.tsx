import { Fragment, useState } from "react";
import { CaseLink } from "../caselink";
import { api } from "../api";
import { Loading, useLoad, when } from "../util";

type Email = { id: number; to: string; to_name: string | null; subject: string; template: string; case_id: number | null;
  case_title: string | null; status: string; status_label: string; provider: string; error: string | null;
  created_at: string; sent_at: string | null; body: string };
type Box = { connection: { provider: string; label: string; sender: string | null; tenant_configured: boolean; auth: string | null;
  demo_hold: boolean; allowed_domains: string[] | null }; emails: Email[] };
const TONE: Record<string, string> = { held: "t-quiet", queued: "t-refer", sent: "t-clear", failed: "t-block" };

/** Every notice the custodian emails, and the connection it goes through. */
export default function Outbox() {
  const [status, setStatus] = useState("");
  const box = useLoad(() => api<Box>(`/api/outbox${status ? `?status=${status}` : ""}`), [status]);
  const [open, setOpen] = useState<number | null>(null);
  if (!box.data) return box.error ? <p className="error">{box.error}</p> : <Loading />;
  const c = box.data.connection;
  return (
    <>
      <h1>Email outbox</h1>
      <p className="muted">Every notice to RIAs and clients (items requested, account opened, application declined, change applied) is written here first, then sent through the custodian's email service.</p>
      <div className="panel">
        <strong>Connection: {c.label}</strong>
        <p className="small" style={{ marginBottom: 0 }}>
          {c.provider === "m365" ? <>Sends as <span className="mono">{c.sender ?? "(no sender set)"}</span> through Microsoft Graph, signed in by {c.auth ?? "(not configured)"}.{" "}</>
            : <>No email service is connected. A Microsoft 365 (Outlook) connection sends through Microsoft Graph from a shared mailbox; see docs/DEPLOY.md.{" "}</>}
          {c.demo_hold && "Demo mode holds every message here: the recipients are synthetic."}
          {c.allowed_domains && ` Live sending is limited to ${c.allowed_domains.join(", ")}.`}
        </p>
      </div>
      <div className="field" style={{ maxWidth: 220 }}><label htmlFor="obs">Show</label>
        <select id="obs" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All</option><option value="held">Held</option><option value="sent">Sent</option><option value="failed">Failed</option>
        </select></div>
      {box.data.emails.length === 0 ? <div className="empty">No notices yet.</div> : (
        <div className="scroll"><table>
          <thead><tr><th>To</th><th>Subject</th><th>Case</th><th>Status</th><th>When</th></tr></thead>
          <tbody>{box.data.emails.map((e) => (
            <Fragment key={e.id}>
              <tr className="link" onClick={() => setOpen(open === e.id ? null : e.id)}>
                <td className="small">{e.to_name}<div className="mono muted">{e.to}</div></td>
                <td>{e.subject}</td>
                <td className="small">{e.case_id ? <CaseLink id={e.case_id} onClick={(x) => x.stopPropagation()}>#{e.case_id}</CaseLink> : ""}</td>
                <td><span className={`pill ${TONE[e.status] ?? "t-quiet"}`} title={e.error ?? ""}>{e.status_label}</span></td>
                <td className="small">{when(e.sent_at ?? e.created_at)}</td>
              </tr>
              {open === e.id && <tr className="detail"><td colSpan={5}><pre className="email-body">{e.body}</pre>
                {e.error && <p className="small muted">{e.error}</p>}</td></tr>}
            </Fragment>))}</tbody>
        </table></div>)}
    </>
  );
}
