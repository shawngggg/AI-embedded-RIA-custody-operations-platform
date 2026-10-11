import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type CaseDetail } from "../api";
import { useAuth } from "../auth";
import { Loading, useLoad } from "../util";

type ChangeType = { value: string; label: string; owner_level: boolean };
type PortalAccount = { id: string; display_name: string; group: string; status: string; firm: string; firm_crd: string; address: string | null };

type Party = { name: string; ownership_pct: number; date_of_birth: string; address: string; tax_id: string; country_of_residence: string };
const blankParty: Party = { name: "", ownership_pct: 25, date_of_birth: "", address: "", tax_id: "", country_of_residence: "United States" };

// What each change type asks for. Keys become the change request's details.
const DETAIL_FIELDS: Record<string, [string, string, string?][]> = {
  address: [["new_address", "New residential address", "1 Main St, City, ST 00000"]],
  contact_info: [["email", "New email"], ["phone", "New phone"]],
  trusted_contact: [["trusted_contact_name", "Trusted contact name"], ["trusted_contact_phone", "Trusted contact phone"]],
  beneficiary: [["beneficiaries", "Beneficiaries and shares", "Ana Delgado 50%, Luis Delgado 50%"]],
  registration: [["new_title", "New registration (title)", "Jane Doe TTEE, Doe Family Trust"]],
  power_of_attorney: [["agent_name", "Agent named in the power of attorney"], ["scope", "Scope", "Durable, full"]],
  standing_loa: [["payee", "Payee"], ["purpose", "Purpose", "Monthly transfer to the client's own bank"]],
  bank_instruction: [],
  beneficial_owner: [],
};

