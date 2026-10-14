import { Link } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { Loading, Status, useLoad, when } from "../util";

type Home = {
  name: string; role: string;
  firm: { name: string; crd: string; status: string; since: string | null; reliance_eligible: boolean } | null;
  accounts: { total: number; open: number; pending: number; restricted: number };
  requests_open: number;
  attention: { case_id: number; title: string; status: string; why: string; items: string[]; since: string | null }[];
  contact: { team: string; name: string | null; email: string; hours: string };
};

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

/** The portal's front door: a welcome for the firm (or client), their relationship, and what needs them. */
export default function PortalHome() {
  const { user } = useAuth();
  const home = useLoad(() => api<Home>("/api/portal/home"), []);
  if (!home.data) return home.error ? <p className="error">{home.error}</p> : <Loading />;
  const h = home.data;
  const client = user.role === "client";
  const since = h.firm?.since ? new Date(h.firm.since).toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" }) : null;
  return (
    <>
      <section className="hero">
        <p className="eyebrow">{client ? "Your account at the custodian" : "Your custody relationship"}</p>
        <h1>{greeting()}, {h.name}</h1>
        {client ? (
          <p>This is your self-directed account. You can see its status and request changes here; your RIA can view it but doesn't manage it.</p>
        ) : h.firm && (
          <p><strong>{h.firm.name}</strong> · CRD {h.firm.crd} · {h.firm.status === "ACTIVE" ? `custody relationship active${since ? ` since ${since}` : ""}` : "onboarding in progress"}
            {h.firm.reliance_eligible ? " · eligible for CIP reliance (Path A)" : ""}</p>
        )}
        <p className="small muted">Your {h.contact.team}{h.contact.name ? `: ${h.contact.name} (lead)` : ""} · {h.contact.email} · {h.contact.hours}</p>
        <div className="actions">
          {!client && <Link to="/portal/new-account"><button>Open a new account</button></Link>}
          <Link to="/portal/change"><button className={client ? "" : "secondary"}>Request an account change</button></Link>
          {user.role === "ria_admin" && <Link to="/portal/users"><button className="secondary">Add a firm user</button></Link>}
        </div>
      </section>

      <div className="grid tiles">
        <Link className="tile" to="/portal/accounts"><div className="label">{client ? "Account" : "Accounts open"}</div>
          <div className="value">{h.accounts.open}</div><div className="sub">{h.accounts.pending ? `${h.accounts.pending} opening` : `${h.accounts.total} in total`}</div></Link>
        <Link className="tile" to="/portal/accounts"><div className="label">Requests in progress</div>
          <div className="value">{h.requests_open}</div><div className="sub">Applications and changes</div></Link>
        <div className="tile"><div className="label">Needs your attention</div>
          <div className="value">{h.attention.length}</div><div className={`sub ${h.attention.length ? "alert" : ""}`}>{h.attention.length ? "See below" : "Nothing waiting on you"}</div></div>
        <div className="tile"><div className="label">Restricted accounts</div>
          <div className="value">{h.accounts.restricted}</div><div className="sub">{h.accounts.restricted ? "Activity limited until resolved" : "None"}</div></div>
      </div>

      <h2>Needs your attention</h2>
      {h.attention.length ? h.attention.map((a) => (
        <div key={a.case_id} className="panel attention">
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <strong>{a.title}</strong><Status status={a.status} />
          </div>
          <p className="small muted">{a.why}{a.since ? ` · since ${when(a.since)}` : ""}</p>
          {a.items.length > 0 && <ul className="small">{a.items.map((i) => <li key={i}>{i}</li>)}</ul>}
          <Link to={`/portal/cases/${a.case_id}`}>Open and respond</Link>
        </div>
      )) : <div className="empty">Nothing is waiting on you. We'll show requests for documents or signatures here.</div>}

      <h2>Coming to the portal</h2>
      <ul className="plain coming">
        <li><Link to="/portal/transfers">Transfers</Link> <span className="badge">R2</span> <span className="muted small">Wires, ACH, journals, and ACATS, tracked to settlement</span></li>
        <li><Link to="/portal/trading">Trading</Link> <span className="badge">R3</span> <span className="muted small">{client ? "Orders in your self-directed account" : "Block orders and allocations"}</span></li>
        {!client && <li><Link to="/portal/billing">Fee billing</Link> <span className="badge">R3</span> <span className="muted small">Post your advisory fees, or approve what we calculate</span></li>}
      </ul>
    </>
  );
}
