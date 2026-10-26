import { useState } from "react";
import { api } from "../api";
import { Loading, useLoad } from "../util";

type Incident = { id: number; ref: string; summary: string; status: string; aware_on: string; reported_by: string;
  service_provider: string | null; provider_late: boolean | null; accounts: string[]; information: string | null;
  contained_on: string | null; containment: string | null; determination: string | null; determination_reason: string | null;
  determined_by: string | null; notice_due: string; days_left: number | null; overdue: boolean; notices_sent_on: string | null;
  notices_sent: number };
type View = { incidents: Incident[]; notice_days: number; provider_hours: number; source: string };

/** Regulation S-P incident response: assess, contain, decide on notice, and notify within 30 days. */
export default function Privacy() {
  const v = useLoad(() => api<View>("/api/privacy"), []);
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [form, setForm] = useState({ summary: "", aware_on: new Date().toISOString().slice(0, 10), accounts: "", information: "",
    service_provider: "" });
  const [notes, setNotes] = useState<Record<number, string>>({});
  if (!v.data) return v.error ? <p className="error">{v.error}</p> : <Loading />;
  const act = (path: string, body: object, ok: string) => api<Incident>(path, { body })
    .then(() => { setMsg({ ok }); v.reload(); }).catch((e: Error) => setMsg({ error: e.message }));
  const open = () => act("/api/privacy", { ...form, accounts: form.accounts.split(/[\s,]+/).filter(Boolean),
    service_provider: form.service_provider || null }, "Incident opened; the 30-day notice clock runs from the date you became aware.");

  return (
    <>
      <h1>Data incidents (Regulation S-P)</h1>
      <p className="muted">Assess what happened and whose information was involved, contain it, then decide: notify each affected customer
        as soon as practicable and within {v.data.notice_days} days of becoming aware, or document after a reasonable investigation that
        the information isn't reasonably likely to be used to cause substantial harm or inconvenience. A service provider owes us notice
        within {v.data.provider_hours} hours of its own awareness. ({v.data.source}.)</p>
      {msg.ok && <p className="ok">{msg.ok}</p>}{msg.error && <p className="error">{msg.error}</p>}

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Open an incident</h2>
        <div className="field"><label htmlFor="ps">What happened</label>
          <textarea id="ps" style={{ minHeight: 70, fontFamily: "inherit", fontSize: "0.9rem" }} value={form.summary}
            onChange={(e) => setForm({ ...form, summary: e.target.value })} /></div>
        <div className="row">
          <div className="field" style={{ flex: "0 1 180px" }}><label htmlFor="pa">Became aware on</label>
            <input id="pa" type="date" value={form.aware_on} onChange={(e) => setForm({ ...form, aware_on: e.target.value })} /></div>
          <div className="field"><label htmlFor="pc">Accounts involved (ids)</label>
            <input id="pc" type="text" placeholder="ACC-3005, ACC-3006" value={form.accounts} onChange={(e) => setForm({ ...form, accounts: e.target.value })} /></div>
        </div>
        <div className="row">
          <div className="field"><label htmlFor="pi">What information</label>
            <input id="pi" type="text" placeholder="Names, addresses, and account numbers" value={form.information}
              onChange={(e) => setForm({ ...form, information: e.target.value })} /></div>
          <div className="field"><label htmlFor="pp">Service provider, if it happened there</label>
            <input id="pp" type="text" value={form.service_provider} onChange={(e) => setForm({ ...form, service_provider: e.target.value })} /></div>
        </div>
        <button disabled={form.summary.trim().length < 20 || form.information.trim().length < 3} onClick={open}>Open the incident</button>
      </div>

      {v.data.incidents.length === 0 ? <div className="empty">No incidents.</div> : v.data.incidents.map((i) => (
        <div key={i.id} className="panel">
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <h3 style={{ margin: 0 }}>{i.ref}: {i.status}</h3>
            <span className={i.overdue ? "error" : "muted"}>{i.notices_sent_on ? `${i.notices_sent} notices sent ${i.notices_sent_on}`
              : i.determination === "no_notice" ? "No notice: documented" : `Notice due ${i.notice_due}${i.days_left !== null ? ` (${i.days_left} days)` : ""}`}</span>
          </div>
          <p className="small">{i.summary}</p>
          <p className="small muted">Aware {i.aware_on} · reported by {i.reported_by} · accounts {i.accounts.join(", ") || "none named"} ·
            information: {i.information}{i.service_provider ? ` · at ${i.service_provider}${i.provider_late ? " (told us after 72 hours)" : ""}` : ""}</p>
          {i.containment && <p className="small">Contained {i.contained_on}: {i.containment}</p>}
          {i.determination && <p className="small">Determination ({i.determined_by}): {i.determination === "notify" ? "notify the customers" : "no notice"}: {i.determination_reason}</p>}
          {i.status !== "closed" && (
            <div className="row" style={{ alignItems: "end" }}>
              <div className="field"><label htmlFor={`n${i.id}`}>{i.contained_on ? "Reason for the determination" : "How it was contained"}</label>
                <input id={`n${i.id}`} type="text" value={notes[i.id] ?? ""} onChange={(e) => setNotes({ ...notes, [i.id]: e.target.value })} /></div>
              {!i.contained_on && <button className="secondary" onClick={() => act(`/api/privacy/${i.id}/contain`, { note: notes[i.id] ?? "" }, "Recorded as contained.")}>Contained</button>}
              {i.contained_on && !i.determination && <>
                <button onClick={() => act(`/api/privacy/${i.id}/determine`, { decision: "notify", reason: notes[i.id] ?? "" }, "Determination: notify.")}>Notify the customers</button>
                <button className="secondary" onClick={() => act(`/api/privacy/${i.id}/determine`, { decision: "no_notice", reason: notes[i.id] ?? "" }, "Documented: no notice.")}>No harm reasonably likely</button></>}
              {i.determination === "notify" && !i.notices_sent_on && <button onClick={() => act(`/api/privacy/${i.id}/notify`, {}, "Notices sent.")}>Send the notices</button>}
            </div>)}
        </div>))}
    </>
  );
}
