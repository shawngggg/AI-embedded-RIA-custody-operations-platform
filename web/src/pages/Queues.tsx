import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type CaseSummary } from "../api";
import { useAuth } from "../auth";
import { Loading, slaText, Status, useLoad, when } from "../util";

type Q = { key: string; label: string; owner: string; roles: string[]; sla_hours: number | null; count: number; breached: number; mine: boolean };

export default function Queues() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const queues = useLoad(() => api<{ queues: Q[] }>("/api/queues"), []);
  const [selected, setSelected] = useState<string | null>(null);
  const list = queues.data?.queues.filter((q) => q.key !== "reviews") ?? [];
  const active = selected ?? list.find((q) => q.mine && q.count)?.key ?? list.find((q) => q.count)?.key ?? "items_requested";
  const cases = useLoad(() => api<CaseSummary[]>(`/api/cases?queue=${active}`), [active]);
  const allOpen = useLoad(() => api<CaseSummary[]>("/api/cases"), []);

  return (
    <>
      <h1>Work queues</h1>
      <p className="muted">Cases wait here where their process map waits for a person. Oldest SLA first.{" "}
        {queues.data && <>Your role: <strong>{user.role_label}</strong>; queues you work are marked.</>}</p>
      {queues.loading && !queues.data ? <Loading /> : (
        <div className="grid tiles" role="tablist" aria-label="Queues">
          {list.map((q) => (
            <button key={q.key} role="tab" aria-selected={q.key === active} className={`tile ${q.key === active ? "selected" : ""}`} onClick={() => setSelected(q.key)}>
              <div className="label">{q.label}{q.mine ? " · yours" : ""}</div>
              <div className="value">{q.count}</div>
              <div className={`sub ${q.breached ? "alert" : ""}`}>{q.breached ? `${q.breached} past SLA` : `${q.owner} · ${q.sla_hours}h SLA`}</div>
            </button>
          ))}
        </div>
      )}
      <h2>{list.find((q) => q.key === active)?.label}</h2>
      {cases.loading && !cases.data ? <Loading /> : cases.data && cases.data.length ? (
        <div className="scroll">
          <table>
            <thead><tr><th>Case</th><th>Firm</th><th>Status</th><th>In queue since</th><th>SLA</th></tr></thead>
            <tbody>
              {cases.data.map((c) => (
                <tr key={c.id} className="link" onClick={() => navigate(`/cases/${c.id}`)}>
                  <td><a href={`/cases/${c.id}`} onClick={(e) => { e.preventDefault(); navigate(`/cases/${c.id}`); }}>#{c.id} {c.title}</a></td>
                  <td>{c.firm}</td><td><Status status={c.status} /></td><td>{when(c.queue_entered_at)}</td>
                  <td className={c.sla.breached ? "error" : ""}>{slaText(c.sla.hours_left)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <div className="empty">Nothing waiting in this queue.</div>}
      <h2>All cases</h2>
      {allOpen.data && (
        <div className="scroll">
          <table>
            <thead><tr><th>Case</th><th>Kind</th><th>Status</th><th>Updated</th></tr></thead>
            <tbody>
              {allOpen.data.map((c) => (
                <tr key={c.id} className="link" onClick={() => navigate(`/cases/${c.id}`)}>
                  <td>#{c.id} {c.title}</td><td>{c.kind}</td><td><Status status={c.status} /></td><td>{when(c.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
