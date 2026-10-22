import { api, type SuggestionSummary } from "../api";
import { CaseLink } from "../caselink";
import { Loading, STATUS, useLoad, when } from "../util";
import { clock } from "../walkthrough";

const pct = (x: number | null) => (x === null ? "–" : `${Math.round(x * 100)}%`);

export default function Dashboard() {
  const d = useLoad(() => api<any>("/api/dashboard"), []);
  if (!d.data) return <Loading />;
  const x = d.data;
  const breached = x.queues.reduce((n: number, q: any) => n + q.breached, 0);
  const max = Math.max(1, ...x.queues.map((q: any) => q.count));
  const ai: SuggestionSummary = x.ai_suggestions;
  const w = x.walkthrough;
  return (
    <>
      <h1>Operations dashboard</h1>
      <p className="muted">Synthetic workload. Straight-through means opened with no manual work before the registered principal&apos;s acceptance, which every account needs (FINRA Rule 4512).</p>
      <div className="grid tiles">
        <div className="tile"><div className="label">Cases</div><div className="value">{x.cases}</div><div className="sub">all kinds</div></div>
        <div className="tile"><div className="label">Straight-through rate</div><div className="value">{pct(x.straight_through.rate)}</div><div className="sub">{x.straight_through.no_manual_step} of {x.straight_through.opened} opened</div></div>
        <div className="tile"><div className="label">NIGO rate</div><div className="value">{pct(x.nigo.rate)}</div><div className="sub">{x.nigo.with_nigo} of {x.nigo.onboarding_cases} onboarding cases</div></div>
        <div className="tile"><div className="label">Past SLA</div><div className="value">{breached}</div><div className={`sub ${breached ? "alert" : ""}`}>across all queues</div></div>
        <div className="tile"><div className="label">AI suggestions accepted unchanged</div><div className="value">{pct(ai.live.share_accepted)}</div>
          <div className="sub">{ai.live.reviewed ? `${ai.live.accepted} of ${ai.live.reviewed} live values reviewed` : "no live values reviewed yet"}</div></div>
        <div className="tile"><div className="label">Reviewer walkthrough (median)</div><div className="value">{w.median_seconds === null ? "–" : clock(w.median_seconds)}</div>
          <div className={`sub ${w.first_time_runs && !w.met ? "alert" : ""}`}>{w.first_time_runs ? `${w.under_target} of ${w.first_time_runs} first-time runs under 5:00` : "no first-time runs yet"}</div></div>
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
          <h2>AI suggestions</h2>
          <p className="muted small">Each value the AI reads from a package is checked by onboarding. Live: {ai.live.suggestions} values on {ai.live.cases} {ai.live.cases === 1 ? "case" : "cases"};
            {" "}{ai.live.accepted} accepted, {ai.live.edited} edited, {ai.live.rejected} rejected, {ai.live.pending} waiting.
            {ai.illustrative.suggestions > 0 && ` The illustrative sample's ${ai.illustrative.reviewed} reviewed values are practice and not counted.`}</p>
          {ai.recent_changes?.length ? (
            <div className="scroll"><table>
              <thead><tr><th>What the reviewer changed</th><th>AI</th><th>Reviewer</th></tr></thead>
              <tbody>{ai.recent_changes.map((c) => (
                <tr key={c.id}>
                  <td className="small">{c.label}<div className="muted">{c.case_id ? <CaseLink id={c.case_id}>{c.case}</CaseLink> : `${c.case} (removed by a demo reset)`}
                    {c.mode !== "live" && " · illustrative"}</div></td>
                  <td className="small">{c.suggested}</td>
                  <td className="small">{c.status === "rejected" ? <span className="pill t-block">Rejected</span> : c.final}
                    {c.reason && <div>“{c.reason}”</div>}<div className="muted">{c.by}, {when(c.at)}</div></td>
                </tr>))}</tbody>
            </table></div>
          ) : <div className="empty">No edits or rejections yet.</div>}
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
