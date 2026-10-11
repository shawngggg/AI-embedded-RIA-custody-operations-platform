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
import NewAccount from "./pages/NewAccount";
import NewChange from "./pages/NewChange";
import { Loading } from "./util";

type Counts = { queues: { key: string; count: number; breached: number }[]; pending_approvals: number };

export default function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [counts, setCounts] = useState<Counts | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    api<{ user: User | null }>("/api/auth/session").then((s) => setUser(s.user)).catch(() => setUser(null));
  }, []);

  const refreshCounts = useCallback(() => {
    if (user && isInternal(user)) api<Counts>("/api/queues").then(setCounts).catch(() => setCounts(null));
  }, [user]);
  useEffect(() => { refreshCounts(); }, [refreshCounts]);

  const signOut = () => {
    api("/api/auth/logout", { method: "POST" }).finally(() => { setUser(null); setCounts(null); navigate("/"); });
  };

  if (user === undefined) return <div className="signin"><Loading what="Waking the server" /></div>;
  if (user === null) return <SignIn onSignedIn={(u) => { setUser(u); navigate(u.workspace === "portal" ? "/portal" : "/queues"); }} />;

  const internal = isInternal(user);
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
              <div className="group">Work</div>
              <NavLink to="/queues">Queues {total ? <span className={`badge ${breached ? "alert" : ""}`}>{total}</span> : null}</NavLink>
              <NavLink to="/approvals">Approvals {counts?.pending_approvals ? <span className="badge alert">{counts.pending_approvals}</span> : null}</NavLink>
              <NavLink to="/reviews">Reviews {qcount("reviews")?.count ? <span className="badge">{qcount("reviews")!.count}</span> : null}</NavLink>
              <NavLink to="/intake">New application</NavLink>
              <div className="group">Controls</div>
              <NavLink to="/restrictions">Restriction codes</NavLink>
              <NavLink to="/audit">Audit trail</NavLink>
              <NavLink to="/dashboard">Dashboard</NavLink>
              {user.role === "platform_admin" && <><div className="group">Admin</div><NavLink to="/admin">Users and rules</NavLink></>}
            </>
          ) : (
            <>
              <div className="group">Portal</div>
              <NavLink to="/portal" end>Accounts and requests</NavLink>
              {user.role !== "client" && <NavLink to="/portal/new-account">New account</NavLink>}
              <NavLink to="/portal/change">Account change</NavLink>
              {user.role === "ria_admin" && <NavLink to="/portal/users">Firm users</NavLink>}
            </>
          )}
        </nav>
        <main className="main">
          <div className="topbar">
            <div className="who">
              <strong>{user.display_name}</strong>
              <span className="role">{user.role_label}</span>
              {user.firm && <span className="muted small">{user.firm.name}</span>}
            </div>
            <button className="secondary small" onClick={signOut}>{user.is_demo ? "Switch user" : "Sign out"}</button>
          </div>
          <Routes>
            {internal ? (
              <>
                <Route path="/queues" element={<Queues />} />
                <Route path="/approvals" element={<Approvals />} />
                <Route path="/reviews" element={<Reviews />} />
                <Route path="/intake" element={<Intake />} />
                <Route path="/restrictions" element={<Restrictions />} />
                <Route path="/audit" element={<AuditPage />} />
                <Route path="/dashboard" element={<Dashboard />} />
                <Route path="/admin" element={<Admin />} />
                <Route path="/cases/:id" element={<CasePage />} />
                <Route path="*" element={<Navigate to="/queues" replace />} />
              </>
            ) : (
              <>
                <Route path="/portal" element={<PortalHome />} />
                <Route path="/portal/new-account" element={<NewAccount />} />
                <Route path="/portal/change" element={<NewChange />} />
                <Route path="/portal/users" element={<Admin portal />} />
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
