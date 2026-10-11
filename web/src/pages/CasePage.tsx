import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type CaseDetail, type Correction, type Doc } from "../api";
import { isInternal, useAuth } from "../auth";
import { Checks, DocList, DocStatus, DocUpload } from "../docs";
import { LANES, Loading, slaText, Status, useLoad, when } from "../util";

const DOC_CHOICES = [
  ["account_application", "Client-signed account application"], ["lpoa", "Limited power of attorney (LPOA)"],
  ["fee_authorization", "Fee deduction authorization"], ["w9", "Form W-9"], ["w8ben", "Form W-8BEN"],
  ["w8bene", "Form W-8BEN-E"], ["formation_documents", "Formation documents or trust instrument"],
  ["bo_certification", "Beneficial ownership certification"], ["ownership_chart", "Ownership chart"],
  ["custodial_agreement", "Custodial services agreement (signed)"], ["reliance_contract", "CIP reliance agreement"],
  ["identity_documents", "Identity documents for owners and control persons"], ["change_form", "Client-signed change form"],
  ["medallion", "Medallion signature guarantee"], ["other", "Other document"],
].map(([value, label]) => ({ value, label }));

const ROUTE: Record<string, string> = { acats_in_kind: "ACATS in kind", loi: "LOI", liquidate: "Liquidate",
  product_acceptance: "Product acceptance", review: "Review" };

