import { useEffect, useState } from "react";
import { api, type Doc, type DocRef } from "./api";
import { useAuth } from "./auth";
import { when } from "./util";

type DocTypes = { types: { value: string; label: string }[]; channels: { value: string; label: string }[];
  max_bytes: number; samples: boolean };

let cached: Promise<DocTypes> | null = null;
export function useDocTypes() {
  const [data, setData] = useState<DocTypes | null>(null);
  useEffect(() => {
    cached = cached ?? api<DocTypes>("/api/documents/types");
    cached.then(setData).catch(() => { cached = null; });
  }, []);
  return data;
}

export const TAX_FORMS: [string, string][] = [["w9", "W-9 (US person)"], ["w8ben", "W-8BEN (foreign individual)"],
  ["w8bene", "W-8BEN-E (foreign entity)"]];

export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = () => reject(new Error("Couldn't read the file"));
    reader.readAsDataURL(file);
  });
}

const STATUS_LABEL: Record<Doc["status"], [string, string]> = {
  received: ["Received, not yet checked", "refer"], in_good_order: ["In good order", "clear"],
  not_in_good_order: ["Not in good order", "block"],
  superseded: ["Superseded by another copy", "quiet"],
};
const MARK: Record<string, string> = { pass: "✓", fail: "✗", manual: "?" };

/** Direct links to the documents behind a decision, each opening in a new tab, with where its check stands. */
export function DocLinks({ docs, title = "Documents", empty }: { docs: DocRef[]; title?: string; empty?: string }) {
  if (!docs.length) return empty ? <p className="muted small">{empty}</p> : null;
  return (
    <div className="doclinks small">{title && <strong>{title}</strong>}
      <ul>{docs.map((d) => (
        <li key={d.id}><a href={`/api/documents/${d.id}/file`} target="_blank" rel="noreferrer" title={d.filename}>{d.type_label}</a>
          <span className="muted"> · {STATUS_LABEL[d.status][0].toLowerCase()}</span></li>))}</ul>
    </div>
  );
}

export function DocStatus({ d }: { d: Doc }) {
  const [label, tone] = STATUS_LABEL[d.status];
  return <span className={`pill t-${tone}`}>{label}</span>;
}

export function Checks({ d }: { d: Doc }) {
  return (
    <ul className="checks">
      {d.checks.map((c) => <li key={c.check} className={`chk ${c.result}`} title={c.detail}>
        <span aria-hidden="true">{MARK[c.result]}</span> {c.check}{c.result !== "pass" ? `: ${c.detail}` : ""}</li>)}
    </ul>
  );
}

