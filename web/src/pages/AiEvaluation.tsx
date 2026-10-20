import { Fragment, useState } from "react";
import { api, type SuggestionSummary } from "../api";
import { Loading, useLoad, when } from "../util";

type Row = { key: string; about: string; seeded: string[]; flagged: string[]; caught: string[]; missed: string[];
  false_nigo: string[]; rejected?: number; error: string | null;
  evidence?: Record<string, { field: string; value: string; quote: string }[]> };
type Run = { at: string; by: string; model: string; prompt_version: string; auth: string; packages: number; seeded: number;
  caught: number; missed: number; recall: number | null; false_nigo: number; clean_packages_flagged: number; target: number;
  met: boolean; rows: Row[] };
type Evaluation = { latest: Run | null; can_run: boolean; why_not: string | null; model: string; prompt_version: string;
  target: number; rules: Record<string, string>; suggestions: SuggestionSummary;
  packages: { key: string; about: string; name: string; seeded: string[]; text: string }[] };

const pct = (x: number | null) => (x === null ? "–" : `${Math.round(x * 1000) / 10}%`);

/** The NIGO detection test set, run against the live model on demand (PRD guardrail 5, pre-release testing). */
export default function AiEvaluation() {
  const ev = useLoad(() => api<Evaluation>("/api/ai-evaluation"), []);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [open, setOpen] = useState<string | null>(null);
  if (!ev.data) return ev.error ? <p className="error">{ev.error}</p> : <Loading />;
  const d = ev.data;
  const r = d.latest;
  const rowFor = (key: string) => r?.rows.find((x) => x.key === key);
  const label = (ids: string[]) => ids.map((i) => d.rules[i] ?? i).join("; ");
  const runIt = () => {
    setBusy(true); setMsg({});
    api<Run>("/api/ai-evaluation", { body: {} })
      .then((x) => { setMsg({ ok: `Run finished: recall ${pct(x.recall)}, ${x.false_nigo} false NIGO.` }); ev.reload(); })
      .catch((e: Error) => setMsg({ error: e.message })).finally(() => setBusy(false));
  };
  return (
    <>
      <h2>AI evaluation: NIGO detection</h2>
      <p className="muted small">{d.packages.length} synthetic firm packages with {d.packages.reduce((n, p) => n + p.seeded.length, 0)} known
        defects, including traps (a certificate "to follow", a chart listed but not attached, a blank signature block) and clean packages.
        The model reads each one through the controls gate and the firm rules flag what isn't in good order. Recall is the share of seeded
        defects flagged (target {pct(d.target)}); a false NIGO is an item flagged missing that is in the package. Every run is logged with the model
        ({d.model}) and prompt version ({d.prompt_version}).</p>
      {r ? (
        <div className="grid tiles">
          <div className="tile"><div className="label">Recall</div><div className="value">{pct(r.recall)}</div>
            <div className={`sub ${r.met ? "" : "alert"}`}>{r.caught} of {r.seeded} defects · target {pct(r.target)}</div></div>
          <div className="tile"><div className="label">False NIGO</div><div className="value">{r.false_nigo}</div>
            <div className="sub">{r.clean_packages_flagged} of the clean packages flagged</div></div>
          <div className="tile"><div className="label">Last run</div><div className="value small-value">{when(r.at)}</div>
            <div className="sub">{r.by} · {r.model} · prompt {r.prompt_version}</div></div>
        </div>
      ) : <div className="empty">Not run yet. It needs live AI.</div>}
      {msg.ok && <p className="ok" role="status">{msg.ok}</p>}{msg.error && <p className="error" role="alert">{msg.error}</p>}
      <p><button onClick={runIt} disabled={!d.can_run || busy}>{busy ? "Running…" : `Run the evaluation (${d.packages.length} model calls)`}</button>
        {!d.can_run && <span className="muted small"> {d.why_not}</span>}</p>
      <Acceptance s={d.suggestions} />
      <h3>Test packages</h3>
      <div className="scroll"><table>
        <thead><tr><th>Package</th><th>Seeded defects</th><th>Last run</th></tr></thead>
        <tbody>{d.packages.map((p) => {
          const x = rowFor(p.key);
          return (
            <Fragment key={p.key}>
              <tr className="link" onClick={() => setOpen(open === p.key ? null : p.key)}>
                <td><strong>{p.about}</strong><div className="muted small">{p.name}</div></td>
                <td className="small">{p.seeded.length ? label(p.seeded) : <span className="muted">None (clean)</span>}</td>
                <td className="small">{!x ? <span className="muted">–</span> : x.error ? <span className="error">Call failed: {x.error}</span> : <>
                  {x.missed.length === 0 && x.false_nigo.length === 0 && <span className="pill t-clear">Correct</span>}
                  {x.missed.length > 0 && <div><span className="pill t-block">Missed</span> {label(x.missed)}</div>}
                  {x.false_nigo.length > 0 && <div><span className="pill t-refer">False NIGO</span> {label(x.false_nigo)}</div>}
                  {[...x.missed, ...x.false_nigo].map((rule) => x.evidence?.[rule] && (
                    <div key={rule} className="muted small">{x.evidence[rule].length
                      ? <>Model's evidence: {x.evidence[rule].map((e) => `“${e.quote}”`).join("; ")}</>
                      : "The model left it out"}</div>))}</>}</td>
              </tr>
              {open === p.key && <tr className="detail"><td colSpan={3}><pre className="email-body">{p.text}</pre></td></tr>}
            </Fragment>);
        })}</tbody>
      </table></div>
    </>
  );
}

