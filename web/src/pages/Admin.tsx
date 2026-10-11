import { useState } from "react";
import { api, type User } from "../api";
import { useAuth } from "../auth";
import { Loading, useLoad, when } from "../util";

export default function Admin({ portal = false }: { portal?: boolean }) {
  const { user } = useAuth();
  const platform = user.role === "platform_admin";
  const users = useLoad(() => api<User[]>("/api/admin/users"), []);
  const roles = useLoad(() => api<{ role: string; label: string; decision_role: boolean }[]>("/api/admin/roles"), []);
  const grants = useLoad(() => platform ? api<any[]>("/api/admin/grants") : Promise.resolve([]), []);
  const rules = useLoad<Record<string, any[]>>(() => platform ? api<Record<string, any[]>>("/api/admin/rules") : Promise.resolve({}), []);
  const [form, setForm] = useState({ username: "", display_name: "", email: "", role: portal ? "ria_user" : "ops_analyst",
    password: "", firm_crd: "", client_account_id: "" });
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [lib, setLib] = useState("Firm rules");

  const reload = () => { users.reload(); grants.reload(); };
  const create = (e: React.FormEvent) => {
    e.preventDefault();
    const body = { ...form, email: form.email || null, firm_crd: form.firm_crd || null, client_account_id: form.client_account_id || null };
    api<any>("/api/admin/users", { body }).then((r) => {
      setMsg({ ok: r.grant ? `${r.user.display_name} added; the ${r.grant.role_label} role waits for a second administrator.` : `${r.user.display_name} added.` });
      reload();
    }).catch((err) => setMsg({ error: err.message }));
  };
  const decide = (id: number, approve: boolean) => api(`/api/admin/grants/${id}/${approve ? "approve" : "reject"}`, { body: {} })
    .then(() => { setMsg({ ok: approve ? "Grant approved" : "Grant rejected" }); reload(); }).catch((e) => setMsg({ error: e.message }));
  const deactivate = (u: User) => api(`/api/admin/users/${u.id}/deactivate`, { body: {} })
    .then(() => { setMsg({ ok: `${u.display_name} deactivated` }); reload(); }).catch((e) => setMsg({ error: e.message }));
  const reset = () => api("/api/admin/reset", { body: {} }).then(() => { setMsg({ ok: "Demo data reset. Sign in again." }); setTimeout(() => location.assign("/"), 800); })
    .catch((e) => setMsg({ error: e.message }));
  const f = (k: keyof typeof form, label: string, type = "text") => (
    <div className="field"><label htmlFor={`f-${k}`}>{label}</label><input id={`f-${k}`} type={type} value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} /></div>
  );

  return (
    <>
      <h1>{portal ? "Firm users" : "Users, roles, and rules"}</h1>
      <p className="muted">{portal ? "Add your firm's users and grant their access. You can't grant more than your firm's own permissions."
        : "Add team members and assign roles. Giving someone a decision role (supervisor, AML compliance, sanctions, administrator) waits for a second administrator."}</p>
      {msg.ok && <p className="ok">{msg.ok}</p>}{msg.error && <p className="error">{msg.error}</p>}
      {platform && grants.data && grants.data.length > 0 && (
        <div className="decide">
          <h3>Grants waiting for approval</h3>
          {grants.data.map((g) => (
            <div key={g.id} className="row" style={{ alignItems: "center", marginBottom: 6 }}>
              <span><strong>{g.user}</strong> as {g.role_label}, requested by {g.requested_by}, {when(g.created_at)}</span>
              {g.can_approve ? <><button className="small" onClick={() => decide(g.id, true)}>Approve</button>
                <button className="small secondary" onClick={() => decide(g.id, false)}>Reject</button></>
                : <span className="muted small">You requested this; another administrator approves it.</span>}
            </div>
          ))}
        </div>
      )}
      <div className="grid two">
        <section>
          {!users.data ? <Loading /> : (
            <div className="scroll"><table>
              <thead><tr><th>User</th><th>Role</th><th>Firm or account</th><th>Status</th><th></th></tr></thead>
              <tbody>{users.data.map((u) => (
                <tr key={u.id}><td>{u.display_name}<br /><span className="mono muted">{u.username}</span></td><td>{u.role_label}</td>
                  <td className="small">{u.firm?.name ?? u.client_account_id ?? ""}</td>
                  <td>{u.active ? <span className="pill t-clear">Active</span> : <span className="pill t-quiet">Inactive</span>}{u.is_demo ? <span className="muted small"> demo</span> : null}</td>
                  <td>{u.active && u.id !== user.id && <button className="small secondary" onClick={() => deactivate(u)}>Deactivate</button>}</td></tr>
              ))}</tbody>
            </table></div>
          )}
        </section>
        <form className="panel" onSubmit={create}>
          <h2 style={{ marginTop: 0 }}>Add a user</h2>
          {f("display_name", "Name")}{f("username", "Username (lowercase)")}{f("email", "Email (optional)")}
          <div className="field"><label htmlFor="f-role">Role</label>
            <select id="f-role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              {(roles.data ?? []).map((r) => <option key={r.role} value={r.role}>{r.label}{r.decision_role ? " (needs second approval)" : ""}</option>)}
            </select></div>
          {platform && ["ria_user", "ria_admin"].includes(form.role) && f("firm_crd", "Firm CRD")}
          {form.role === "client" && f("client_account_id", "Client's self-directed account id")}
          {f("password", "Temporary password (10+ characters)", "password")}
          <button type="submit">Add user</button>
        </form>
      </div>
      {platform && rules.data && (
        <>
          <h2>Rule library</h2>
          <p className="muted small">Every rule version in the engine, with its effective dates and citation. The engine applies the version in force on the evaluation date.</p>
          <div className="tabs">{Object.keys(rules.data).map((k) => <button key={k} className={lib === k ? "on" : ""} onClick={() => setLib(k)}>{k}</button>)}</div>
          <div className="scroll"><table>
            <thead><tr><th>Rule</th><th>In force</th><th>What it checks</th><th>Source</th></tr></thead>
            <tbody>{(rules.data[lib] ?? []).map((r, i) => (
              <tr key={i}><td className="mono">{r.rule_id} v{r.version}</td><td className="small">{r.effective_date}{r.expiry_date ? ` to ${r.expiry_date}` : " onward"}</td>
                <td>{r.reason}{r.points ? ` (+${r.points})` : ""}</td><td className="small">{r.source}</td></tr>
            ))}</tbody>
          </table></div>
          <h2>Demo data</h2>
          <p className="muted small">Put every case, user, and code back to the seeded starting point.</p>
          <button className="danger" onClick={reset}>Reset the demo</button>
        </>
      )}
    </>
  );
}
