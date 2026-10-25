import { Actions, Entries, Linked, Summary, type R2Case } from "../r2";
import { Fragment, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type AiSuggestion, type CaseDetail, type Correction, type Doc, type PortalCase } from "../api";
import { isInternal, useAuth } from "../auth";
import { Checks, DocLinks, DocList, DocStatus, DocUpload } from "../docs";
import { LANES, Loading, slaText, Status, useLoad, when } from "../util";
import { type Message, MessageBody } from "./Messages";

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
  const { user } = useAuth();
  return isInternal(user) ? <InternalCase /> : <PortalCase />;
}

const DOT: Record<string, string> = { OPEN: "clear", APPLIED: "clear", ACTIVE: "clear", DECLINED: "block" };

/** The portal's view of a request: where it stands in plain words, what's needed, and the documents sent. */
function PortalCase() {
  const { id } = useParams();
  const { user } = useAuth();
  const c = useLoad(() => api<PortalCase>(`/api/cases/${id}`), [id]);
  const [tab, setTab] = useState("documents");
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  if (c.error) return <p className="error">{c.error}</p>;
  if (!c.data) return <Loading />;
  const d = c.data;
  const refresh = (ok?: string) => api<PortalCase>(`/api/cases/${id}`).then((x) => { c.setData(x); if (ok) setMsg({ ok }); })
    .catch((e: Error) => setMsg({ error: e.message }));
  const done = (x: unknown, ok: string) => { c.setData(x as PortalCase); setMsg({ ok }); };
  const fail = (e: Error) => setMsg({ error: e.message });
  const STATUS_WORDS: Record<string, string> = { SUBMITTED: "Submitted" };
  return (
    <>
      <p className="small"><Link to="/portal/accounts">← Accounts and requests</Link></p>
      <h1>{d.title}</h1>
      <p className="muted small">Request #{d.id}{d.target_id ? ` · ${d.target_id}` : ""}{d.firm ? ` · ${d.firm}` : ""} · submitted {when(d.created_at)}</p>
      <div className="row" style={{ alignItems: "center", margin: "12px 0" }}><Status status={d.status} stamp /></div>
      <p>{d.status_note}</p>
      {d.requested_items.length > 0 && <ul>{d.requested_items.map((i) => <li key={i}>{i}</li>)}</ul>}
      {d.restrictions_portal.length > 0 && (
        <div className="panel">
          <strong>Account restrictions</strong>
          <ul className="small" style={{ marginBottom: 0 }}>{d.restrictions_portal.map((r) => (
            <li key={r.reason}>{r.reason}: no {r.limits.join(", ")} for now</li>))}</ul>
        </div>
      )}
      {msg.ok && <p className="ok" role="status">{msg.ok}</p>}
      {msg.error && <p className="error" role="alert">{msg.error}</p>}
      {d.corrections.length > 0 && <Corrections d={d as unknown as CaseDetail} onDone={done} onFail={fail} onRefresh={refresh} />}

      <div className="tabs" role="tablist">
        {[["documents", `Documents (${d.documents.length})`], ["details", "What you submitted"], ["history", "History"]]
          .map(([k, label]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>)}
      </div>
      {tab === "documents" && (
        <>
          <DocList docs={d.documents} onChanged={() => refresh()} />
          {!d.closed_at && (
            <>
              <h3>Add a document</h3>
              <DocUpload caseId={d.id} types={DOC_CHOICES} onUploaded={(doc) => refresh(`${doc.filename} added. We'll check it and let you know if anything's missing.`)} />
            </>
          )}
        </>
      )}
      {tab === "details" && (d.details.length ? (
        <dl className="details">{d.details.map((x, i) => <div key={i}><dt>{x.label}</dt><dd>{x.value}</dd></div>)}</dl>
      ) : <div className="empty">Nothing to show.</div>)}
      {tab === "history" && (
        <ol className="history">{d.history.map((h, i) => (
          <li key={i} className={`t-${DOT[h.status] ?? "quiet"}`}><span>{STATUS_WORDS[h.status] ?? <Status status={h.status} />}</span> <span className="muted small">{when(h.at)}</span></li>))}
        </ol>
      )}
      <p className="small muted" style={{ marginTop: 24 }}>Questions about this request? {user.role === "client" ? "Contact your RIA, or" : "Contact"} the RIA service team at riaservice@custodian.example.</p>
    </>
  );
}

function InternalCase() {
  const { id } = useParams();
  const { user, refreshCounts } = useAuth();
  const c = useLoad(() => api<R2Case>(`/api/cases/${id}`), [id]);
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
  const back = "/queues";
  const active = d.restrictions.filter((x) => x.active);

  return (
    <>
      <p className="small"><Link to={back}>← Back</Link></p>
      <h1>#{d.id} {d.title}</h1>
      <p className="muted small">
        {d.map ?? (d.kind === "firm" ? "RIA firm onboarding (map 01)" : d.kind === "account" ? "Client account opening (map 02)" : "Account change (map 04)")}
        {" · "}{d.target_id}{d.path ? ` · Path ${d.path}` : ""}{d.firm ? ` · ${d.firm}` : ""}
        {r.policy_version ? ` · policy ${r.policy_version}` : ""}
      </p>
      <div className="row" style={{ alignItems: "center", margin: "12px 0" }}>
        <Status status={d.status} stamp />
        {d.sla.due && <span className={d.sla.breached ? "error" : "muted"}>{d.queue_label}: {slaText(d.sla.hours_left)}</span>}
        {d.queue && <Owner d={d} onDone={done} onFail={fail} />}
      </div>
      {active.length > 0 && (
        <p>{active.map((x) => <span key={x.id} className="code" title={x.description}>{x.code} · owner {x.owner.replace(/_/g, " ")}</span>)}</p>
      )}
      {msg.ok && <p className="ok" role="status">{msg.ok}</p>}
      {msg.error && <p className="error" role="alert">{msg.error}</p>}

      {r.requested_items?.length > 0 && d.status !== "OPEN" && (
        <p><strong>Requested:</strong> {r.requested_items.join("; ")}</p>
      )}

      {d.ai_suggestions.some((s) => s.status === "pending") && <AiReview d={d} onDone={done} onFail={fail} />}
      {d.identity_check && <IdentityCheck d={d} onDone={done} onFail={fail} />}
      {d.acceptance && <Acceptance d={d} onDone={done} onFail={fail} />}
      {d.rework && d.status === "PENDING_REWORK" && <Rework d={d} onDone={done} onFail={fail} />}
      {d.rescission?.can_request && <Rescind d={d} onDone={done} onFail={fail} />}
      {(d.actions?.length ?? 0) > 0 && <Actions caseId={d.id} actions={d.actions!} documents={d.documents} onDone={done} onFail={fail} />}
      <Decisions d={d} onDone={done} onFail={fail} />
      {d.corrections.length > 0 && <Corrections d={d} onDone={done} onFail={fail} onRefresh={refresh} />}
      {d.summary && Object.keys(d.summary).length > 0 && <div className="panel"><Summary summary={d.summary} />
        {(d.linked?.length ?? 0) > 0 && <Linked linked={d.linked!} />}</div>}

      <div className="tabs" role="tablist">
        {[["steps", "Steps"], ["ledger", `Ledger (${d.ledger_entries?.length ?? 0})`], ["documents", `Documents (${d.documents.length})`],
          ["findings", "Rule findings"], ["risk", "Risk"],
          ["transfer", "Transferability"], ["extraction", "AI extraction"], ["codes", "Codes"], ["audit", "Audit trail"],
          ["data", "Request"], ["discussion", "Discussion"]]
          .filter(([k]) => (k !== "transfer" || r.transfer) && (k !== "extraction" || d.extra?.extraction) && (k !== "risk" || r.risk)
            && (k !== "discussion" || user.capabilities?.messages) && (k !== "ledger" || d.map)
            && (k !== "findings" || !d.map))
          .map(([k, label]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>)}
      </div>

      {tab === "discussion" && <Discussion caseId={d.id} queue={d.queue} />}
      {tab === "ledger" && <>
        <p className="muted small">Every entry posts at three layers (the client account, its RIA's master account, and the general ledger)
          and is checked before it's written: a posting that would break the rollup is refused (LED-001).</p>
        <Entries entries={d.ledger_entries ?? []} />
      </>}
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
          {!d.closed_at && ["ops_analyst", "ops_supervisor"].includes(user.role) && (
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
          {d.ai_suggestions.length ? <AiReview d={d} onDone={done} onFail={fail} readOnly /> : (
            <div className="scroll"><table>
              <thead><tr><th>Accepted</th><th>Value</th><th>Quote</th></tr></thead>
              <tbody>{d.extra.extraction.accepted.map((a: any, i: number) => <tr key={i}><td>{a.field}</td><td>{String(a.value)}</td><td>{a.quote}</td></tr>)}</tbody>
            </table></div>)}
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
      {(pending.length > 0 || d.available_decisions.length > 0) &&
        <DocLinks docs={d.documents} title="Documents on this case" empty={d.related_documents ? undefined : "No documents on this case."} />}
      {(pending.length > 0 || d.available_decisions.length > 0) && d.related_documents &&
        <DocLinks docs={d.related_documents.documents} title={d.related_documents.title} />}
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
      {pending.length === 0 && !d.available_decisions.length && d.status.startsWith("PENDING") && !["PENDING_ACCEPTANCE", "PENDING_AI_REVIEW"].includes(d.status) && !d.corrections.length && (
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

const OUTCOME: Record<string, [string, string]> = {
  pending: ["To review", "quiet"], accepted: ["Accepted", "clear"], edited: ["Edited", "refer"], rejected: ["Rejected", "block"] };
const PERSON_ROLES = [["owner", "Owner"], ["control_person", "Control person"], ["principal", "Principal"]];

/** Onboarding and CIP check each value the AI read against the package (onboarding the firm and Form ADV facts, CIP the
 *  identity facts): accept it, edit it, or reject it. Until every value is reviewed the case waits, and the rules don't
 *  run on AI values (app/ai_suggestions.py). */
function AiReview({ d, onDone, onFail, readOnly }: { d: CaseDetail; onDone: (c: CaseDetail, msg: string) => void;
  onFail: (e: Error) => void; readOnly?: boolean }) {
  const list = d.ai_suggestions;
  const left = list.filter((s) => s.status === "pending").length;
  const leftFor = (team: string) => list.filter((s) => s.status === "pending" && s.team === team).length;
  const canReview = !readOnly && list.some((s) => s.actions.length > 0);
  const [open, setOpen] = useState<{ id: number; action: "edit" | "reject" } | null>(null);
  const [value, setValue] = useState<any>("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const send = (s: AiSuggestion, action: "accept" | "edit" | "reject", extra: object = {}) => {
    setBusy(true);
    api<CaseDetail>(`/api/cases/${d.id}/ai-suggestions/${s.id}`, { body: { action, ...extra } })
      .then((c) => {
        setOpen(null); setReason("");
        const n = c.ai_suggestions.filter((x) => x.status === "pending").length;
        onDone(c, n ? `${OUTCOME[`${action}ed`][0]}. ${n} left to review.` :
          "Every value is reviewed. The rules ran on the values you confirmed.");
      })
      .catch(onFail).finally(() => setBusy(false));
  };
  const start = (s: AiSuggestion, action: "edit" | "reject") => {
    setOpen({ id: s.id, action }); setReason("");
    setValue(s.kind === "person" ? { ...s.suggested } : s.kind === "boolean"
      ? (["true", "yes"].includes(String(s.suggested.value).toLowerCase()) ? "false" : "true") : s.suggested.value);
  };
  const editor = (s: AiSuggestion) => {
    if (s.kind === "person") return (
      <div className="row">
        <div className="field"><label htmlFor={`pn-${s.id}`}>Name</label>
          <input id={`pn-${s.id}`} type="text" value={value.name} onChange={(e) => setValue({ ...value, name: e.target.value })} /></div>
        <div className="field"><label htmlFor={`pr-${s.id}`}>Role</label>
          <select id={`pr-${s.id}`} value={value.role} onChange={(e) => setValue({ ...value, role: e.target.value })}>
            {PERSON_ROLES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        <div className="field"><label htmlFor={`pp-${s.id}`}>Ownership %</label>
          <input id={`pp-${s.id}`} type="number" min={0} max={100} step="any" value={value.ownership_pct}
            onChange={(e) => setValue({ ...value, ownership_pct: e.target.value })} /></div>
      </div>);
    const id = `ev-${s.id}`;
    return (
      <div className="field"><label htmlFor={id}>What the package says</label>
        {s.choices ? <select id={id} value={value} onChange={(e) => setValue(e.target.value)}>
          {s.choices.map((c) => <option key={c} value={c}>{c}</option>)}</select>
          : s.kind === "boolean" ? <select id={id} value={value} onChange={(e) => setValue(e.target.value)}>
            <option value="true">Yes</option><option value="false">No</option></select>
          : <input id={id} type={s.kind === "date" ? "date" : "text"} value={value} onChange={(e) => setValue(e.target.value)} />}
      </div>);
  };
  return (
    <div className={readOnly ? "" : "decide"}>
      {!readOnly && <>
        <h3>Review the AI&apos;s suggestions</h3>
        <p className="small">The AI read these values from the package, and the controls gate found each one&apos;s quote in it.
          Onboarding checks the firm and Form ADV facts; CIP checks the identity facts (name, address, EIN, owners and control
          persons, formation and ownership documents). Accept, edit, or reject each value. Nothing else runs on this case until
          every value is reviewed; then the rules run on the confirmed values, and a licensed principal accepts at the end.</p>
        <p className="small"><strong>{list.length - left} of {list.length} reviewed</strong> · onboarding: {leftFor("onboarding")} left ·
          CIP: {leftFor("cip")} left</p>
        <DocLinks docs={d.documents.filter((x) => x.doc_type === "application_package")} title="The package the AI read"
          empty="The package isn't attached to this case." />
        {d.extra?.extraction?.mode !== "live" && <p className="muted small">Illustrative output: reviews here are practice and
          don&apos;t count toward the AI suggestion acceptance metric, which measures live AI only.</p>}
        {!canReview && <p className="muted small">Onboarding and CIP review these. Switch to Jamie Ortiz (onboarding) or Taylor
          Brooks (CIP) to review.</p>}
      </>}
      <div className="scroll"><table className="review">
        <thead><tr><th>Value</th><th>The AI read</th><th>Quote from the package</th><th>Review</th></tr></thead>
        <tbody>{list.map((s) => (
          <Fragment key={s.id}>
            <tr>
              <td className="small"><strong>{s.label}</strong><div className="muted">{s.team_label}</div></td>
              <td data-label="The AI read">{s.status === "edited" ? <><s className="muted">{s.suggested_text}</s> {s.final_text}</> :
                s.status === "rejected" ? <s className="muted">{s.suggested_text}</s> : s.suggested_text}</td>
              <td className="small" data-label="Quote from the package">“{s.quote}”</td>
              <td className="small" data-label="Review">
                {s.status === "pending" && canReview && s.actions.length > 0 && open?.id !== s.id ? (
                  <div className="actions">
                    <button className="small" disabled={busy} onClick={() => send(s, "accept")}>Accept</button>
                    <button className="secondary small" disabled={busy} onClick={() => start(s, "edit")}>Edit</button>
                    {s.actions.includes("reject") && <button className="secondary small" disabled={busy} onClick={() => start(s, "reject")}>Reject</button>}
                  </div>
                ) : <>
                  <span className={`pill t-${OUTCOME[s.status][1]}`}>{OUTCOME[s.status][0]}</span>
                  {s.reviewed_by && <div className="muted">{s.reviewed_by}, {when(s.reviewed_at)}</div>}
                  {s.reason && <div>“{s.reason}”</div>}
                </>}
              </td>
            </tr>
            {open?.id === s.id && (
              <tr className="detail"><td colSpan={4}><div className="narrow">
                {open.action === "edit" ? editor(s) : <p className="small">Rejecting leaves this value empty, so the rules treat the item as missing.</p>}
                <div className="field"><label htmlFor={`rs-${s.id}`}>{open.action === "edit" ? "Why (what the package shows)" : "Why it's wrong"}</label>
                  <input id={`rs-${s.id}`} type="text" value={reason} onChange={(e) => setReason(e.target.value)} /></div>
                <div className="actions">
                  <button disabled={busy || reason.trim().length < 10}
                    onClick={() => send(s, open.action, open.action === "edit" ? { value, reason } : { reason })}>
                    {open.action === "edit" ? "Save the edit" : "Reject the value"}</button>
                  <button className="secondary" onClick={() => setOpen(null)}>Cancel</button>
                  {reason.trim().length < 10 && <span className="muted small">A sentence is enough.</span>}
                </div>
              </div></td></tr>)}
          </Fragment>))}
        </tbody>
      </table></div>
    </div>
  );
}

const ISSUE_WORDS: Record<string, string> = { insufficient_information: "Insufficient information",
  wrong_document: "Wrong type of document", suspected_fraud: "Suspected fraudulent identity" };

/** Why an identity check failed: insufficient information or the wrong document goes back to the RIA to resubmit; a
 *  suspected fraudulent identity goes to AML compliance (app/identity_check.py). */
function IdentityCheck({ d, onDone, onFail }: { d: CaseDetail; onDone: (c: CaseDetail, msg: string) => void; onFail: (e: Error) => void }) {
  const s = d.identity_check!;
  const [open, setOpen] = useState(false);
  const [person, setPerson] = useState(s.people[0] ?? "");
  const [kind, setKind] = useState("insufficient_information");
  const [note, setNote] = useState("");
  const fraud = kind === "suspected_fraud";
  const issue = s.issue;
  const send = () => api<CaseDetail>(`/api/cases/${d.id}/identity-check`, { body: { kind, person, note } })
    .then((c) => { setOpen(false); setNote(""); onDone(c, fraud ? "Sent to AML compliance and held (IDFR)." :
      "Sent back to the RIA to resubmit identity documents."); })
    .catch(onFail);
  if (!s.can_record && !issue) return null;
  return (
    <div className={open ? "accept" : ""} style={open ? undefined : { marginBottom: 12 }}>
      {issue && (
        <p className="small"><strong>Identity check failed for {issue.person}:</strong> {ISSUE_WORDS[issue.kind] ?? issue.kind}
          {" "}({issue.by}). {issue.kind === "suspected_fraud" ? "AML compliance decides; the case is held." :
            issue.status === "open" ? "Waiting for the RIA to resubmit identity documents." :
            "New identity documents arrived; verify again."}</p>
      )}
      {s.can_record && <DocLinks docs={d.documents.filter((x) => x.doc_type === "identity_documents")}
        title="Identity documents" empty="No identity documents attached yet." />}
      {s.can_record && !open && (
        <div className="actions">
          <button className="secondary small" onClick={() => setOpen(true)}>Record a failed identity check</button>
          <span className="muted small">Identity pending for {s.people.join(", ")}.</span>
        </div>
      )}
      {s.can_record && open && (
        <>
          <h3>Record a failed identity check</h3>
          <div className="row">
            <div className="field"><label htmlFor="idp">Person</label>
              <select id="idp" value={person} onChange={(e) => setPerson(e.target.value)}>
                {s.people.map((p) => <option key={p} value={p}>{p}</option>)}</select></div>
            <div className="field"><label htmlFor="idk">Why it failed</label>
              <select id="idk" value={kind} onChange={(e) => setKind(e.target.value)}>
                {Object.entries(s.kinds).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
          </div>
          <div className="field"><label htmlFor="idn">{fraud ? "What you found (for AML compliance only)" : "What the RIA needs to send (goes to the RIA)"}</label>
            <input id="idn" type="text" value={note} onChange={(e) => setNote(e.target.value)} /></div>
          <p className="small muted">{fraud ? "The case is held (IDFR) and goes to AML compliance, which confirms the fraud (declined, stays held) or clears it (verification continues). Nothing is said to the RIA or the client." :
            "The request goes to the RIA, and the identity documents waiting to be checked are marked not in good order. New identity documents start verification again."}</p>
          <div className="actions">
            <button className={fraud ? "danger" : ""} disabled={note.trim().length < 10 || !person} onClick={send}>
              {fraud ? "Send to AML compliance" : "Send back to the RIA"}</button>
            <button className="secondary" onClick={() => setOpen(false)}>Cancel</button>
          </div>
        </>
      )}
    </div>
  );
}

function Acceptance({ d, onDone, onFail }: { d: CaseDetail; onDone: (c: CaseDetail, msg: string) => void; onFail: (e: Error) => void }) {
  const [note, setNote] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const outstanding: string[] = d.result?.requested_items ?? [];
  const a = d.acceptance!;
  const r = d.result ?? {};
  // The principal reviews every document operations found in good order (Shawn, Oct. 23, 2026): each has a link and a
  // Reviewed tick, and Accept stays off until all are ticked. One that needs another look goes back to operations.
  const toReview = d.documents.filter((x) => a.to_review.includes(x.id));
  const others = d.documents.filter((x) => !a.to_review.includes(x.id));
  const [ticked, setTicked] = useState<Set<number>>(new Set());
  const [flagged, setFlagged] = useState<Set<number>>(new Set());
  const [opened, setOpened] = useState<Set<number>>(new Set(a.opened));
  const [why, setWhy] = useState("");
  const toggle = (set: Set<number>, id: number, on: boolean) => { const n = new Set(set); if (on) n.add(id); else n.delete(id); return n; };
  const allTicked = toReview.every((x) => ticked.has(x.id));
  const send = (value: "accepted" | "declined") =>
    api<CaseDetail>(`/api/cases/${d.id}/acceptance`, { body: { value, note: note || null, reviewed,
      documents_reviewed: [...ticked] } })
      .then((c) => onDone(c, value === "accepted" ? (outstanding.length ? "Accepted. The account opened, restricted from trading until the outstanding items arrive." : "Accepted. The case ran again and opened.") : "Declined."))
      .catch(onFail);
  const sendBack = () => api<CaseDetail>(`/api/cases/${d.id}/send-back`, { body: { reason: why, documents: [...flagged] } })
    .then((c) => onDone(c, "Sent back to operations. The case is in rework and comes back to you when they're done."))
    .catch(onFail);
  return (
    <div className="accept">
      <h3>Principal acceptance (FINRA Rule 4512)</h3>
      <p className="small">Every check is complete. A registered principal on the RIA service team accepts the
        {d.kind === "firm" ? " RIA relationship and its master account" : " account"} before it opens.</p>
      <ul className="small">
        {r.path && <li>Due diligence path: {r.path === "A" ? "A (relies on the RIA's CIP)" : "B (the custodian carries CIP and CDD)"}</li>}
        {r.risk && <li>Risk: {r.risk.tier} ({r.risk.score} points); next review {r.risk.next_review}</li>}
        <li>Restriction codes: {d.restrictions.filter((x) => x.active).map((x) => x.code).join(", ") || "none"}</li>
        <li>Prepared without manual steps: {d.manual_steps === 0 ? "yes" : `no (${d.manual_steps})`}</li>
      </ul>
      {outstanding.length > 0 && (
        <p className="small"><strong>Opens restricted from trading and fee deduction until these non-regulatory items arrive:</strong> {outstanding.join("; ")}.
          Every regulatory requirement (KYC, CDD, EDD, CIP, AML) is met.</p>
      )}
      <h4>Documents to review ({a.can_accept ? `${toReview.filter((x) => ticked.has(x.id)).length} of ${toReview.length} reviewed` : `${toReview.length} in good order`})</h4>
      {toReview.length === 0 ? <p className="muted small">No documents in good order on this case.</p> : (
        <div className="scroll"><table className="review-docs">
          <thead><tr><th>Document</th><th>Checked by</th>{a.can_accept && <th>Your review</th>}</tr></thead>
          <tbody>{toReview.map((x) => (
            <tr key={x.id}>
              <td><a href={`/api/documents/${x.id}/file`} target="_blank" rel="noreferrer"
                onClick={() => setOpened(toggle(opened, x.id, true))}>{x.type_label}</a>
                <div className="muted small">{x.filename}{opened.has(x.id) ? " · opened" : ""}</div></td>
              <td className="small">{x.reviewed_by ?? "Automated pre-checks"}{x.reviewed_at ? `, ${when(x.reviewed_at)}` : ""}
                {x.review_note && <div>“{x.review_note}”</div>}</td>
              {a.can_accept && (
                <td className="ticks"><label className="check"><input type="checkbox" checked={ticked.has(x.id)} disabled={flagged.has(x.id)}
                  onChange={(e) => setTicked(toggle(ticked, x.id, e.target.checked))} /> Reviewed</label>
                  <label className="check"><input type="checkbox" checked={flagged.has(x.id)}
                    onChange={(e) => { setFlagged(toggle(flagged, x.id, e.target.checked)); if (e.target.checked) setTicked(toggle(ticked, x.id, false)); }} /> Needs another look</label></td>
              )}
            </tr>))}</tbody>
        </table></div>
      )}
      <DocLinks docs={others} title="Other documents on the case (not in good order, superseded, or not yet checked)" />
      {a.can_accept ? (
        flagged.size > 0 ? (
          <>
            <div className="field"><label htmlFor="sb-why">What operations should look at again</label>
              <input id="sb-why" type="text" value={why} onChange={(e) => setWhy(e.target.value)} /></div>
            <p className="small muted">The case goes to onboarding&apos;s rework queue with the {flagged.size === 1 ? "document" : `${flagged.size} documents`} you
              marked. Operations reopens and replaces a document or says why it stands, then sends the case back to you. The RIA
              isn&apos;t told.</p>
            <div className="actions">
              <button disabled={why.trim().length < 10} onClick={sendBack}>Send back to operations</button>
              <button className="secondary" onClick={() => setFlagged(new Set())}>Cancel</button>
            </div>
          </>
        ) : (
          <>
            <div className="field"><label htmlFor="acc-note">Note (optional)</label>
              <input id="acc-note" type="text" value={note} onChange={(e) => setNote(e.target.value)} /></div>
            <label className="check"><input type="checkbox" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} />
              I reviewed the documents, rule findings, and risk for this {d.kind === "firm" ? "relationship" : "account"}</label>
            <p className="small">You sign as <strong>{a.signs_as}</strong>.</p>
            <div className="actions">
              <button disabled={!reviewed || !allTicked} onClick={() => send("accepted")}>Accept</button>
              <button className="secondary" onClick={() => send("declined")}>Decline</button>
              {!allTicked && <span className="muted small">Tick each document as reviewed to accept.</span>}
            </div>
          </>
        )
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
      <DocLinks docs={d.documents} title="Documents on this case" />
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
  const flagged = new Set((w.documents ?? []).map((x) => x.id));
  return (
    <div className="decide">
      <h3>{w.kind === "send_back" ? `Sent back by the principal (${w.reference})` : `Rework after a rescission (${w.reference})`}</h3>
      <p>{w.reason}</p>
      {w.kind === "send_back" ? (
        <>
          <DocLinks docs={d.documents.filter((x) => flagged.has(x.id))} title="Look again at" />
          <p className="muted small">Sent back by {w.requested_by} before accepting, {when(w.at)}. If a document isn't right,
            reopen it in the Documents tab and attach the replacement; if it is, say why. Then send the case back to the principal.</p>
        </>
      ) : (
        <p className="muted small">Asked by {w.requested_by}, approved by {w.approved_by}, {when(w.at)}. Reopen any document that isn't
          right in the Documents tab, attach the replacement, then send the case back to the principal.</p>
      )}
      <DocLinks docs={d.documents.filter((x) => !flagged.has(x.id))} title={flagged.size ? "Other documents" : "Documents on this case"} />
      {ops ? (
        <>
          <div className="field"><label htmlFor="rw">{w.kind === "send_back" ? "What you fixed, or why it stands" : "What you fixed"}</label>
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
  // A value per person (phones, tenants-in-common shares), keyed field -> name -> value
  const [perPerson, setPerPerson] = useState<Record<string, Record<string, string>>>(() => Object.fromEntries(
    d.corrections.filter((c) => c.type === "per_person").map((c) => [c.field,
      Object.fromEntries((c.people ?? []).map((p) => [p.name, String(p.value ?? "")]))])));
  type Ben = { name: string; relationship: string; kind: string; share: number };
  const [bens, setBens] = useState<Ben[]>(() => {
    const c = d.corrections.find((x) => x.type === "beneficiaries");
    return ((c?.current as Ben[] | undefined) ?? []).map((b) => ({ ...b, relationship: b.relationship ?? "" }));
  });
  const setBen = (i: number, patch: Partial<Ben>) => setBens(bens.map((b, j) => (j === i ? { ...b, ...patch } : b)));
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
      if (c.type === "choice" && values[c.field]) fields[c.field] = values[c.field];
      if (c.type === "per_person" && Object.values(perPerson[c.field] ?? {}).some((v) => v.trim())) fields[c.field] = perPerson[c.field];
      if (c.type === "beneficiaries" && bens.some((b) => b.name.trim())) fields.beneficiaries = bens.filter((b) => b.name.trim());
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
          {c.type === "choice" && <><label htmlFor={c.field}>{c.label}</label>
            <select id={c.field} value={values[c.field] ?? ""} onChange={(e) => setValues({ ...values, [c.field]: e.target.value })}>
              <option value="">Choose…</option>
              {(c.options ?? []).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select></>}
          {c.type === "per_person" && <><strong>{c.label}</strong>
            <div className="small muted">{c.reason}</div>
            <div className="row">
              {(c.people ?? []).map((p, i) => (
                <div className="field" key={p.name}><label htmlFor={`${c.field}-${i}`}>{p.name}</label>
                  <input id={`${c.field}-${i}`} type={c.field === "holder_shares" ? "number" : "tel"} value={perPerson[c.field]?.[p.name] ?? ""}
                    onChange={(e) => setPerPerson({ ...perPerson, [c.field]: { ...perPerson[c.field], [p.name]: e.target.value } })} /></div>
              ))}
            </div></>}
          {c.type === "beneficiaries" && <><strong>{c.label}</strong>
            <div className="small muted">{c.reason}. Each kind (primary, contingent) adds up to 100%.</div>
            {bens.map((b, i) => (
              <div className="row" key={i}>
                <div className="field"><label htmlFor={`ben-n${i}`}>Name</label>
                  <input id={`ben-n${i}`} type="text" value={b.name} onChange={(e) => setBen(i, { name: e.target.value })} /></div>
                <div className="field" style={{ flex: "0 1 150px" }}><label htmlFor={`ben-k${i}`}>Kind</label>
                  <select id={`ben-k${i}`} value={b.kind} onChange={(e) => setBen(i, { kind: e.target.value })}>
                    <option value="primary">Primary</option><option value="contingent">Contingent</option></select></div>
                <div className="field" style={{ flex: "0 1 110px" }}><label htmlFor={`ben-s${i}`}>Share %</label>
                  <input id={`ben-s${i}`} type="number" min={0} max={100} value={b.share} onChange={(e) => setBen(i, { share: Number(e.target.value) })} /></div>
                <button type="button" className="secondary small" onClick={() => setBens(bens.filter((_, j) => j !== i))}>Remove</button>
              </div>
            ))}
            <button type="button" className="secondary small" onClick={() => setBens([...bens, { name: "", relationship: "", kind: "primary", share: 0 }])}>Add a beneficiary</button></>}
          <span className="muted small mono">{c.rule_id}</span>
        </div>
      ))}
      {canSubmit && <button onClick={submit}>Record items and re-run</button>}
    </div>
  );
}


/** Who is working the case: claim it, release it, or (a team lead) hand it to someone in the queue. */
function Owner({ d, onDone, onFail }: { d: CaseDetail; onDone: (c: CaseDetail, ok: string) => void; onFail: (e: Error) => void }) {
  const { user } = useAuth();
  const own = d.ownership;
  const [people, setPeople] = useState<{ id: number; name: string; open_cases: number }[] | null>(null);
  const post = (path: string, body: object, ok: string) =>
    api<CaseDetail>(`/api/cases/${d.id}/${path}`, { body }).then((c) => onDone(c, ok)).catch(onFail);
  const mine = d.assigned_to?.id === user.id;
  return (
    <span className="row small" style={{ alignItems: "center", gap: 6 }}>
      <span className="muted">Owner:</span>
      <strong>{d.assigned_to ? (mine ? "You" : d.assigned_to.name) : "Unassigned"}</strong>
      {own?.can_claim && !d.assigned_to && <button className="small secondary" onClick={() => post("claim", {}, "It's yours.")}>Claim</button>}
      {own?.can_release && <button className="small secondary" onClick={() => post("assign", { user_id: null }, "Back in the queue, unassigned.")}>Release</button>}
      {own?.can_assign && (people === null
        ? <button className="small secondary" onClick={() => api<{ id: number; name: string; open_cases: number }[]>(`/api/cases/${d.id}/assignees`).then(setPeople).catch(onFail)}>Assign…</button>
        : <select aria-label="Assign to" defaultValue="" onChange={(e) => e.target.value && post("assign", { user_id: Number(e.target.value) }, "Assigned.")}>
            <option value="">Assign to…</option>
            {people.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.open_cases} open)</option>)}
          </select>)}
    </span>
  );
}


const QUEUE_CHANNEL: Record<string, string> = { items_requested: "onboarding", rework: "onboarding", verification: "cip",
  confirmation: "client-service", maintenance_items: "client-service", edd_review: "aml-compliance", compliance_review: "aml-compliance",
  sanctions_review: "sanctions", principal_acceptance: "ria-service" };

function Discussion({ caseId, queue }: { caseId: number; queue: string | null }) {
  const about = useLoad(() => api<Message[]>(`/api/messages/about?case_id=${caseId}`), [caseId]);
  const channel = QUEUE_CHANNEL[queue ?? ""] ?? "general";
  return (
    <>
      <p className="muted small">Messages in the team channels that reference this case.{" "}
        <Link to={`/messages?c=${channel}&draft=${encodeURIComponent(`Case #${caseId}: `)}`}>Discuss it in #{channel}</Link></p>
      {!about.data ? <Loading /> : about.data.length ? about.data.map((m) => (
        <article key={m.id} className="msg"><div className="msg-head"><strong>{m.author}</strong> in{" "}
          <Link to={`/messages?c=${m.channel}`}>#{m.channel_name}</Link> <span className="muted small">{when(m.created_at)}</span></div>
          <MessageBody m={m} /></article>)) : <div className="empty">No one has discussed this case yet.</div>}
    </>
  );
}
