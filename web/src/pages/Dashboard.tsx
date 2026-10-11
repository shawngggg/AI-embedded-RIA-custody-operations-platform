import { api } from "../api";
import { Loading, STATUS, useLoad } from "../util";

const pct = (x: number | null) => (x === null ? "–" : `${Math.round(x * 100)}%`);

export default function Dashboard() {
  const d = useLoad(() => api<any>("/api/dashboard"), []);
  if (!d.data) return <Loading />;
  const x = d.data;
  const breached = x.queues.reduce((n: number, q: any) => n + q.breached, 0);
  const max = Math.max(1, ...x.queues.map((q: any) => q.count));
  return (
    <>
      <h1>Operations dashboard</h1>
      <p className="muted">Synthetic workload. Straight-through means opened with no manual step besides approval.</p>
      <div className="grid tiles">
        <div className="tile"><div className="label">Cases</div><div className="value">{x.cases}</div><div className="sub">all kinds</div></div>
        <div className="tile"><div className="label">Straight-through rate</div><div className="value">{pct(x.straight_through.rate)}</div><div className="sub">{x.straight_through.no_manual_step} of {x.straight_through.opened} opened</div></div>
        <div className="tile"><div className="label">NIGO rate</div><div className="value">{pct(x.nigo.rate)}</div><div className="sub">{x.nigo.with_nigo} of {x.nigo.onboarding_cases} onboarding cases</div></div>
        <div className="tile"><div className="label">Past SLA</div><div className="value">{breached}</div><div className={`sub ${breached ? "alert" : ""}`}>across all queues</div></div>
      </div>
      <h2>Queues</h2>
      <div className="panel">
        {x.queues.map((q: any) => (
          <div key={q.key} style={{ display: "grid", gridTemplateColumns: "200px 1fr 60px", gap: 12, alignItems: "center", margin: "6px 0" }}>
            <span>{q.label}</span>
            <div style={{ background: "var(--line)", borderRadius: 4, height: 12 }} aria-hidden>
              <div style={{ width: `${(q.count / max) * 100}%`, background: "var(--ink)", height: 12, borderRadius: 4 }} />
            </div>
            <span><strong>{q.count}</strong>{q.breached ? <span className="error"> ({q.breached})</span> : null}</span>
          </div>
        ))}
      </div>
      <div className="grid two">
        <section>
          <h2>Average hours in each waiting state</h2>
          <div className="scroll"><table>
            <thead><tr><th>State</th><th>Cases</th><th>Average hours</th></tr></thead>
            <tbody>{Object.entries(x.hours_in_state).map(([k, v]: any) => (
              <tr key={k}><td>{STATUS[k]?.[0] ?? k}</td><td>{v.cases}</td><td>{v.average}</td></tr>))}</tbody>
          </table></div>
        </section>
        <section>
          <h2>Active restriction codes</h2>
          <div className="scroll"><table>
            <thead><tr><th>Code</th><th>Count</th></tr></thead>
            <tbody>{Object.entries(x.active_codes).map(([k, v]: any) => <tr key={k}><td><span className="code">{k}</span></td><td>{v}</td></tr>)}</tbody>
          </table></div>
        </section>
      </div>
    </>
  );
}
