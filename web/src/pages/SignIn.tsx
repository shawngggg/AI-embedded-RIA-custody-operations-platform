import { useEffect, useState } from "react";
import { api, type User } from "../api";
import { rememberWalkthrough } from "../walkthrough";

const GROUPS: [string, (u: User) => boolean][] = [
  ["Custody operations and second line", (u) => ["ops_analyst", "ops_supervisor", "aml_compliance", "sanctions", "periodic_review"].includes(u.role)],
  ["RIA service team (FINRA-licensed)", (u) => ["ria_service", "ria_service_lead"].includes(u.role)],
  ["Platform administration", (u) => u.role === "platform_admin"],
  ["RIA firms", (u) => ["ria_admin", "ria_user"].includes(u.role)],
  ["Clients", (u) => u.role === "client"],
];

export default function SignIn({ onSignedIn }: { onSignedIn: (u: User) => void }) {
  const [demo, setDemo] = useState<User[]>([]);
  const [demoMode, setDemoMode] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<{ demo_mode: boolean; users: User[] }>("/api/auth/demo-users")
      .then((d) => { setDemo(d.users); setDemoMode(d.demo_mode); })
      .catch((e) => setError(e.message));
  }, []);

  const asDemo = (u: User) => api<User>("/api/auth/demo", { body: { username: u.username } }).then(onSignedIn)
    .catch((e) => setError(e.message));
  const [firstTime, setFirstTime] = useState(true);
  // The clock starts here: sign in as the onboarding analyst, get a fresh case, and go to it
  const startWalkthrough = () => api<User>("/api/auth/demo", { body: { username: "ops.analyst" } })
    .then(() => api<{ case_id: number }>("/api/walkthrough", { body: { first_time: firstTime } }))
    .then((w) => { rememberWalkthrough(w.case_id); window.location.assign(`/cases/${w.case_id}`); })
    .catch((e) => setError(e.message));
  const login = (e: React.FormEvent) => {
    e.preventDefault();
    api<User>("/api/auth/login", { body: { username, password } }).then(onSignedIn).catch((err) => setError(err.message));
  };

  return (
    <div className="signin">
      <p className="muted small">RIA custody operations platform · MVP</p>
      <h1>Custody operations workbench</h1>
      <p className="muted" style={{ maxWidth: "70ch" }}>
        Internal teams work onboarding cases, escalations, and reviews in the workbench. RIA users and clients use
        the portal. Every decision is checked by a second person and written to the audit trail. All firms, people,
        and accounts are synthetic.
      </p>
      {error && <p className="error">{error}</p>}
      {demoMode && (
        <>
          <section className="panel" style={{ maxWidth: "70ch" }}>
            <h2 style={{ marginTop: 0 }}>Time yourself: application to open account</h2>
            <p className="small">You get a fresh account application whose LPOA just arrived from the RIA. As operations, record
              it; then, as a registered principal, accept the account. The clock runs until the account opens; the target is
              under 5 minutes. Your time is kept anonymously as the MVP&apos;s reviewer walkthrough measure.</p>
            <label className="check"><input type="checkbox" checked={firstTime} onChange={(e) => setFirstTime(e.target.checked)} />
              This is my first time using the platform</label>
            <div className="actions"><button onClick={startWalkthrough}>Start the clock</button></div>
          </section>
          <h2>Sign in as a demo user</h2>
          <p className="muted small">No password needed. Decisions need two people, so each deciding team has a maker and a checker.</p>
          {GROUPS.map(([label, test]) => {
            const users = demo.filter(test);
            if (!users.length) return null;
            return (
              <section key={label}>
                <h3>{label}</h3>
                <div className="who-grid">
                  {users.map((u) => (
                    <button key={u.username} className="who-card" onClick={() => asDemo(u)}>
                      <strong>{u.display_name}</strong>
                      <span>{u.role_label}{u.role === "ops_analyst" && u.team_label ? ` · ${u.team_label}` : ""}{u.firm ? ` · ${u.firm.name}` : ""}</span>
                      <span>{u.note}</span>
                    </button>
                  ))}
                </div>
              </section>
            );
          })}
        </>
      )}
      <h2>Sign in with a password</h2>
      <form className="panel" onSubmit={login} style={{ maxWidth: 420 }}>
        <div className="field"><label htmlFor="u">Username</label><input id="u" type="text" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} /></div>
        <div className="field"><label htmlFor="p">Password</label><input id="p" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></div>
        <button type="submit">Sign in</button>
      </form>
    </div>
  );
}
