import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, type CaseSummary } from "../api";
import { useAuth } from "../auth";
import { Loading, slaText, Status, useLoad, when } from "../util";

type Q = { key: string; label: string; owner: string; roles: string[]; sla_hours: number | null; count: number; breached: number; mine: boolean };

export default function Queues() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const queues = useLoad(() => api<{ queues: Q[] }>("/api/queues"), []);
  const [params] = useSearchParams();
  const submitted = useLoad(() => params.get("case") ? api<CaseSummary>(`/api/cases/${params.get("case")}`) : Promise.resolve(null), [params.get("case")]);
  const [selected, setSelected] = useState<string | null>(params.get("queue"));
  const list = queues.data?.queues.filter((q) => q.key !== "reviews") ?? [];
  const hasQueues = !!user.capabilities?.queues;
  const active = selected ?? (list.some((q) => q.key === submitted.data?.queue) ? submitted.data?.queue : null)
    ?? list.find((q) => q.count)?.key ?? list[0]?.key ?? null;
  const cases = useLoad(() => active ? api<CaseSummary[]>(`/api/cases?queue=${active}`) : Promise.resolve([]), [active]);
  const allOpen = useLoad(() => api<CaseSummary[]>("/api/cases"), []);

  return (
    <>
      <h1>{hasQueues ? "Work queues" : "Cases"}</h1>
      {submitted.data && (
        <p className="ok" role="status">Submitted: <Link to={`/cases/${submitted.data.id}`}>#{submitted.data.id} {submitted.data.title}</Link> is in{" "}
          <strong>{submitted.data.queue_label ?? "no queue (it needs no one right now)"}</strong>.</p>
      )}
      {hasQueues ? <p className="muted">The queues you work, oldest SLA first.{" "}
        You're <strong>{user.role_label}</strong>{user.team_label ? <> on the <strong>{user.team_label}</strong> team</> : null}
        {user.role === "ops_supervisor" ? "; you oversee every operations queue" : ""}.</p>
        : <p className="muted">Look up any case, for example the opening file of an account you're reviewing. You don't work a case queue.</p>}
      {!hasQueues ? null : queues.loading && !queues.data ? <Loading /> : (
        <div className="grid tiles" role="tablist" aria-label="Queues">
          {list.map((q) => (
            <button key={q.key} role="tab" aria-selected={q.key === active} className={`tile ${q.key === active ? "selected" : ""}`} onClick={() => setSelected(q.key)}>
              <div className="label">{q.label}</div>
              <div className="value">{q.count}</div>
              <div className={`sub ${q.breached ? "alert" : ""}`}>{q.breached ? `${q.breached} past SLA` : `${q.owner} · ${q.sla_hours}h SLA`}</div>
            </button>
          ))}
        </div>
      )}
      {hasQueues && <h2>{list.find((q) => q.key === active)?.label}</h2>}
      {!hasQueues ? null : cases.loading && !cases.data ? <Loading /> : cases.data && cases.data.length ? (
        <div className="scroll">
          <table>
            <thead><tr><th>Case</th><th>Firm</th><th>Status</th><th>Owner</th><th>In queue since</th><th>SLA</th></tr></thead>
            <tbody>
              {cases.data.map((c) => (
                <tr key={c.id} className="link" onClick={() => navigate(`/cases/${c.id}`)}>
                  <td><a href={`/cases/${c.id}`} onClick={(e) => { e.preventDefault(); navigate(`/cases/${c.id}`); }}>#{c.id} {c.title}</a></td>
                  <td>{c.firm}</td><td><Status status={c.status} /></td>
                  <td className="small">{c.assigned_to ? c.assigned_to.name : <span className="muted">Unassigned</span>}</td><td>{when(c.queue_entered_at)}</td>
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