export default function NewChange() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const accounts = useLoad(() => api<PortalAccount[]>("/api/portal/accounts"), []);
  const types = useLoad(() => api<ChangeType[]>("/api/portal/change-types"), []);
  const [accountId, setAccountId] = useState("");
  const [changeType, setChangeType] = useState("address");
  const [details, setDetails] = useState<Record<string, string>>({});
  const [bank, setBank] = useState({ name: "", aba: "", account_last4: "" });
  const [party, setParty] = useState<Party>(blankParty);
  const [signature, setSignature] = useState(false);
  const [medallion, setMedallion] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const open = (accounts.data ?? []).filter((a) => a.status === "OPEN");
  useEffect(() => {
    if (!accountId && open.length) setAccountId(open[0].id);
  }, [open, accountId]);
  useEffect(() => { setDetails({}); }, [changeType]);

  if (!accounts.data || !types.data) return <Loading />;
  const account = open.find((a) => a.id === accountId);
  const type = types.data.find((t) => t.value === changeType);
  const client = user.role === "client";
  const wrongChannel = account && !client && account.group === "self_directed";

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    const body = {
      account_id: accountId, change_type: changeType,
      details: Object.fromEntries(Object.entries(details).filter(([, v]) => v.trim())),
      client_signature: signature, medallion_guarantee: medallion,
      new_parties: changeType === "beneficial_owner" && party.name.trim()
        ? [{ ...party, role: "owner", date_of_birth: party.date_of_birth || null, address: party.address || null,
             tax_id: party.tax_id || null }] : [],
      new_bank: changeType === "bank_instruction" ? bank : null,
    };
    try {
      const c = await api<CaseDetail>("/api/portal/changes", { body });
      navigate(`/portal/cases/${c.id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  if (!open.length) return (
    <>
      <h1>Request an account change</h1>
      <div className="empty">No open accounts to change.</div>
    </>
  );

  return (
    <>
      <h1>Request an account change</h1>
      <p className="muted">{client
        ? "Changes to your self-directed account come from you. Changes to who owns or controls the account need your signature."
        : "Your firm is the channel for its pure RIA accounts. Owner-level changes (beneficiaries, title, power of attorney, standing instructions, owners) still need the client's signature."}</p>

      <form onSubmit={submit}>
        <div className="panel">
          <div className="row">
            <div className="field">
              <label htmlFor="acct">Account</label>
              <select id="acct" value={accountId} onChange={(e) => setAccountId(e.target.value)}>
                {open.map((a) => <option key={a.id} value={a.id}>{a.display_name} · {a.id} · {a.group === "self_directed" ? "self-directed" : "pure RIA"}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="ct">What's changing</label>
              <select id="ct" value={changeType} onChange={(e) => setChangeType(e.target.value)}>
                {types.data.map((t) => <option key={t.value} value={t.value}>{t.label}{t.owner_level ? " (owner-level)" : ""}</option>)}
              </select>
            </div>
          </div>
          {account?.address && <p className="small muted">Address on file: {account.address}</p>}
          {wrongChannel && <p className="small" style={{ color: "var(--refer)", fontWeight: 600 }}>This account is self-directed, so the
            client makes its changes. If you submit, the channel check will redirect the request.</p>}
        </div>

        <div className="panel">
          <h2 style={{ marginTop: 0 }}>{type?.label}</h2>
          {DETAIL_FIELDS[changeType]?.map(([key, label, placeholder]) => (
            <div className="field" key={key}>
              <label htmlFor={key}>{label}</label>
              <input id={key} type="text" placeholder={placeholder} value={details[key] ?? ""}
                onChange={(e) => setDetails((d) => ({ ...d, [key]: e.target.value }))} />
            </div>
          ))}

          {changeType === "bank_instruction" && (
            <>
              <div className="row">
                <div className="field"><label htmlFor="bn">Bank name</label>
                  <input id="bn" type="text" value={bank.name} onChange={(e) => setBank({ ...bank, name: e.target.value })} /></div>
                <div className="field"><label htmlFor="aba">ABA routing number</label>
                  <input id="aba" type="text" inputMode="numeric" value={bank.aba} onChange={(e) => setBank({ ...bank, aba: e.target.value })} /></div>
                <div className="field" style={{ flex: "0 1 160px" }}><label htmlFor="l4">Account, last 4</label>
                  <input id="l4" type="text" inputMode="numeric" maxLength={4} value={bank.account_last4}
                    onChange={(e) => setBank({ ...bank, account_last4: e.target.value })} /></div>
              </div>
              <p className="small muted">A new bank link stays inactive until it's verified. If the address or contact details also
                changed in the last 30 days, operations confirms with the RIA and calls the client back first.</p>
            </>
          )}

          {changeType === "beneficial_owner" && (
            <>
              <p className="small muted">The new owner is screened against the flagged list. On a Path B account the custodian
                verifies their identity before the change applies; on Path A it relies on the RIA's verification.</p>
              <div className="row">
                <div className="field"><label htmlFor="pn">New owner's full legal name</label>
                  <input id="pn" type="text" value={party.name} onChange={(e) => setParty({ ...party, name: e.target.value })} /></div>
                <div className="field" style={{ flex: "0 1 130px" }}><label htmlFor="pp">Ownership %</label>
                  <input id="pp" type="number" min={0} max={100} value={party.ownership_pct}
                    onChange={(e) => setParty({ ...party, ownership_pct: Number(e.target.value) })} /></div>
              </div>
              <div className="row">
                <div className="field"><label htmlFor="pd">Date of birth</label>
                  <input id="pd" type="date" value={party.date_of_birth} onChange={(e) => setParty({ ...party, date_of_birth: e.target.value })} /></div>
                <div className="field"><label htmlFor="pt">Tax ID (synthetic)</label>
                  <input id="pt" type="text" value={party.tax_id} onChange={(e) => setParty({ ...party, tax_id: e.target.value })} /></div>
                <div className="field"><label htmlFor="pc">Country of residence</label>
                  <input id="pc" type="text" value={party.country_of_residence}
                    onChange={(e) => setParty({ ...party, country_of_residence: e.target.value })} /></div>
              </div>
              <div className="field"><label htmlFor="pa">Address</label>
                <input id="pa" type="text" value={party.address} onChange={(e) => setParty({ ...party, address: e.target.value })} /></div>
            </>
          )}
        </div>

        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Signatures</h2>
          <label className="check"><input type="checkbox" checked={signature} onChange={(e) => setSignature(e.target.checked)} />
            {client ? "I sign this change request" : "Client-signed change form attached"}
            {type?.owner_level && <span className="pill t-refer" style={{ marginLeft: 6 }}>Required for this change</span>}</label>
          {changeType === "registration" && (
            <label className="check"><input type="checkbox" checked={medallion} onChange={(e) => setMedallion(e.target.checked)} />
              Medallion signature guarantee attached <span className="pill t-refer" style={{ marginLeft: 6 }}>Required for this change</span></label>
          )}
        </div>

        {error && <p className="error" role="alert">{error}</p>}
        <div className="actions">
          <button type="submit" disabled={busy || !accountId}>{busy ? "Submitting…" : "Submit change"}</button>
          <Link to="/portal"><button type="button" className="secondary">Cancel</button></Link>
        </div>
      </form>
    </>
  );
}
