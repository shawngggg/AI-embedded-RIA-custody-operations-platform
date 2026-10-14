import { useCallback, useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { api, type User } from "./api";
import { AuthContext, isInternal } from "./auth";
import SignIn from "./pages/SignIn";
import Queues from "./pages/Queues";
import CasePage from "./pages/CasePage";
import Approvals from "./pages/Approvals";
import Intake from "./pages/Intake";
import Restrictions from "./pages/Restrictions";
import Reviews from "./pages/Reviews";
import Dashboard from "./pages/Dashboard";
import AuditPage from "./pages/AuditPage";
import Admin from "./pages/Admin";
import PortalHome from "./pages/PortalHome";
import PortalAccounts from "./pages/PortalAccounts";
import NewAccount from "./pages/NewAccount";
import NewChange from "./pages/NewChange";
import RiskEvents from "./pages/RiskEvents";
import Work from "./pages/Work";
import Messages from "./pages/Messages";
import Outbox from "./pages/Outbox";
import Planned from "./pages/Planned";
import { Loading } from "./util";

type Counts = { queues: { key: string; count: number; breached: number }[]; pending_approvals: number };

export default function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [counts, setCounts] = useState<Counts | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    api<{ user: User | null }>("/api/auth/session").then((s) => setUser(s.user)).catch(() => setUser(null));
  }, []);

  const [unread, setUnread] = useState(0);
  const refreshCounts = useCallback(() => {
    if (user && isInternal(user)) {
      if (user.capabilities?.cases) api<Counts>("/api/queues").then(setCounts).catch(() => setCounts(null));
      if (user.capabilities?.messages) api<{ unread_total: number }>("/api/channels/unread").then((r) => setUnread(r.unread_total)).catch(() => setUnread(0));
    }
  }, [user]);
  useEffect(() => {
    refreshCounts();
    const t = window.setInterval(refreshCounts, 30000);      // queue counts and unread messages stay current
    return () => window.clearInterval(t);
  }, [refreshCounts]);

  const signOut = () => {
    api("/api/auth/logout", { method: "POST" }).finally(() => { setUser(null); setCounts(null); navigate("/"); });
  };

  if (user === undefined) return <div className="signin"><Loading what="Waking the server" /></div>;
  if (user === null) return <SignIn onSignedIn={(u) => { setUser(u); navigate(u.workspace === "portal" ? "/portal" : "/work"); }} />;

  const internal = isInternal(user);
  const can = user.capabilities ?? {};
  const qcount = (key: string) => counts?.queues.find((q) => q.key === key);
  const total = counts ? counts.queues.reduce((n, q) => n + q.count, 0) : 0;
  const breached = counts ? counts.queues.reduce((n, q) => n + q.breached, 0) : 0;

  return (
    <AuthContext.Provider value={{ user, signOut, refreshCounts }}>
      <div className="shell">
        <nav className="side" aria-label="Main">
          <div className="brand">Custody Operations<span>{internal ? "Workbench" : "RIA and client portal"} · synthetic data</span></div>
          {internal ? (
            <>
              <NavLink to="/work">Work</NavLink>
              {can.queues ? <NavLink to="/queues">Queues {total ? <span className={`badge ${breached ? "alert" : ""}`}>{total}</span> : null}</NavLink>
                : can.cases && <NavLink to="/queues">Cases</NavLink>}
              {can.approvals && <NavLink to="/approvals">Approvals {counts?.pending_approvals ? <span className="badge alert">{counts.pending_approvals}</span> : null}</NavLink>}
              {can.reviews && <NavLink to="/reviews">Reviews {qcount("reviews")?.count ? <span className="badge">{qcount("reviews")!.count}</span> : null}</NavLink>}
              {can.intake && <NavLink to="/intake">New application</NavLink>}
              {can.messages && <NavLink to="/messages">Messages {unread ? <span className="badge alert">{unread}</span> : null}</NavLink>}
              {(can.restricted_accounts || can.risk_events || can.audit) && <div className="sep" role="separator" />}
              {can.restricted_accounts && <NavLink to="/restricted">Restricted accounts</NavLink>}
              {can.risk_events && <NavLink to="/risk-events">Risk events</NavLink>}
              {can.audit && <NavLink to="/audit">Audit trail</NavLink>}
              {can.dashboard && <NavLink to="/dashboard">Dashboard</NavLink>}
              {can.outbox && <NavLink to="/outbox">Email outbox</NavLink>}
              {(can.planned_transfers || can.planned_trading || can.planned_billing) && <div className="sep" role="separator" />}
              {can.planned_transfers && <NavLink to="/ops/transfers" className="planned">Transfers <span className="badge">R2</span></NavLink>}
              {can.planned_trading && <NavLink to="/ops/trading" className="planned">Trading <span className="badge">R3</span></NavLink>}
              {can.planned_billing && <NavLink to="/ops/billing" className="planned">Billing <span className="badge">R3</span></NavLink>}
              {can.admin && <><div className="sep" role="separator" /><NavLink to="/admin">Users and rules</NavLink></>}
            </>
          ) : (
            <>
              <NavLink to="/portal" end>Home</NavLink>
              <NavLink to="/portal/accounts">Accounts and requests</NavLink>
              {user.role !== "client" && <NavLink to="/portal/new-account">New account</NavLink>}
              <NavLink to="/portal/change">Account change</NavLink>
              {user.role === "ria_admin" && <NavLink to="/portal/users">Firm users</NavLink>}
              <div className="sep" role="separator" />
              <NavLink to="/portal/transfers" className="planned">Transfers <span className="badge">R2</span></NavLink>
              <NavLink to="/portal/trading" className="planned">Trading <span className="badge">R3</span></NavLink>
              {user.role !== "client" && <NavLink to="/portal/billing" className="planned">Billing <span className="badge">R3</span></NavLink>}
            </>
          )}
        </nav>
        <main className="main">
          <div className="topbar">
            <div className="who">
              <strong>{user.display_name}</strong>
              <span className="role">{user.role_label}</span>
              {user.firm && <span className="muted small">{user.firm.name}</span>}
              {user.registrations.length > 0 && (
                <span className="muted small" title={user.registrations.map((r) => r.label).join("\n")}>
                  {user.registrations.map((r) => r.label.split(" (")[0]).join(" · ")}{user.individual_crd ? ` · CRD ${user.individual_crd}` : ""}
                </span>
              )}
            </div>
            <button className="secondary small" onClick={signOut}>{user.is_demo ? "Switch user" : "Sign out"}</button>
          </div>
          <Routes>
            {internal ? (
              <>
                <Route path="/work" element={<Work />} />
                {can.cases && <Route path="/queues" element={<Queues />} />}
                {can.approvals && <Route path="/approvals" element={<Approvals />} />}
                {can.reviews && <Route path="/reviews" element={<Reviews />} />}
                {can.intake && <Route path="/intake" element={<Intake />} />}
                {can.restricted_accounts && <Route path="/restricted" element={<Restrictions />} />}
                {can.audit && <Route path="/audit" element={<AuditPage />} />}
                {can.risk_events && <Route path="/risk-events" element={<RiskEvents />} />}
                {can.messages && <Route path="/messages" element={<Messages />} />}
                {can.dashboard && <Route path="/dashboard" element={<Dashboard />} />}
                {can.outbox && <Route path="/outbox" element={<Outbox />} />}
                {can.admin && <Route path="/admin" element={<Admin />} />}
                {can.planned_transfers && <Route path="/ops/transfers" element={<Planned which="transfers" audience="ops" />} />}
                {can.planned_trading && <Route path="/ops/trading" element={<Planned which="trading" audience="ops" />} />}
                {can.planned_billing && <Route path="/ops/billing" element={<Planned which="billing" audience="ops" />} />}
                {can.cases && <Route path="/cases/:id" element={<CasePage />} />}
                <Route path="*" element={<Navigate to="/work" replace />} />
              </>
            ) : (
              <>
                <Route path="/portal" element={<PortalHome />} />
                <Route path="/portal/accounts" element={<PortalAccounts />} />
                <Route path="/portal/new-account" element={<NewAccount />} />
                <Route path="/portal/change" element={<NewChange />} />
                <Route path="/portal/users" element={<Admin portal />} />
                <Route path="/portal/transfers" element={<Planned which="transfers" />} />
                <Route path="/portal/trading" element={<Planned which="trading" />} />
                {user.role !== "client" && <Route path="/portal/billing" element={<Planned which="billing" />} />}
                <Route path="/portal/cases/:id" element={<CasePage />} />
                <Route path="*" element={<Navigate to="/portal" replace />} />
              </>
            )}
          </Routes>
        </main>
      </div>
    </AuthContext.Provider>
  );
}
