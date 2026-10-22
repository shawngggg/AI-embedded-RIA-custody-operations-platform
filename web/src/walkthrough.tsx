import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "./api";

/** The timed reviewer walkthrough (app/walkthrough.py). The server keeps the clock; the browser only remembers which
 *  case is being timed, so the banner follows the reviewer across a switch of user. */
type Run = { case_id: number; status: string; steps: string[]; target_seconds: number; started_at: string;
  finished_at?: string; seconds?: number };
const KEY = "walkthrough";

export function rememberWalkthrough(caseId: number) {
  try { localStorage.setItem(KEY, String(caseId)); } catch { /* private window: the banner just won't show */ }
}
function remembered(): number | null {
  try { const v = localStorage.getItem(KEY); return v ? Number(v) : null; } catch { return null; }
}
function forget() {
  try { localStorage.removeItem(KEY); } catch { /* nothing to clear */ }
}
export const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export function WalkthroughBanner() {
  const [id, setId] = useState<number | null>(remembered);
  const [run, setRun] = useState<Run | null>(null);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!id) return;
    const load = () => api<Run>(`/api/walkthrough/${id}`).then(setRun).catch(() => { forget(); setId(null); });
    load();
    const poll = window.setInterval(load, 5000);
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => { window.clearInterval(poll); window.clearInterval(tick); };
  }, [id]);
  if (!id || !run) return null;
  const done = run.seconds !== undefined;
  const elapsed = done ? run.seconds! : Math.max(0, (now - new Date(run.started_at).getTime()) / 1000);
  const close = () => { forget(); setId(null); };
  return (
    <div className="panel walkthrough" role="status">
      {done ? (
        <p style={{ margin: 0 }}><strong>Done: the account opened in {clock(elapsed)}.</strong>{" "}
          {elapsed <= run.target_seconds ? "Under the 5-minute target." : "Over the 5-minute target."} Thank you for timing it.{" "}
          <button className="secondary small" onClick={close}>Close</button></p>
      ) : (
        <>
          <p style={{ margin: 0 }}><strong>Timed walkthrough · <Link to={`/cases/${run.case_id}`}>case #{run.case_id}</Link> · {clock(elapsed)}</strong>
            <span className="muted small"> · target {clock(run.target_seconds)}</span>{" "}
            <button className="secondary small" onClick={close}>Stop</button></p>
          <ol className="small" style={{ margin: "6px 0 0" }}>{run.steps.map((s) => <li key={s}>{s}</li>)}</ol>
        </>
      )}
    </div>
  );
}
