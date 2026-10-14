import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Loading, useLoad, when } from "../util";

type Event = {
  id: number; ref: string; category: string; risk_type: string; risk_type_label: string; source: string; source_label: string;
  case_id: number | null; case_title: string | null; target_id: string | null;
  summary: string; detected_by: string; approved_by: string | null; owner: string; owner_label: string; status: string; status_label: string;
  remediation: string | null; remediation_label: string | null; remediation_note: string | null; created_at: string;
  updated_by: string | null; closed_at: string | null; can_update: boolean;
};
type Register = { events: Event[]; remediation: Record<string, string>; risk_types: Record<string, string>; owners: Record<string, string> };

const TYPE_TONE: Record<string, string> = { operational: "t-refer", regulatory: "t-escalate", financial: "t-block" };

export default function RiskEvents() {
  const [type, setType] = useState("");
  const [status, setStatus] = useState("open");
  const data = useLoad(() => api<Register>(`/api/risk-events?status=${status}${type ? `&risk_type=${type}` : ""}`), [type, status]);
  const all = useLoad(() => api<Register>("/api/risk-events"), []);
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [reporting, setReporting] = useState(false);
  const reload = () => { data.reload(); all.reload(); };
  const save = (e: Event, form: { remediation: string; note: string }, close: boolean) =>
    api(`/api/risk-events/${e.id}`, { body: { ...form, close } })
      .then(() => { setMsg({ ok: close ? `${e.ref} closed.` : `${e.ref} updated.` }); reload(); })
      .catch((err: Error) => setMsg({ error: err.message }));
  const counts = (all.data?.events ?? []).filter((e) => e.status !== "closed").reduce<Record<string, number>>(
    (n, e) => ({ ...n, [e.risk_type]: (n[e.risk_type] ?? 0) + 1 }), {});

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <h1 style={{ margin: 0 }}>Risk events</h1>
        <button className="secondary" onClick={() => setReporting(!reporting)}>{reporting ? "Cancel" : "Report a risk event"}</button>
      </div>
      <p className="muted">Operational, regulatory, and financial events. The platform logs rescission requests, documents reopened on an
        open account, sanctions true matches, and cases past their SLA; anyone can report one. The owning team records the
        remediation path and closes the event.</p>
      {msg.ok && <p className="ok">{msg.ok}</p>}{msg.error && <p className="error">{msg.error}</p>}
      {reporting && all.data && <ReportForm reg={all.data} onDone={(ref) => { setReporting(false); setMsg({ ok: `${ref} reported.` }); reload(); }}
        onError={(m) => setMsg({ error: m })} />}

      <div className="grid tiles" style={{ margin: "12px 0" }}>
        {Object.entries(all.data?.risk_types ?? {}).map(([k, label]) => (
          <button key={k} className={`tile ${type === k ? "selected" : ""}`} onClick={() => setType(type === k ? "" : k)}>
            <div className="label">{label}</div><div className="value">{counts[k] ?? 0}</div><div className="sub">open</div>
          </button>))}
      </div>
      <div className="row" style={{ alignItems: "center", marginBottom: 8 }}>
        <div className="field" style={{ flex: "0 1 200px" }}><label htmlFor="rst">Show</label>
          <select id="rst" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="open">Open and in remediation</option><option value="closed">Closed</option><option value="">All</option>
          </select></div>
        {type && <button className="small secondary" onClick={() => setType("")}>All types</button>}
      </div>

      {!data.data ? <Loading /> : data.data.events.length === 0 ? <div className="empty">No events here.</div> : data.data.events.map((e) => (
        <div key={e.id} className="panel">
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <h3 style={{ margin: 0 }}>{e.ref} · {e.category}</h3>
            <span>
              <span className={`pill ${TYPE_TONE[e.risk_type] ?? "t-quiet"}`}>{e.risk_type_label}</span>{" "}
              <span className={`pill ${e.status === "closed" ? "t-clear" : e.status === "open" ? "t-escalate" : "t-refer"}`}>{e.status_label}</span>
            </span>
          </div>
          <p>{e.summary}</p>
          <p className="muted small">
            {e.case_id ? <><Link to={`/cases/${e.case_id}`}>#{e.case_id} {e.case_title}</Link> · </> : e.target_id ? <><span className="mono">{e.target_id}</span> · </> : null}
            {e.source_label} · found by {e.detected_by}{e.approved_by ? `, approved by ${e.approved_by}` : ""} · {when(e.created_at)} · owner: {e.owner_label}
          </p>
          {e.remediation && <p className="small"><strong>{e.remediation_label}:</strong> {e.remediation_note} <span className="muted">({e.updated_by}{e.closed_at ? `, closed ${when(e.closed_at)}` : ""})</span></p>}
          {e.can_update && <Remediate e={e} options={data.data!.remediation} onSave={save} />}
        </div>
      ))}
    </>
  );
}