/** Upload a file, or attach a synthetic sample in the demo. */
export function DocUpload({ caseId, types, onUploaded, compact }: {
  caseId: number; types: { value: string; label: string }[]; onUploaded: (d: Doc) => void; compact?: boolean;
}) {
  const { user } = useAuth();
  const meta = useDocTypes();
  const ops = ["ops_analyst", "ops_supervisor"].includes(user.role);
  const [docType, setDocType] = useState(types[0]?.value ?? "other");
  const [channel, setChannel] = useState("email");
  const [received, setReceived] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!types.some((t) => t.value === docType)) setDocType(types[0]?.value ?? "other"); }, [types, docType]);

  const send = (sample: boolean) => {
    const fd = new FormData();
    fd.append("doc_type", docType);
    fd.append("channel", ops ? channel : "portal");
    if (received) fd.append("received_on", received);
    if (sample) fd.append("sample", "true");
    else if (file) fd.append("file", file);
    setBusy(true); setError(null);
    api<Doc>(`/api/cases/${caseId}/documents`, { form: fd })
      .then((d) => { setFile(null); onUploaded(d); })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <div className={compact ? "upload compact" : "upload"}>
      <div className="row">
        {types.length > 1 && (
          <div className="field"><label htmlFor={`dt-${caseId}-${types[0].value}`}>Document</label>
            <select id={`dt-${caseId}-${types[0].value}`} value={docType} onChange={(e) => setDocType(e.target.value)}>
              {types.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select></div>
        )}
        {ops && !compact && (
          <>
            <div className="field" style={{ flex: "0 1 150px" }}><label htmlFor={`ch-${caseId}`}>Came in by</label>
              <select id={`ch-${caseId}`} value={channel} onChange={(e) => setChannel(e.target.value)}>
                {(meta?.channels ?? []).map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select></div>
            <div className="field" style={{ flex: "0 1 170px" }}><label htmlFor={`rc-${caseId}`}>Received on</label>
              <input id={`rc-${caseId}`} type="date" value={received} onChange={(e) => setReceived(e.target.value)} /></div>
          </>
        )}
        <div className="field"><label htmlFor={`f-${caseId}-${types[0]?.value}`}>File (PDF, PNG, or JPEG, up to 5 MB)</label>
          <input id={`f-${caseId}-${types[0]?.value}`} type="file" accept=".pdf,.png,.jpg,.jpeg"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></div>
      </div>
      <div className="actions" style={{ marginTop: 0 }}>
        <button type="button" className="small" disabled={busy || !file} onClick={() => send(false)}>Upload</button>
        {meta?.samples && <button type="button" className="small secondary" disabled={busy} onClick={() => send(true)}>Attach a synthetic sample</button>}
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}

export function DocList({ docs, onChanged }: { docs: Doc[]; onChanged: () => void }) {
  const { user } = useAuth();
  const ops = ["ops_analyst", "ops_supervisor"].includes(user.role);
  const portal = ["ria_admin", "ria_user", "client"].includes(user.role);   // no automated-check detail in the portal
  const reject = (d: Doc) => {
    const reason = window.prompt(d.status === "in_good_order"
      ? `Reopen ${d.filename}? It was accepted; the item it satisfied comes back as outstanding. On an open account, ` +
        "the account stays open but restricted from trading, and an operational risk event is logged. Why isn't it in good order?"
      : `Why is ${d.filename} not in good order?`);
    if (!reason || reason.trim().length < 5) return;
    api(`/api/documents/${d.id}/reject`, { body: { reason } }).then(onChanged).catch((e: Error) => alert(e.message));
  };
  if (!docs.length) return <div className="empty">No documents on this case yet.</div>;
  return (
    <div className="scroll"><table>
      <thead><tr><th>Document</th><th>Received</th><th>Status</th>{!portal && <th>Automated checks</th>}<th>Fingerprint (SHA-256)</th>{!portal && <th></th>}</tr></thead>
      <tbody>{docs.map((d) => (
        <tr key={d.id}>
          <td><strong>{d.type_label}</strong><br />
            <a href={`/api/documents/${d.id}/file`} target="_blank" rel="noreferrer">{d.filename}</a>
            {d.synthetic && <span className="muted small"> · synthetic sample</span>}</td>
          <td className="small">{d.received_on} via {d.channel_label}<br /><span className="muted">added by {d.uploaded_by}</span></td>
          <td><DocStatus d={d} />{d.reviewed_by && <div className="muted small">{d.reviewed_by}, {when(d.reviewed_at)}</div>}
            {d.review_note && <div className="small">“{d.review_note}”</div>}</td>
          {!portal && <td className="small"><Checks d={d} /></td>}
          <td className="mono small" title={d.sha256}>{d.sha256.slice(0, 16)}…</td>
          {!portal && <td>{ops && d.status === "received" && <button className="small secondary" onClick={() => reject(d)}>Not in good order</button>}
            {d.status === "in_good_order" && d.reopen_refusal === null && <button className="small secondary" onClick={() => reject(d)}>Reopen</button>}
            {d.status === "in_good_order" && d.reopen_refusal?.startsWith("This document backs") && <span className="muted small">Regulatory item: rescind the acceptance to reopen</span>}</td>}
        </tr>
      ))}</tbody>
    </table></div>
  );
}

/** At intake: attach a file or a synthetic sample for each document an application needs. */
export type DocChoice = { sample: true } | { filename: string; content_b64: string } | null;

export function IntakeDocRow({ label, value, onChange, required, children }: {
  label: string; value: DocChoice; onChange: (v: DocChoice) => void; required?: boolean; children?: React.ReactNode;
}) {
  const meta = useDocTypes();
  const [error, setError] = useState<string | null>(null);
  const pick = async (file: File | undefined) => {
    if (!file) return;
    if (meta && file.size > meta.max_bytes) { setError("Keep documents under 5 MB"); return; }
    setError(null);
    onChange({ filename: file.name, content_b64: await fileToBase64(file) });
  };
  const id = label.replace(/\W+/g, "-").toLowerCase();
  return (
    <div className="docrow">
      <div className="docrow-label"><strong>{label}</strong>{required && <span className="pill t-refer" style={{ marginLeft: 6 }}>Required for this change</span>}
        <div className="small muted">{value === null ? "Not attached" : "sample" in value ? "Synthetic sample attached" : `Attached: ${value.filename}`}</div>
        {children}</div>
      <div className="actions" style={{ marginTop: 0 }}>
        <label className="filebtn" htmlFor={`in-${id}`}>Choose file<input id={`in-${id}`} type="file" accept=".pdf,.png,.jpg,.jpeg"
          onChange={(e) => pick(e.target.files?.[0])} /></label>
        {meta?.samples && <button type="button" className="small secondary" onClick={() => onChange({ sample: true })}>Synthetic sample</button>}
        {value !== null && <button type="button" className="small secondary" onClick={() => onChange(null)}>Remove</button>}
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

export function toUploads(entries: [string, DocChoice][]) {
  return entries.filter(([, v]) => v !== null).map(([doc_type, v]) => ({ doc_type, ...(v as object) }));
}
