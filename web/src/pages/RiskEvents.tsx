import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Loading, useLoad, when } from "../util";

type Event = {
  id: number; ref: string; category: string; case_id: number | null; case_title: string | null; target_id: string | null;
  summary: string; detected_by: string; approved_by: string | null; owner_label: string; status: string; status_label: string;
  remediation: string | null; remediation_label: string | null; remediation_note: string | null; created_at: string;
  updated_by: string | null; closed_at: string | null; can_update: boolean;
};

export default function RiskEvents() {
  const data = useLoad(() => api<{ events: Event[]; remediation: Record<string, string> }>("/api/risk-events"), []);
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  if (!data.data) return <Loading />;
  const { events, remediation } = data.data;
  const save = (e: Event, form: { remediation: string; note: string }, close: boolean) =>
    api(`/api/risk-events/${e.id}`, { body: { ...form, close } })
      .then(() => { setMsg({ ok: close ? `${e.ref} closed.` : `${e.ref} updated.` }); data.reload(); })
      .catch((err: Error) => setMsg({ error: err.message }));
  return (
    <>
      <h1>Operational risk events</h1>
      <p className="muted">Errors a control let through, such as an acceptance that had to be rescinded or a document accepted in
        error. The team lead agrees a remediation path (training, a technology change, or a procedure update) and closes the event.</p>
      {msg.ok && <p className="ok">{msg.ok}</p>}{msg.error && <p className="error">{msg.error}</p>}
      {events.length === 0 ? <div className="empty">No operational risk events.</div> : events.map((e) => (
        <div key={e.id} className="panel">
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <h3 style={{ margin: 0 }}>{e.ref} · {e.category}</h3>
            <span className={`pill ${e.status === "closed" ? "t-clear" : e.status === "open" ? "t-escalate" : "t-refer"}`}>{e.status_label}</span>
          </div>
          <p>{e.summary}</p>
          <p className="muted small">
            {e.case_id && <><Link to={`/cases/${e.case_id}`}>#{e.case_id} {e.case_title}</Link> · </>}
            Found by {e.detected_by}{e.approved_by ? `, rescission approved by ${e.approved_by}` : ""} · {when(e.created_at)} · owner: {e.owner_label}
          </p>
          {e.remediation && <p className="small"><strong>{e.remediation_label}:</strong> {e.remediation_note} <span className="muted">({e.updated_by}{e.closed_at ? `, closed ${when(e.closed_at)}` : ""})</span></p>}
          {e.can_update && <Remediate e={e} options={remediation} onSave={save} />}
        </div>
      ))}
    </>
  );
}

function Remediate({ e, options, onSave }: { e: Event; options: Record<string, string>;
  onSave: (e: Event, f: { remediation: string; note: string }, close: boolean) => void }) {
  const [form, setForm] = useState({ remediation: e.remediation ?? "training", note: e.remediation_note ?? "" });
  return (
    <div className="row">
      <div className="field" style={{ flex: "0 1 240px" }}><label htmlFor={`rm-${e.id}`}>Remediation path</label>
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
