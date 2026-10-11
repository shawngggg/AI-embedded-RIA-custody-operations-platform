import { useState } from "react";
import { api, type Restriction } from "../api";
import { useAuth } from "../auth";
import { Loading, useLoad, when } from "../util";

const FUNCTION: Record<string, string> = { ops_analyst: "operations", ops_supervisor: "operations",
  aml_compliance: "aml_compliance", sanctions: "sanctions", periodic_review: "periodic_review" };

export default function Restrictions() {
  const { user } = useAuth();
  const [showAll, setShowAll] = useState(false);
  const list = useLoad(() => api<Restriction[]>(`/api/restrictions${showAll ? "?active=" : ""}`), [showAll]);
  const catalog = useLoad(() => api<any[]>("/api/restrictions/catalog"), []);
  const [place, setPlace] = useState({ target_id: "", code: "CIPV", reason: "" });
  const [removing, setRemoving] = useState<Record<number, { reason: string; authorization: string }>>({});
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});

  const doPlace = (e: React.FormEvent) => {
    e.preventDefault();
    api("/api/restrictions", { body: place }).then(() => { setMsg({ ok: `${place.code} placed on ${place.target_id}` }); list.reload(); })
      .catch((err) => setMsg({ error: err.message }));
  };
  const doRemove = (r: Restriction) => {
    const f = removing[r.id] ?? { reason: "", authorization: "" };
    api(`/api/restrictions/${r.id}/remove`, { body: { reason: f.reason, authorization: f.authorization || null } })
      .then(() => { setMsg({ ok: `${r.code} removed` }); list.reload(); }).catch((err) => setMsg({ error: err.message }));
  };

  return (
    <>
      <h1>Restriction codes</h1>
      <p className="muted">A code stops specific activity and names the function that owns it. Anyone on an internal team can place one; only the owner removes it. Codes a case placed are released by that case's decision.</p>
      {msg.ok && <p className="ok">{msg.ok}</p>}{msg.error && <p className="error">{msg.error}</p>}
      <label className="check"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Include removed codes</label>
      {!list.data ? <Loading /> : list.data.length ? (
        <div className="scroll"><table>
          <thead><tr><th>Code</th><th>On</th><th>Owner</th><th>Blocks</th><th>Reason</th><th>Placed</th><th></th></tr></thead>
          <tbody>{list.data.map((r) => {
            const mine = FUNCTION[user.role] === r.owner;
            const f = removing[r.id] ?? { reason: "", authorization: "" };
            return (
              <tr key={r.id}>
                <td><span className={`code ${r.active ? "" : "off"}`} title={r.description}>{r.code}</span></td>
                <td className="mono">{r.target_id}</td><td>{r.owner.replace(/_/g, " ")}</td><td className="small">{r.blocks.join(", ")}</td>
                <td>{r.reason}{r.removal_reason && <><br /><span className="muted small">Removed: {r.removal_reason}{r.authorization ? ` (${r.authorization})` : ""}</span></>}</td>
                <td className="small">{r.placed_by}<br />{when(r.placed_at)} · {r.source}</td>
                <td style={{ minWidth: 220 }}>{r.active && r.source !== "case" && (mine ? (
                  <div>
                    <input type="text" placeholder="Reason" value={f.reason} onChange={(e) => setRemoving({ ...removing, [r.id]: { ...f, reason: e.target.value } })} />
                    {r.requires_authorization && <input type="text" placeholder="Authorization ref" value={f.authorization} style={{ marginTop: 4 }} onChange={(e) => setRemoving({ ...removing, [r.id]: { ...f, authorization: e.target.value } })} />}
                    <button className="small" style={{ marginTop: 4 }} onClick={() => doRemove(r)}>Remove</button>
                  </div>) : <span className="muted small">Only {r.owner.replace(/_/g, " ")} removes this</span>)}
                  {r.active && r.source === "case" && r.case_id && <a href={`/cases/${r.case_id}`}>Released by case #{r.case_id}</a>}
                </td>
              </tr>
            );
          })}</tbody>
        </table></div>
      ) : <div className="empty">No active codes.</div>}
      <form className="panel" onSubmit={doPlace} style={{ marginTop: 16, maxWidth: 640 }}>
        <h2 style={{ marginTop: 0 }}>Place a code</h2>
        <div className="row">
          <div className="field"><label htmlFor="t">Account or firm id</label><input id="t" type="text" placeholder="ACC-2001" value={place.target_id} onChange={(e) => setPlace({ ...place, target_id: e.target.value })} /></div>
          <div className="field"><label htmlFor="c">Code</label>
            <select id="c" value={place.code} onChange={(e) => setPlace({ ...place, code: e.target.value })}>
              {(catalog.data ?? []).filter((c) => c.manual).map((c) => <option key={c.code} value={c.code}>{c.code}: {c.description}</option>)}
            </select></div>
        </div>
        <div className="field"><label htmlFor="rs">Reason</label><input id="rs" type="text" value={place.reason} onChange={(e) => setPlace({ ...place, reason: e.target.value })} /></div>
        <button type="submit" disabled={!place.target_id || place.reason.length < 3}>Place code</button>
      </form>
    </>
  );
}