/** The AI suggestion acceptance metric (PRD guardrail 6): what onboarding did with each live value. Counts only: the
 *  values and reasons stay with the case files, which the platform administrator doesn't have. */
function Acceptance({ s }: { s: SuggestionSummary }) {
  const l = s.live;
  return (
    <>
      <h3>AI suggestions in use</h3>
      <p className="muted small">Every value the AI reads from a real intake waits for onboarding to accept, edit, or reject it. The metric is the
        share of reviewed live values accepted without an edit; edits and rejections show which fields to test next.
        {s.illustrative.suggestions > 0 && ` The illustrative sample's ${s.illustrative.suggestions} values are tracked apart and not counted.`}</p>
      {l.reviewed ? (
        <>
          <div className="grid tiles">
            <div className="tile"><div className="label">Accepted unchanged</div><div className="value">{pct(l.share_accepted)}</div>
              <div className="sub">{l.accepted} of {l.reviewed} reviewed · {l.cases} {l.cases === 1 ? "case" : "cases"}</div></div>
            <div className="tile"><div className="label">Edited</div><div className="value">{l.edited}</div><div className="sub">a person corrected the value</div></div>
            <div className="tile"><div className="label">Rejected</div><div className="value">{l.rejected}</div><div className="sub">not in the package</div></div>
            <div className="tile"><div className="label">Waiting</div><div className="value">{l.pending}</div><div className="sub">not reviewed yet</div></div>
          </div>
          <div className="grid two">
            <div className="scroll"><table>
              <thead><tr><th>Field</th><th>Reviewed</th><th>Edited</th><th>Rejected</th></tr></thead>
              <tbody>{s.by_field.filter((f) => f.reviewed).map((f) => (
                <tr key={f.field}><td className="small">{f.label}</td><td>{f.reviewed}</td><td>{f.edited || ""}</td><td>{f.rejected || ""}</td></tr>))}</tbody>
            </table></div>
            <div className="scroll"><table>
              <thead><tr><th>Prompt version</th><th>Model</th><th>Accepted unchanged</th></tr></thead>
              <tbody>{s.by_prompt.map((p) => (
                <tr key={`${p.prompt_version}-${p.model}`}><td className="mono">{p.prompt_version ?? "–"}</td><td className="small">{p.model}</td>
                  <td>{pct(p.share_accepted)} <span className="muted small">({p.accepted} of {p.reviewed})</span></td></tr>))}</tbody>
            </table></div>
          </div>
        </>
      ) : <div className="empty">No live values reviewed yet{l.pending ? ` (${l.pending} waiting in onboarding)` : ""}. Read a package with live AI on New application, then review it.</div>}
    </>
  );
}
