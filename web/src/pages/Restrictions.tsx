import { Fragment, useMemo, useState } from "react";
import { CaseLink } from "../caselink";
import { api, type Restriction } from "../api";
import { useAuth } from "../auth";
import { Loading, Status, useLoad, when } from "../util";

const FUNCTION: Record<string, string> = { ops_analyst: "operations", ops_supervisor: "operations",
  aml_compliance: "aml_compliance", sanctions: "sanctions", periodic_review: "periodic_review",
  ria_service: "ria_service", ria_service_lead: "ria_service" };
const pretty = (s: string) => s.replace(/_/g, " ");

type Restricted = {
  target_id: string; kind: "account" | "firm"; name: string; firm: string | null; status: string | null; group: string | null;
  codes: Restriction[]; blocks: string[]; owners: string[]; since: string; case_ids: number[];
};

/** Accounts and firms that carry an active restriction, one row each. */
export default function Restrictions() {
  const { user } = useAuth();
  const list = useLoad(() => api<Restricted[]>("/api/restrictions/accounts"), []);
  const history = useLoad(() => api<Restriction[]>("/api/restrictions?active=false"), []);
  const catalog = useLoad(() => api<any[]>("/api/restrictions/catalog"), []);
  const [q, setQ] = useState("");
  const [code, setCode] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [place, setPlace] = useState({ target_id: "", code: "CIPV", reason: "" });
  const [removing, setRemoving] = useState<Record<number, { reason: string; authorization: string }>>({});
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});

  const codes = useMemo(() => [...new Set((list.data ?? []).flatMap((r) => r.codes.map((c) => c.code)))].sort(), [list.data]);
  const rows = (list.data ?? []).filter((r) => (!code || r.codes.some((c) => c.code === code)) &&
    (!q || `${r.name} ${r.target_id} ${r.firm ?? ""}`.toLowerCase().includes(q.toLowerCase())));
  const reload = () => { list.reload(); history.reload(); };

  const doPlace = (e: React.FormEvent) => {
    e.preventDefault();
    api("/api/restrictions", { body: place }).then(() => { setMsg({ ok: `${place.code} placed on ${place.target_id}` }); setOpen(place.target_id); reload(); })
      .catch((err) => setMsg({ error: err.message }));
  };
  const doRemove = (r: Restriction) => {
    const f = removing[r.id] ?? { reason: "", authorization: "" };
    api(`/api/restrictions/${r.id}/remove`, { body: { reason: f.reason, authorization: f.authorization || null } })
      .then(() => { setMsg({ ok: `${r.code} removed from ${r.target_id}` }); reload(); }).catch((err) => setMsg({ error: err.message }));
  };

  return (
    <>
      <h1>Restricted accounts</h1>
      <p className="muted">Accounts and firms with an active restriction: what's blocked, which team owns each code, and since when. Only the owning team removes a code; a code a case placed comes off with that case's decision.</p>
      {msg.ok && <p className="ok">{msg.ok}</p>}{msg.error && <p className="error">{msg.error}</p>}
      <div className="row" style={{ alignItems: "flex-end", marginBottom: 8 }}>
        <div className="field" style={{ flex: "1 1 260px" }}><label htmlFor="rq">Find an account or firm</label>
          <input id="rq" type="search" placeholder="Name, account id, or firm" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <div className="field" style={{ flex: "0 1 200px" }}><label htmlFor="rc">Code</label>
          <select id="rc" value={code} onChange={(e) => setCode(e.target.value)}>
            <option value="">All codes</option>{codes.map((c) => <option key={c}>{c}</option>)}
          </select></div>
      </div>
      {!list.data ? <Loading /> : rows.length ? (
        <div className="scroll"><table className="restricted">
          <thead><tr><th>Account or firm</th><th>Status</th><th>What's blocked</th><th>Codes</th><th>Restricted since</th><th></th></tr></thead>
          <tbody>{rows.map((r) => (
            <Fragment key={r.target_id}>
              <tr className="link" onClick={() => setOpen(open === r.target_id ? null : r.target_id)}>
                <td><strong>{r.name}</strong><div className="muted small"><span className="mono">{r.target_id}</span>
                  {r.kind === "firm" ? " · RIA firm" : ` · ${r.group ?? "account"}${r.firm ? ` · ${r.firm}` : ""}`}</div></td>
                <td>{r.status ? <Status status={r.status} /> : null}</td>
                <td className="small">{r.blocks.includes("all") ? <strong>All activity</strong> : r.blocks.map(pretty).join(", ")}</td>
                <td>{r.codes.map((c) => <span key={c.id} className="code" title={`${c.description} (owner: ${pretty(c.owner)})`}>{c.code}</span>)}</td>
                <td className="small">{when(r.since)}</td>
                <td><button className="small secondary" aria-expanded={open === r.target_id}>{open === r.target_id ? "Hide" : "Details"}</button></td>
              </tr>
              {open === r.target_id && (
                <tr className="detail"><td colSpan={6}>
                  {r.codes.map((c) => {
                    const mine = FUNCTION[user.role] === c.owner;
                    const f = removing[c.id] ?? { reason: "", authorization: "" };
                    return (
                      <div key={c.id} className="code-detail">
                        <div><span className="code">{c.code}</span> <strong>{c.description}</strong>
                          <div className="small muted">Owner: {pretty(c.owner)} · blocks {c.blocks.map(pretty).join(", ")} · placed by {c.placed_by}, {when(c.placed_at)} ({c.source})</div>
                          <div className="small">{c.reason}</div></div>
                        <div className="code-action">
                          {c.source === "case" && c.case_id ? <CaseLink id={c.case_id}>Comes off with case #{c.case_id}</CaseLink>
                            : c.code === "RSCN" ? <span className="muted small">Comes off through the rescission decision</span>
                            : mine ? (
                              <>
                                <input type="text" placeholder="Why it can come off" value={f.reason} onChange={(e) => setRemoving({ ...removing, [c.id]: { ...f, reason: e.target.value } })} />
                                {c.requires_authorization && <input type="text" placeholder="Authorization reference" value={f.authorization} onChange={(e) => setRemoving({ ...removing, [c.id]: { ...f, authorization: e.target.value } })} />}
                                <button className="small" disabled={f.reason.length < 3} onClick={() => doRemove(c)}>Remove {c.code}</button>
                              </>) : <span className="muted small">Only {pretty(c.owner)} removes this</span>}
                        </div>
                      </div>);
                  })}
                </td></tr>
              )}
            </Fragment>
          ))}</tbody>
        </table></div>
      ) : <div className="empty">{list.data.length ? "No restricted account matches." : "No account or firm is restricted."}</div>}

      <form className="panel" onSubmit={doPlace} style={{ marginTop: 16, maxWidth: 680 }}>
        <h2 style={{ marginTop: 0 }}>Restrict an account</h2>
        <div className="row">
          <div className="field"><label htmlFor="t">Account or firm id</label><input id="t" type="text" placeholder="ACC-2001 or FIRM-310001" value={place.target_id} onChange={(e) => setPlace({ ...place, target_id: e.target.value })} /></div>
          <div className="field"><label htmlFor="c">Code</label>
            <select id="c" value={place.code} onChange={(e) => setPlace({ ...place, code: e.target.value })}>
              {(catalog.data ?? []).filter((c) => c.manual).map((c) => <option key={c.code} value={c.code}>{c.code}: {c.description}</option>)}
            </select></div>
        </div>
        <div className="field"><label htmlFor="rs">Reason</label><input id="rs" type="text" value={place.reason} onChange={(e) => setPlace({ ...place, reason: e.target.value })} /></div>
        <button type="submit" disabled={!place.target_id || place.reason.length < 3}>Place code</button>
      </form>

      <label className="check" style={{ marginTop: 12 }}><input type="checkbox" checked={showHistory} onChange={(e) => setShowHistory(e.target.checked)} /> Show codes that came off</label>
      {showHistory && history.data && (history.data.length ? (
        <div className="scroll"><table>
          <thead><tr><th>Code</th><th>On</th><th>Placed</th><th>Removed</th></tr></thead>
          <tbody>{history.data.map((r) => (
            <tr key={r.id}><td><span className="code off">{r.code}</span></td><td className="mono">{r.target_id}</td>
              <td className="small">{r.placed_by}, {when(r.placed_at)}<div className="muted">{r.reason}</div></td>
              <td className="small">{r.removed_by}, {when(r.removed_at)}<div className="muted">{r.removal_reason}{r.authorization ? ` (${r.authorization})` : ""}</div></td></tr>
          ))}</tbody>
        </table></div>) : <div className="empty">No code has come off yet.</div>)}
    </>
  );
}
