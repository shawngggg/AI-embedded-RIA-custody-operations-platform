import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Loading, useLoad, when } from "../util";

type Entry = { entry_id: string; kind: string; name: string; aliases: string[]; date_of_birth: string | null; country: string | null;
  program: string; added: boolean; added_by: string | null; added_at: string | null };
type Run = { at: string; by: string; trigger: string; parties: number; targets: number; list_size: number; alerts: number[] };
type Alert = { id: number; title: string; status: string; target_id: string; trigger: string; created_at: string; hits: string[] };
type View = { entries: Entry[]; runs: Run[]; alerts: Alert[]; can_act: boolean; population: number };
type Ran = { trigger: string; parties: number; targets: number; list_size: number; alerts: { case: number; name: string; hits: string[] }[] };

const TRIGGER: Record<string, string> = { list_change: "List update", nightly: "Nightly" };
const STATUS: Record<string, string> = { PENDING_SANCTIONS: "Waiting for the sanctions team", CLEARED: "Cleared", BLOCKED: "Blocked" };

/** Ongoing screening: the flagged list in force, list updates, rescreening runs, and the alerts they raised. */
export default function Screening() {
  const data = useLoad(() => api<View>("/api/screening"), []);
  const [form, setForm] = useState({ kind: "individual", name: "", aliases: "", date_of_birth: "", country: "", program: "" });
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [busy, setBusy] = useState(false);
  if (!data.data) return <Loading />;
  const v = data.data;
  const said = (r: Ran) => setMsg({ ok: `${TRIGGER[r.trigger]} rescreen: ${r.parties} parties on ${r.targets} accounts and firms against ${r.list_size} entries. `
    + (r.alerts.length ? `${r.alerts.length} new match${r.alerts.length === 1 ? "" : "es"}: ${r.alerts.map((a) => `#${a.case} ${a.name}`).join(", ")}.` : "No new matches.") });
  const add = () => {
    setBusy(true); setMsg({});
    api<{ run: Ran }>("/api/screening/entries", { body: { ...form, aliases: form.aliases.split(",").map((a) => a.trim()).filter(Boolean),
      date_of_birth: form.date_of_birth || null, country: form.country || null, program: form.program || null } })
      .then((r) => { said(r.run); setForm({ ...form, name: "", aliases: "", date_of_birth: "", country: "" }); data.reload(); })
      .catch((e: Error) => setMsg({ error: e.message })).finally(() => setBusy(false));
  };
  const rescreen = () => {
    setBusy(true); setMsg({});
    api<{ run: Ran }>("/api/screening/run", { body: {} }).then((r) => { said(r.run); data.reload(); })
      .catch((e: Error) => setMsg({ error: e.message })).finally(() => setBusy(false));
  };

  return (
    <>
      <h1>Ongoing screening</h1>
      <p className="muted">Everyone the custodian serves is checked again whenever the flagged list changes and every night: each open account's
        holders, owners, control persons, beneficiaries, and standing payees and their banks, and each active RIA and its people
        ({v.population} parties today). A match the sanctions team cleared for that client isn't raised again. A new one places the
        sanctions hold on the account or firm at once and waits for the sanctions team, under maker-checker.</p>
      {msg.ok && <p className="ok">{msg.ok}</p>}{msg.error && <p className="error">{msg.error}</p>}

      {v.can_act && (
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Add a list update</h2>
          <p className="small muted">Stands in for the list provider's feed (synthetic names only). Adding an entry rescreens everyone at once.</p>
          <div className="row">
            <div className="field" style={{ flex: "0 1 170px" }}><label htmlFor="fk">Kind</label>
              <select id="fk" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
                <option value="individual">Individual</option><option value="entity">Entity</option><option value="institution">Bank</option>
              </select></div>
            <div className="field"><label htmlFor="fn">Name</label>
              <input id="fn" type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
            <div className="field"><label htmlFor="fa">Aliases (comma-separated)</label>
              <input id="fa" type="text" value={form.aliases} onChange={(e) => setForm({ ...form, aliases: e.target.value })} /></div>
          </div>
          <div className="row">
            <div className="field"><label htmlFor="fd">Date of birth</label>
              <input id="fd" type="date" value={form.date_of_birth} onChange={(e) => setForm({ ...form, date_of_birth: e.target.value })} /></div>
            <div className="field"><label htmlFor="fc">Country</label>
              <input id="fc" type="text" value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value })} /></div>
            <div className="field"><label htmlFor="fp">Program</label>
              <input id="fp" type="text" placeholder="Synthetic list update" value={form.program} onChange={(e) => setForm({ ...form, program: e.target.value })} /></div>
          </div>
          <div className="actions">
            <button disabled={busy || form.name.trim().length < 3} onClick={add}>Add and rescreen</button>
            <button className="secondary" disabled={busy} onClick={rescreen}>Run the nightly rescreen now</button>
          </div>
        </div>
      )}

      <h2>Alerts</h2>
      {v.alerts.length === 0 ? <div className="empty">No rescreening alerts yet.</div> : (
        <div className="scroll"><table>
          <thead><tr><th>Case</th><th>Match</th><th>Raised by</th><th>Status</th></tr></thead>
          <tbody>{v.alerts.map((a) => (
            <tr key={a.id}><td><Link to={`/cases/${a.id}`}>#{a.id} {a.title.replace("Ongoing screening: ", "")}</Link>
              <div className="small muted mono">{a.target_id}</div></td>
              <td className="small">{a.hits.join("; ")}</td>
              <td className="small">{TRIGGER[a.trigger] ?? a.trigger}<div className="muted">{when(a.created_at)}</div></td>
              <td>{STATUS[a.status] ?? a.status}</td></tr>))}</tbody>
        </table></div>)}

      <h2>Runs</h2>
      {v.runs.length === 0 ? <div className="empty">No runs yet.</div> : (
        <div className="scroll"><table>
          <thead><tr><th>When</th><th>Why</th><th className="num">Parties</th><th className="num">List entries</th><th className="num">New matches</th></tr></thead>
          <tbody>{v.runs.map((r, i) => (
            <tr key={i}><td>{when(r.at)}<div className="small muted">{r.by}</div></td><td>{TRIGGER[r.trigger] ?? r.trigger}</td>
              <td className="num">{r.parties}</td><td className="num">{r.list_size}</td><td className="num">{r.alerts.length}</td></tr>))}</tbody>
        </table></div>)}

      <h2>The flagged list ({v.entries.length} entries)</h2>
      <div className="scroll"><table>
        <thead><tr><th>Entry</th><th>Name</th><th>Kind</th><th>Details</th><th>Program</th></tr></thead>
        <tbody>{v.entries.map((e) => (
          <tr key={e.entry_id}><td className="mono">{e.entry_id}</td>
            <td>{e.name}{e.aliases.length > 0 && <div className="small muted">also {e.aliases.join(", ")}</div>}</td>
            <td>{e.kind}</td><td className="small">{[e.date_of_birth && `born ${e.date_of_birth}`, e.country].filter(Boolean).join(", ")}</td>
            <td className="small">{e.program}{e.added && <div className="muted">added by {e.added_by}, {when(e.added_at!)}</div>}</td></tr>))}</tbody>
      </table></div>
    </>
  );
}
