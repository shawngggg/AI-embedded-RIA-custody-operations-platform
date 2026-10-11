import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type CaseDetail, type Correction } from "../api";
import { isInternal, useAuth } from "../auth";
import { LANES, Loading, slaText, Status, useLoad, when } from "../util";

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

      {internal && <Decisions d={d} onDone={done} onFail={fail} />}
      {d.corrections.length > 0 && <Corrections d={d} onDone={done} onFail={fail} />}

      <div className="tabs" role="tablist">
        {[["steps", "Steps"], ["findings", "Rule findings"], ["risk", "Risk"], ["transfer", "Transfer"],
          ["extraction", "AI extraction"], ["codes", "Codes"], ["audit", "Audit trail"], ["data", "Application"]]
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
      {pending.length === 0 && !d.available_decisions.length && d.status.startsWith("PENDING") && !d.corrections.length && (
        <p className="muted small">This case waits for another team. Switch to that team's demo user to decide.</p>
      )}
      {d.decision_log.filter((x) => x.status !== "pending").map((x) => (
        <p key={x.id} className="small muted">{x.label}: {x.value_label} ({x.reference}), proposed by {x.proposed_by}, {x.status} by {x.decided_by}</p>
      ))}
    </div>
  );
}

function Corrections({ d, onDone, onFail }: { d: CaseDetail; onDone: (c: CaseDetail, msg: string) => void; onFail: (e: Error) => void }) {
  const { user } = useAuth();
  const ops = ["ops_analyst", "ops_supervisor"].includes(user.role);
  const [values, setValues] = useState<Record<string, any>>({});
  const [verify, setVerify] = useState(false);
  const [control, setControl] = useState("");
  const [bank, setBank] = useState({ name: "", aba: "", account_last4: "" });

  const submit = () => {
    const fields: Record<string, any> = {};
    d.corrections.forEach((c: Correction) => {
      if (c.type === "bool" && values[c.field]) fields[c.field] = true;
      if (c.type === "text" && (values[c.field] ?? "").trim()) fields[c.field] = values[c.field].trim();
      if (c.type === "bank" && bank.name && bank.aba && bank.account_last4) fields.new_bank = bank;
    });
    api<CaseDetail>(`/api/cases/${d.id}/corrections`, { body: { fields, verify_people: verify, control_person: control || null } })
      .then((c) => onDone(c, "Items recorded; the case ran again.")).catch(onFail);
  };

  return (
    <div className="panel">
      <h3 style={{ marginTop: 0 }}>Resolve the missing items</h3>
      {d.corrections.map((c) => (
        <div key={c.field} className="field">
          {c.type === "bool" && <label className="check"><input type="checkbox" checked={!!values[c.field]} onChange={(e) => setValues({ ...values, [c.field]: e.target.checked })} /> {c.label}</label>}
          {c.type === "text" && <><label htmlFor={c.field}>{c.label}</label><input id={c.field} type="text" value={values[c.field] ?? ""} onChange={(e) => setValues({ ...values, [c.field]: e.target.value })} /></>}
          {c.type === "verify_people" && (ops
            ? <label className="check"><input type="checkbox" checked={verify} onChange={(e) => setVerify(e.target.checked)} /> {c.label} (simulated verification service)</label>
            : <p className="muted small">{c.reason}: operations records identity verification after the documents arrive.</p>)}
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
      <button onClick={submit}>Record items and re-run</button>
    </div>
  );
}
