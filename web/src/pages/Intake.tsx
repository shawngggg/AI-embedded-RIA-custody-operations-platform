import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type CaseDetail } from "../api";
import { useAuth } from "../auth";
import { useLoad } from "../util";

export default function Intake() {
  const navigate = useNavigate();
  const { user, refreshCounts } = useAuth();
  const ops = ["ops_analyst", "ops_supervisor"].includes(user.role);
  const status = useLoad(() => ops ? api<any>("/api/intake/status") : Promise.resolve(null), []);
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ legal_name: "", registration: "SEC", sec_file_number: "", crd_number: "", raum: "",
    principal_address: "", ein: "", adv_last_annual_amendment: "", control_person: "", owner_pct: "100" });
  const [docs, setDocs] = useState({ formation_documents: true, ownership_chart: true, beneficial_ownership_certification: true,
    custodial_agreement_signed: true, reliance_contract: false });

  const go = (c: CaseDetail) => { refreshCounts(); navigate(`/cases/${c.id}`); };
  const extract = () => {
    const fd = new FormData();
    if (file) fd.append("file", file); else fd.append("text", text);
    setBusy(true); setError(null);
    api<CaseDetail>("/api/intake/extract", { form: fd }).then(go).catch((e) => setError(e.message)).finally(() => setBusy(false));
  };
  const submitForm = (e: React.FormEvent) => {
    e.preventDefault();
    const app = { legal_name: form.legal_name, registration: form.registration, sec_file_number: form.sec_file_number || null,
      crd_number: form.crd_number || null, raum: Number(form.raum || 0), principal_address: form.principal_address || null,
      ein: form.ein || null, adv_last_annual_amendment: form.adv_last_annual_amendment || null, ...docs,
      people: form.control_person ? [{ name: form.control_person, role: "control_person", ownership_pct: Number(form.owner_pct || 0) }] : [] };
    api<CaseDetail>("/api/intake/firm", { body: { application: app } }).then(go).catch((err) => setError(err.message));
  };
  const f = (k: keyof typeof form, label: string, type = "text") => (
    <div className="field"><label htmlFor={k}>{label}</label><input id={k} type={type} value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} /></div>
  );

  if (!ops) return <><h1>New application</h1><p className="muted">Operations takes in new RIA firm applications. Switch to the operations analyst to try it.</p></>;
  return (
    <>
      <h1>New RIA firm application</h1>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="grid two">
        <section className="panel">
          <h2 style={{ marginTop: 0 }}>Read a package with AI</h2>
          <p className="muted small">
            {status.data?.live ? `Live: ${status.data.model}, ${status.data.used_today} of ${status.data.cap} reads used today.`
              : "Live AI is off on this server. The sample package runs through the controls gate with an illustrative, hand-written model response."}
            {" "}A value is accepted only if the model quotes it from the documents.
          </p>
          <div className="field"><label htmlFor="pkg">Paste the package text</label>
            <textarea id="pkg" value={text} onChange={(e) => setText(e.target.value)} placeholder="Form ADV excerpt, formation documents, ownership chart, agreement signature page" /></div>
          <div className="field"><label htmlFor="file">Or upload a PDF or text file</label>
            <input id="file" type="file" accept=".pdf,.txt" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></div>
          <div className="actions">
            <button disabled={busy || (!text.trim() && !file)} onClick={extract}>{busy ? "Reading…" : "Read and open a case"}</button>
            <button className="secondary" onClick={() => { setText(status.data?.sample_text ?? ""); setFile(null); }}>Use the sample package</button>
          </div>
        </section>
        <form className="panel" onSubmit={submitForm}>
          <h2 style={{ marginTop: 0 }}>Or enter it by form</h2>
          {f("legal_name", "Legal name")}
          <div className="row">
            <div className="field"><label htmlFor="registration">Registration</label>
              <select id="registration" value={form.registration} onChange={(e) => setForm({ ...form, registration: e.target.value })}>
                <option value="SEC">SEC</option><option value="state">State</option><option value="none">Not registered</option></select></div>
            {f("crd_number", "CRD number")}
          </div>
          <div className="row">{f("sec_file_number", "SEC file number")}{f("raum", "RAUM (USD)", "number")}</div>
          {f("principal_address", "Principal place of business")}
          <div className="row">{f("ein", "EIN")}{f("adv_last_annual_amendment", "Last ADV annual amendment", "date")}</div>
          <div className="row">{f("control_person", "Control person")}{f("owner_pct", "Their ownership %", "number")}</div>
          {Object.entries(docs).map(([k, v]) => (
            <label key={k} className="check"><input type="checkbox" checked={v} onChange={(e) => setDocs({ ...docs, [k]: e.target.checked })} /> {k.replace(/_/g, " ")}</label>
          ))}
          <button type="submit" disabled={!form.legal_name}>Open a case</button>
        </form>
      </div>
    </>
  );
}