function ReportForm({ reg, onDone, onError }: { reg: Register; onDone: (ref: string) => void; onError: (m: string) => void }) {
  const [f, setF] = useState({ category: "", risk_type: "operational", owner: "ops_supervisor", summary: "", case_id: "", target_id: "" });
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    api<Event>("/api/risk-events", { body: { ...f, case_id: f.case_id ? Number(f.case_id) : null, target_id: f.target_id || null } })
      .then((ev) => onDone(ev.ref)).catch((err: Error) => onError(err.message));
  };
  return (
    <form className="panel" onSubmit={submit}>
      <h2 style={{ marginTop: 0 }}>Report a risk event</h2>
      <div className="row">
        <div className="field"><label htmlFor="rc">What happened (short title)</label><input id="rc" type="text" maxLength={60} value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })} /></div>
        <div className="field" style={{ flex: "0 1 180px" }}><label htmlFor="rt">Type</label>
          <select id="rt" value={f.risk_type} onChange={(e) => setF({ ...f, risk_type: e.target.value })}>
            {Object.entries(reg.risk_types).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        <div className="field" style={{ flex: "0 1 220px" }}><label htmlFor="ro">Team that owns it</label>
          <select id="ro" value={f.owner} onChange={(e) => setF({ ...f, owner: e.target.value })}>
            {Object.entries(reg.owners).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
      </div>
      <div className="field"><label htmlFor="rsum">Details</label><textarea id="rsum" value={f.summary} onChange={(e) => setF({ ...f, summary: e.target.value })} /></div>
      <div className="row">
        <div className="field" style={{ flex: "0 1 160px" }}><label htmlFor="rcase">Case number (optional)</label><input id="rcase" type="number" value={f.case_id} onChange={(e) => setF({ ...f, case_id: e.target.value })} /></div>
        <div className="field" style={{ flex: "0 1 220px" }}><label htmlFor="rtg">Account or firm id (optional)</label><input id="rtg" type="text" value={f.target_id} onChange={(e) => setF({ ...f, target_id: e.target.value })} /></div>
      </div>
      <button type="submit" disabled={f.category.trim().length < 3 || f.summary.trim().length < 10}>Report</button>
    </form>
  );
}

function Remediate({ e, options, onSave }: { e: Event; options: Record<string, string>;
  onSave: (e: Event, f: { remediation: string; note: string }, close: boolean) => void }) {
  const [form, setForm] = useState({ remediation: e.remediation ?? (e.risk_type === "regulatory" ? "report" : "training"), note: e.remediation_note ?? "" });
  return (
    <div className="row">
      <div className="field" style={{ flex: "0 1 260px" }}><label htmlFor={`rm-${e.id}`}>Remediation path</label>
        <select id={`rm-${e.id}`} value={form.remediation} onChange={(x) => setForm({ ...form, remediation: x.target.value })}>
          {Object.entries(options).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select></div>
      <div className="field"><label htmlFor={`rn-${e.id}`}>What will change</label>
        <input id={`rn-${e.id}`} type="text" value={form.note} onChange={(x) => setForm({ ...form, note: x.target.value })} /></div>
      <div className="actions" style={{ marginTop: 0, marginBottom: 12 }}>
        <button className="secondary small" disabled={form.note.trim().length < 10} onClick={() => onSave(e, form, false)}>Save</button>
        <button className="small" disabled={form.note.trim().length < 10} onClick={() => onSave(e, form, true)}>Save and close</button>
      </div>
    </div>
  );
}