export default function CasePage() {
  const { id } = useParams();
  const { user, refreshCounts } = useAuth();
  const internal = isInternal(user);
  const c = useLoad(() => api<CaseDetail>(`/api/cases/${id}`), [id]);
  const auditLog = useLoad(() => api<any[]>(`/api/cases/${id}/audit`), [id]);
  const [tab, setTab] = useState("steps");
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});

  const done = (detail: CaseDetail, ok: string) => {
    c.setData(detail);
    auditLog.reload();
    refreshCounts();
    setMsg({ ok });
  };
  const fail = (e: Error) => setMsg({ error: e.message });
  const refresh = (ok?: string) => api<CaseDetail>(`/api/cases/${id}`).then((detail) => {
    c.setData(detail); auditLog.reload(); if (ok) setMsg({ ok });
  }).catch(fail);

  if (c.error) return <p className="error">{c.error}</p>;
  if (!c.data) return <Loading />;
  const d = c.data;
  const r = d.result ?? {};
  const back = internal ? "/queues" : "/portal";
  const active = d.restrictions.filter((x) => x.active);

  return (
    <>
      <p className="small"><Link to={back}>← Back</Link></p>
      <h1>#{d.id} {d.title}</h1>
      <p className="muted small">
        {d.kind === "firm" ? "RIA firm onboarding (map 01)" : d.kind === "account" ? "Client account opening (map 02)" : "Account change (map 04)"}
        {" · "}{d.target_id}{d.path ? ` · Path ${d.path}` : ""}{d.firm ? ` · ${d.firm}` : ""}
        {r.policy_version ? ` · policy ${r.policy_version}` : ""}
      </p>
      <div className="row" style={{ alignItems: "center", margin: "12px 0" }}>
        <Status status={d.status} stamp />
        {d.sla.due && <span className={d.sla.breached ? "error" : "muted"}>{d.queue_label}: {slaText(d.sla.hours_left)}</span>}
      </div>
      {active.length > 0 && (
        <p>{active.map((x) => <span key={x.id} className="code" title={x.description}>{x.code} · owner {x.owner.replace(/_/g, " ")}</span>)}</p>
      )}
      {msg.ok && <p className="ok" role="status">{msg.ok}</p>}
      {msg.error && <p className="error" role="alert">{msg.error}</p>}

      {r.requested_items?.length > 0 && d.status !== "OPEN" && (
        <p><strong>Requested:</strong> {r.requested_items.join("; ")}</p>
      )}

      {internal && d.acceptance && <Acceptance d={d} onDone={done} onFail={fail} />}
      {internal && d.rework && d.status === "PENDING_REWORK" && <Rework d={d} onDone={done} onFail={fail} />}
      {internal && d.rescission?.can_request && <Rescind d={d} onDone={done} onFail={fail} />}
      {internal && <Decisions d={d} onDone={done} onFail={fail} />}
      {d.corrections.length > 0 && <Corrections d={d} onDone={done} onFail={fail} onRefresh={refresh} />}

      <div className="tabs" role="tablist">
        {[["steps", "Steps"], ["documents", `Documents (${d.documents.length})`], ["findings", "Rule findings"], ["risk", "Risk"],
          ["transfer", "Transfer"], ["extraction", "AI extraction"], ["codes", "Codes"], ["audit", "Audit trail"],
          ["data", "Application"]]
          .filter(([k]) => (k !== "transfer" || r.transfer) && (k !== "extraction" || d.extra?.extraction) && (k !== "risk" || r.risk))
          .map(([k, label]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>)}
      </div>

      {tab === "steps" && (
        <ol className="steps">
          {(r.steps ?? []).map((s: any, i: number) => (
            <li key={i} style={{ ["--lane" as any]: `var(${LANES[s.lane] ?? "--ink"})` }}>
              <span className="lane">{s.lane}</span>
              <span className="stepname">{s.step}</span>
              <span className="outcome">{s.outcome}</span>
              {s.detail.length > 0 && <ul>{s.detail.map((x: string, j: number) => <li key={j}>{x}</li>)}</ul>}
              {s.rule_ids.length > 0 && <span className="mono muted">{s.rule_ids.join(" · ")}</span>}
            </li>
          ))}
        </ol>
      )}
      {tab === "documents" && (
        <>
          <p className="muted small">Every item marked received points to a document here. Each file keeps its SHA-256 fingerprint,
            so anyone can later prove it wasn't changed. Automated checks read the text; a person in operations confirms good order.</p>
          <DocList docs={d.documents} onChanged={() => refresh("Marked not in good order.")} />
          {!d.closed_at && ["ops_analyst", "ops_supervisor", "ria_admin", "ria_user", "client"].includes(user.role) && (
            <>
              <h3>Add a document</h3>
              <DocUpload caseId={d.id} types={DOC_CHOICES} onUploaded={(doc) => refresh(`${doc.filename} added.`)} />
            </>
          )}
        </>
      )}
      {tab === "findings" && (r.findings?.decisions?.length ? (
        <div className="scroll"><table>
          <thead><tr><th>Rule</th><th>Finding</th><th>Source</th><th>Owner</th></tr></thead>
          <tbody>{r.findings.decisions.map((f: any, i: number) => (
            <tr key={i}><td className="mono">{f.rule_id} v{f.rule_version}</td><td>{f.reason}</td><td>{f.source}</td><td>{f.owner}</td></tr>
          ))}</tbody>
        </table></div>
      ) : <div className="empty">No rule findings: every rule in force passed.</div>)}
      {tab === "risk" && r.risk && (
        <>
          <p><strong>{r.risk.tier}</strong> ({r.risk.score} points), rated on {r.risk.information_basis}. Next review {r.risk.next_review}.</p>
          {r.risk.factors.length ? (
            <div className="scroll"><table>
              <thead><tr><th>Factor</th><th>Description</th><th>Points</th><th>Requires EDD</th><th>Source</th></tr></thead>
              <tbody>{r.risk.factors.map((f: any) => (
                <tr key={f.factor_id}><td className="mono">{f.factor_id}</td><td>{f.description}</td><td>+{f.points}</td><td>{f.mandatory_edd ? "Yes" : ""}</td><td>{f.source}</td></tr>
              ))}</tbody>
            </table></div>
          ) : <div className="empty">No risk factors apply.</div>}
          {r.risk.edd_requirements.length > 0 && <><h3>EDD requirements</h3><ul>{r.risk.edd_requirements.map((x: string) => <li key={x}>{x}</li>)}</ul></>}
        </>
      )}
      {tab === "transfer" && r.transfer && (
        <>
          <p>Release: <strong>{r.transfer.release}</strong></p>
          <div className="scroll"><table>
            <thead><tr><th>Position</th><th>Route</th><th>In kind</th><th>Sold</th><th>Reason</th></tr></thead>
            <tbody>{r.transfer.dispositions.map((p: any) => (
              <tr key={p.security_id}><td>{p.description}</td><td>{ROUTE[p.route] ?? p.route}</td><td>{p.quantity_in_kind}</td><td>{p.quantity_to_liquidate}</td><td>{p.reason}</td></tr>
            ))}</tbody>
          </table></div>
        </>
      )}
      {tab === "extraction" && d.extra?.extraction && (
        <>
          <p className="muted small">
            {d.extra.extraction.mode === "live" ? `Live model call (${d.extra.extraction.model}).` :
              "Illustrative model output, hand-written for the demo; not a recorded API response."}{" "}
            Every value needs a quote found in the documents, and the value must appear in its quote.
          </p>
          <div className="scroll"><table>
            <thead><tr><th>Accepted</th><th>Value</th><th>Quote</th></tr></thead>
            <tbody>{d.extra.extraction.accepted.map((a: any, i: number) => <tr key={i}><td>{a.field}</td><td>{String(a.value)}</td><td>{a.quote}</td></tr>)}</tbody>
          </table></div>
          {d.extra.extraction.rejected.length > 0 && (
            <div className="scroll" style={{ marginTop: 10 }}><table>
              <thead><tr><th>Rejected</th><th>Value</th><th>Why</th></tr></thead>
              <tbody>{d.extra.extraction.rejected.map((a: any, i: number) => <tr key={i}><td>{a.field}</td><td>{String(a.value)}</td><td>{a.reason}</td></tr>)}</tbody>
            </table></div>
          )}
        </>
      )}
      {tab === "codes" && (d.restrictions.length ? (
        <div className="scroll"><table>
          <thead><tr><th>Code</th><th>Owner</th><th>Reason</th><th>Placed</th><th>Removed</th></tr></thead>
          <tbody>{d.restrictions.map((x) => (
            <tr key={x.id}><td><span className={`code ${x.active ? "" : "off"}`}>{x.code}</span></td><td>{x.owner}</td><td>{x.reason}</td>
              <td>{x.placed_by}, {when(x.placed_at)}</td>
              <td>{x.removed_at ? `${x.removed_by}, ${when(x.removed_at)}${x.authorization ? ` (${x.authorization})` : ""}` : ""}</td></tr>
          ))}</tbody>
        </table></div>
      ) : <div className="empty">No restriction codes on {d.target_id}.</div>)}
      {tab === "audit" && auditLog.data && (
        <div className="scroll"><table>
          <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Detail</th></tr></thead>
          <tbody>{auditLog.data.map((e) => (
            <tr key={e.id}><td>{when(e.at)}</td><td>{e.actor}<br /><span className="muted small">{e.role}</span></td><td>{e.action.replace(/_/g, " ")}</td>
              <td className="mono">{Object.entries(e.detail).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`).join(" · ")}</td></tr>
          ))}</tbody>
        </table></div>
      )}
      {tab === "data" && <pre>{JSON.stringify(d.application, null, 2)}</pre>}
    </>
  );
}

function Decisions({ d, onDone, onFail }: { d: CaseDetail; onDone: (c: CaseDetail, msg: string) => void; onFail: (e: Error) => void }) {
  const { user } = useAuth();
  const [form, setForm] = useState<Record<string, { value: string; reference: string; note: string }>>({});
  const pending = d.decision_log.filter((x) => x.status === "pending");
  const reload = () => api<CaseDetail>(`/api/cases/${d.id}`);

  const propose = (type: string) => {
    const f = form[type];
    api(`/api/cases/${d.id}/decisions`, { body: { decision_type: type, value: f.value, reference: f.reference, note: f.note || null } })
      .then(() => reload()).then((c) => onDone(c, "Proposed. A second person must approve it.")).catch(onFail);
  };
  const decide = (decisionId: number, approve: boolean) => {
    api<CaseDetail>(`/api/decisions/${decisionId}/${approve ? "approve" : "reject"}`, { body: {} })
      .then((c) => onDone(c, approve ? "Approved; the case ran again with the decision." : "Rejected; nothing changed.")).catch(onFail);
  };

  if (!d.available_decisions.length && !pending.length && !d.decision_log.length) return null;
  return (
    <div className="decide">
      {pending.map((p) => {
        const canApprove = p.approver_roles.includes(user.role) && p.proposed_by_id !== user.id;
        return (
          <div key={p.id} style={{ marginBottom: 10 }}>
            <h3>Waiting for a checker: {p.label}</h3>
            <p><strong>{p.value_label}</strong> · reference {p.reference} · proposed by {p.proposed_by}, {when(p.proposed_at)}{p.note ? ` · "${p.note}"` : ""}</p>
            {canApprove ? (
              <div className="actions">
                <button onClick={() => decide(p.id, true)}>Approve</button>
                <button className="secondary" onClick={() => decide(p.id, false)}>Reject</button>
              </div>
            ) : (
              <p className="muted small">
                {p.proposed_by_id === user.id ? "You proposed this, so someone else must approve it." :
                  `Approval needs: ${p.approver_roles.join(" or ").replace(/_/g, " ")}. Switch user to approve.`}
              </p>
            )}
          </div>
        );
      })}
      {pending.length === 0 && d.available_decisions.map((a) => {
        const f = form[a.decision_type] ?? { value: a.values[0].value, reference: "", note: "" };
        const set = (patch: Partial<typeof f>) => setForm({ ...form, [a.decision_type]: { ...f, ...patch } });
        return (
          <div key={a.decision_type}>
            <h3>{a.label}</h3>
            <div className="row">
              <div className="field"><label htmlFor={`v-${a.decision_type}`}>Decision</label>
                <select id={`v-${a.decision_type}`} value={f.value} onChange={(e) => set({ value: e.target.value })}>
                  {a.values.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}
                </select></div>
              <div className="field"><label htmlFor={`r-${a.decision_type}`}>Reference</label>
                <input id={`r-${a.decision_type}`} type="text" placeholder="e.g. DET-2026-0412" value={f.reference} onChange={(e) => set({ reference: e.target.value })} /></div>
            </div>
            <div className="field"><label htmlFor={`n-${a.decision_type}`}>Note (optional)</label>
              <input id={`n-${a.decision_type}`} type="text" value={f.note} onChange={(e) => set({ note: e.target.value })} /></div>
            <button disabled={f.reference.trim().length < 3} onClick={() => propose(a.decision_type)}>Propose for approval</button>
            <p className="muted small">Approval needs a different person: {a.approver_roles.join(" or ").replace(/_/g, " ")}.</p>
          </div>
        );
      })}
      {pending.length === 0 && !d.available_decisions.length && d.status.startsWith("PENDING") && d.status !== "PENDING_ACCEPTANCE" && !d.corrections.length && (
        <p className="muted small">This case waits for another team. Switch to that team's demo user to decide.</p>
      )}
      {d.decision_log.filter((x) => x.status !== "pending").map((x) => (
        x.decision_type === "acceptance"
          ? <p key={x.id} className="small muted">{x.label}: {x.value_label} by {x.reference}, {when(x.decided_at)}{x.note ? ` · "${x.note}"` : ""}</p>
          : <p key={x.id} className="small muted">{x.label}: {x.value_label} ({x.reference}), proposed by {x.proposed_by}, {x.status} by {x.decided_by}</p>
      ))}
    </div>
  );
}

function Acceptance({ d, onDone, onFail }: { d: CaseDetail; onDone: (c: CaseDetail, msg: string) => void; onFail: (e: Error) => void }) {
  const [note, setNote] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const outstanding: string[] = d.result?.requested_items ?? [];
  const a = d.acceptance!;
  const r = d.result ?? {};
  const good = d.documents.filter((x) => x.status === "in_good_order").length;
  const send = (value: "accepted" | "declined") =>
    api<CaseDetail>(`/api/cases/${d.id}/acceptance`, { body: { value, note: note || null, reviewed } })
      .then((c) => onDone(c, value === "accepted" ? (outstanding.length ? "Accepted. The account opened, restricted from trading until the outstanding items arrive." : "Accepted. The case ran again and opened.") : "Declined."))
      .catch(onFail);
  return (
    <div className="accept">
      <h3>Principal acceptance (FINRA Rule 4512)</h3>
      <p className="small">Every check is complete. A registered principal on the RIA service team accepts the
        {d.kind === "firm" ? " RIA relationship and its master account" : " account"} before it opens.</p>
      <ul className="small">
        {r.path && <li>Due diligence path: {r.path === "A" ? "A (relies on the RIA's CIP)" : "B (the custodian carries CIP and CDD)"}</li>}
        {r.risk && <li>Risk: {r.risk.tier} ({r.risk.score} points); next review {r.risk.next_review}</li>}
        <li>Documents in good order: {good} of {d.documents.length}</li>
        <li>Restriction codes: {d.restrictions.filter((x) => x.active).map((x) => x.code).join(", ") || "none"}</li>
        <li>Prepared without manual steps: {d.manual_steps === 0 ? "yes" : `no (${d.manual_steps})`}</li>
      </ul>
      {outstanding.length > 0 && (
        <p className="small"><strong>Opens restricted from trading until these non-regulatory items arrive:</strong> {outstanding.join("; ")}.
          Every regulatory requirement (KYC, CDD, EDD, CIP, AML) is met.</p>
      )}
      {a.can_accept ? (
        <>
          <div className="field"><label htmlFor="acc-note">Note (optional)</label>
            <input id="acc-note" type="text" value={note} onChange={(e) => setNote(e.target.value)} /></div>
          <label className="check"><input type="checkbox" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} />
            I reviewed the documents, rule findings, and risk for this {d.kind === "firm" ? "relationship" : "account"}</label>
          <p className="small">You sign as <strong>{a.signs_as}</strong>.</p>
          <div className="actions">
            <button disabled={!reviewed} onClick={() => send("accepted")}>Accept</button>
            <button className="secondary" onClick={() => send("declined")}>Decline</button>
          </div>
        </>
      ) : <p className="muted small">{a.reason}{a.reason?.startsWith("A registered principal") ? ". Switch to Avery Collins to accept." : ""}</p>}
    </div>
  );
}

function Rescind({ d, onDone, onFail }: { d: CaseDetail; onDone: (c: CaseDetail, msg: string) => void; onFail: (e: Error) => void }) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState("accepted_before_review");
  const [returnTo, setReturnTo] = useState("review");
  const [reason, setReason] = useState("");
  const r = d.rescission!;
  const send = () => api<CaseDetail>(`/api/cases/${d.id}/rescission`, { body: { kind, return_to: returnTo, reason } })
    .then((c) => onDone(c, "Restricted now (RSCN). The rescission waits for the RIA service team lead.")).catch(onFail);
  if (!open) return (
    <div className="actions" style={{ marginBottom: 12 }}>
      <button className="secondary small" onClick={() => setOpen(true)}>Rescind acceptance</button>
      <span className="muted small">For a regulatory issue or an acceptance made before the review. A non-regulatory issue
        doesn't need this: reopen the document instead, and the account stays open but restricted.</span>
    </div>
  );
  return (
    <div className="accept">
      <h3>Rescind the acceptance</h3>
      <p className="small">The account is restricted at once (code RSCN: no activity). The RIA service team lead approves or
        rejects the rescission, and an approved rescission is logged as an operational risk event.</p>
      <div className="row">
        <div className="field"><label htmlFor="rk">Why</label>
          <select id="rk" value={kind} onChange={(e) => { setKind(e.target.value); setReturnTo(e.target.value === "regulatory_issue" ? "operations" : "review"); }}>
            {Object.entries(r.kinds).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select></div>
        <div className="field"><label htmlFor="rt">Then the case goes</label>
          <select id="rt" value={returnTo} onChange={(e) => setReturnTo(e.target.value)}>
            {Object.entries(r.return_to).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select></div>
      </div>
      <div className="field"><label htmlFor="rr">What happened</label>
        <input id="rr" type="text" value={reason} onChange={(e) => setReason(e.target.value)} /></div>
      <div className="actions">
        <button className="danger" disabled={reason.trim().length < 10} onClick={send}>Restrict and ask the lead to rescind</button>
        <button className="secondary" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </div>
  );
}

function Rework({ d, onDone, onFail }: { d: CaseDetail; onDone: (c: CaseDetail, msg: string) => void; onFail: (e: Error) => void }) {
  const { user } = useAuth();
  const ops = ["ops_analyst", "ops_supervisor"].includes(user.role);
  const [note, setNote] = useState("");
  const w = d.rework!;
  const send = () => api<CaseDetail>(`/api/cases/${d.id}/rework-complete`, { body: { note } })
    .then((c) => onDone(c, "Rework recorded; the case ran again.")).catch(onFail);
  return (
    <div className="decide">
      <h3>Rework after a rescission ({w.reference})</h3>
      <p>{w.reason}</p>
      <p className="muted small">Asked by {w.requested_by}, approved by {w.approved_by}, {when(w.at)}. Reopen any document that isn't
        right in the Documents tab, attach the replacement, then send the case back to the principal.</p>
      {ops ? (
        <>
          <div className="field"><label htmlFor="rw">What you fixed</label>
            <input id="rw" type="text" value={note} onChange={(e) => setNote(e.target.value)} /></div>
          <button disabled={note.trim().length < 5} onClick={send}>Send back to the principal</button>
        </>
      ) : <p className="muted small">Operations does the rework.</p>}
    </div>
  );
}

function Corrections({ d, onDone, onFail, onRefresh }: {
  d: CaseDetail; onDone: (c: CaseDetail, msg: string) => void; onFail: (e: Error) => void; onRefresh: (ok?: string) => void;
}) {
  const { user } = useAuth();
  const ops = ["ops_analyst", "ops_supervisor"].includes(user.role);
  const [values, setValues] = useState<Record<string, any>>({});
  const [chosen, setChosen] = useState<Record<string, number>>({});     // field -> document id checked in good order
  const [overrides, setOverrides] = useState<Record<string, string>>({}); // field -> why a failed check is fine
  const [another, setAnother] = useState<Record<string, boolean>>({});     // field -> show the upload for a different copy
  const [control, setControl] = useState("");
  const [bank, setBank] = useState({ name: "", aba: "", account_last4: "" });
  const docItems = d.corrections.filter((c) => c.type === "document");
  const otherItems = d.corrections.filter((c) => c.type !== "document");

  const submit = () => {
    const fields: Record<string, any> = {};
    const documents: Record<string, number> = {};
    let verify = false;
    d.corrections.forEach((c: Correction) => {
      if (c.type === "document" && chosen[c.field]) {
        documents[c.field] = chosen[c.field];
        if (c.field === "verify_people") verify = true;
        else fields[c.field] = c.field === "tax_form" ? "from document" : true;
      }
      if (c.type === "bool" && values[c.field]) fields[c.field] = true;
      if (c.type === "text" && (values[c.field] ?? "").trim()) fields[c.field] = values[c.field].trim();
      if (c.type === "bank" && bank.name && bank.aba && bank.account_last4) fields.new_bank = bank;
    });
    const notes = Object.fromEntries(Object.entries(overrides).filter(([k, v]) => documents[k] && v.trim()));
    api<CaseDetail>(`/api/cases/${d.id}/corrections`, { body: { fields, documents, overrides: notes, verify_people: verify, control_person: control || null } })
      .then((c) => onDone(c, "Items recorded; the case ran again.")).catch(onFail);
  };

  const canSubmit = ops ? true : otherItems.length > 0;
  return (
    <div className="panel">
      <h3 style={{ marginTop: 0 }}>Resolve the missing items</h3>
      {docItems.map((c) => (
        <div key={c.field} className="field">
          <strong>{c.label}</strong> <span className="muted small mono">{c.rule_id}</span>
          {c.documents.map((doc: Doc) => (
            <div key={doc.id} className="evidence">
              <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                <span><a href={`/api/documents/${doc.id}/file`} target="_blank" rel="noreferrer">{doc.filename}</a>
                  <span className="muted small"> · {doc.type_label} · {doc.received_on} via {doc.channel_label} · added by {doc.uploaded_by}</span></span>
                <DocStatus d={doc} />
              </div>
              <div className="small"><Checks d={doc} /></div>
              {ops && (
                <label className="check" style={{ marginTop: 6 }}>
                  <input type="checkbox" checked={chosen[c.field] === doc.id}
                    onChange={(e) => setChosen({ ...chosen, [c.field]: e.target.checked ? doc.id : 0 })} />
                  {c.field === "verify_people" ? "I checked these documents and verified identity" : "I opened this document and it's in good order"}
                </label>
              )}
              {ops && chosen[c.field] === doc.id && doc.checks.some((k) => k.result === "fail") && (
                <div className="field" style={{ marginTop: 6 }}>
                  <label htmlFor={`ov-${doc.id}`}>It failed an automated check. Why is it in good order anyway?</label>
                  <input id={`ov-${doc.id}`} type="text" value={overrides[c.field] ?? ""}
                    onChange={(e) => setOverrides({ ...overrides, [c.field]: e.target.value })} />
                </div>
              )}
            </div>
          ))}
          {!c.documents.length && <p className="muted small">Nothing attached yet.{ops ? "" : " Upload it here; operations checks it and records the item."}</p>}
          {!ops && c.documents.length > 0 && <p className="muted small">Received. Operations checks it and records the item.</p>}
          {c.documents.length === 0 || another[c.field] ? (
            <DocUpload compact caseId={d.id} types={c.doc_types}
              onUploaded={(doc) => { setAnother({ ...another, [c.field]: false }); onRefresh(`${doc.filename} attached.`); }} />
          ) : (
            <button type="button" className="small secondary" onClick={() => setAnother({ ...another, [c.field]: true })}>
              Attach a different copy</button>
          )}
        </div>
      ))}
      {otherItems.map((c) => (
        <div key={c.field} className="field">
          {c.type === "bool" && <label className="check"><input type="checkbox" checked={!!values[c.field]} onChange={(e) => setValues({ ...values, [c.field]: e.target.checked })} /> {c.label}</label>}
          {c.type === "text" && <><label htmlFor={c.field}>{c.label}</label><input id={c.field} type="text" value={values[c.field] ?? ""} onChange={(e) => setValues({ ...values, [c.field]: e.target.value })} /></>}
          {c.type === "control_person" && <><label htmlFor="cp">{c.label}</label><input id="cp" type="text" value={control} onChange={(e) => setControl(e.target.value)} /></>}
          {c.type === "bank" && (
            <div className="row">
              <div className="field"><label htmlFor="bn">Bank name</label><input id="bn" type="text" value={bank.name} onChange={(e) => setBank({ ...bank, name: e.target.value })} /></div>
              <div className="field"><label htmlFor="ba">ABA routing number</label><input id="ba" type="text" value={bank.aba} onChange={(e) => setBank({ ...bank, aba: e.target.value })} /></div>
              <div className="field"><label htmlFor="bl">Account last 4</label><input id="bl" type="text" value={bank.account_last4} onChange={(e) => setBank({ ...bank, account_last4: e.target.value })} /></div>
            </div>
          )}
          <span className="muted small mono">{c.rule_id}</span>
        </div>
      ))}
      {canSubmit && <button onClick={submit}>Record items and re-run</button>}
    </div>
  );
}
