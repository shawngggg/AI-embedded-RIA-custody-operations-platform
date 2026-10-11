import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type CaseDetail } from "../api";
import { useAuth } from "../auth";

type Holder = {
  name: string; role: string; ownership_pct: number; date_of_birth: string; address: string; tax_id: string;
  country_of_residence: string; nationality: string; occupation: string; is_pep: boolean;
};

const blankHolder = (role = "account_holder"): Holder => ({
  name: "", role, ownership_pct: 0, date_of_birth: "", address: "", tax_id: "",
  country_of_residence: "United States", nationality: "United States", occupation: "", is_pep: false,
});

const ENTITY_TYPES = ["trust", "llc", "corporation"];

const ROLES: [string, string][] = [
  ["account_holder", "Account holder"], ["owner", "Beneficial owner (25% or more)"],
  ["control_person", "Control person"], ["trustee", "Trustee"], ["authorized_signer", "Authorized signer"],
];

type Form = {
  group: "pure_ria" | "self_directed"; registration_type: string; holders: Holder[];
  entity_name: string; entity_ein: string; entity_type: string; entity_formation_documents: boolean;
  beneficial_ownership_certification: boolean; first_entity_account: boolean; bo_info_confirmed_current: boolean;
  client_signature: boolean; lpoa_signed: boolean; fee_authorization: boolean; tax_form: string;
  account_purpose: string; source_of_funds: string; source_of_wealth: string; expected_initial_funding: number;
  third_party_funding: boolean;
};

const blank: Form = {
  group: "pure_ria", registration_type: "individual", holders: [blankHolder()],
  entity_name: "", entity_ein: "", entity_type: "operating_company", entity_formation_documents: false,
  beneficial_ownership_certification: false, first_entity_account: true, bo_info_confirmed_current: false,
  client_signature: false, lpoa_signed: false, fee_authorization: false, tax_form: "",
  account_purpose: "", source_of_funds: "", source_of_wealth: "", expected_initial_funding: 0, third_party_funding: false,
};

// A clean individual application, so the demo can show straight-through processing in one click.
const sample: Form = {
  ...blank,
  holders: [{
    ...blankHolder(), name: "Theo Lindqvist", date_of_birth: "1988-09-14", address: "77 Canyon Rd, Park City, UT 84060",
    tax_id: "900-00-3101", occupation: "Architect",
  }],
  client_signature: true, lpoa_signed: true, fee_authorization: true, tax_form: "W-9",
  account_purpose: "Long-term investing", source_of_funds: "Employment income", source_of_wealth: "Salary and savings",
  expected_initial_funding: 320000,
};

export default function NewAccount() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [f, setF] = useState<Form>(blank);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const entity = ENTITY_TYPES.includes(f.registration_type);
  const pure = f.group === "pure_ria";

  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((x) => ({ ...x, [k]: v }));
  const setHolder = (i: number, patch: Partial<Holder>) =>
    setF((x) => ({ ...x, holders: x.holders.map((h, j) => (j === i ? { ...h, ...patch } : h)) }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    const holders = f.holders.filter((h) => h.name.trim()).map((h) => ({
      ...h, date_of_birth: h.date_of_birth || null, address: h.address || null, tax_id: h.tax_id || null,
      occupation: h.occupation || null, pep_type: h.is_pep ? "foreign" : null,
    }));
    const application: Record<string, unknown> = {
      group: f.group, registration_type: f.registration_type, holders,
      client_signature: f.client_signature, lpoa_signed: pure && f.lpoa_signed, fee_authorization: pure && f.fee_authorization,
      tax_form: f.tax_form || null, account_purpose: f.account_purpose || null, source_of_funds: f.source_of_funds || null,
      source_of_wealth: f.source_of_wealth || null, expected_initial_funding: Number(f.expected_initial_funding) || 0,
      third_party_funding: f.third_party_funding,
    };
    if (entity) {
      Object.assign(application, {
        entity_name: f.entity_name || null, entity_ein: f.entity_ein || null,
        entity_type: f.registration_type === "trust" ? "trust" : f.entity_type,
        entity_formation_documents: f.entity_formation_documents,
        beneficial_ownership_certification: f.beneficial_ownership_certification,
        first_entity_account: f.first_entity_account, bo_info_confirmed_current: f.bo_info_confirmed_current,
      });
    }
    try {
      const c = await api<CaseDetail>("/api/portal/accounts", { body: { application } });
      navigate(`/portal/cases/${c.id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <>
      <h1>New account application</h1>
      <p className="muted">Submitted under {user.firm?.name}. The rules engine checks it as soon as you submit: anything
        missing comes back to you as a list of items, and a clean application opens without anyone keying it.</p>
      <div className="actions" style={{ marginBottom: 16 }}>
        <button type="button" className="secondary" onClick={() => setF(sample)}>Fill with a clean sample</button>
        <button type="button" className="secondary" onClick={() => setF(blank)}>Clear</button>
      </div>

      <form onSubmit={submit}>
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Account</h2>
          <div className="row">
            <div className="field">
              <label htmlFor="group">Account group</label>
              <select id="group" value={f.group} onChange={(e) => set("group", e.target.value as Form["group"])}>
                <option value="pure_ria">Pure RIA (you manage it under an LPOA)</option>
                <option value="self_directed">Self-directed (the client trades it)</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="reg">Registration</label>
              <select id="reg" value={f.registration_type} onChange={(e) => set("registration_type", e.target.value)}>
                <option value="individual">Individual</option>
                <option value="joint">Joint</option>
                <option value="ira">IRA</option>
                <option value="trust">Trust</option>
                <option value="llc">LLC</option>
                <option value="corporation">Corporation</option>
              </select>
            </div>
          </div>
          {!pure && <p className="small muted">Self-directed accounts need no LPOA or fee authorization. After opening, the
            client makes changes to the account and your firm sees it view-only.</p>}
        </div>

        {entity && (
          <div className="panel">
            <h2 style={{ marginTop: 0 }}>Legal entity</h2>
            <div className="row">
              <div className="field"><label htmlFor="en">Entity name</label>
                <input id="en" type="text" value={f.entity_name} onChange={(e) => set("entity_name", e.target.value)} /></div>
              <div className="field"><label htmlFor="ein">EIN</label>
                <input id="ein" type="text" placeholder="87-5550000" value={f.entity_ein} onChange={(e) => set("entity_ein", e.target.value)} /></div>
              {f.registration_type !== "trust" && (
                <div className="field"><label htmlFor="et">Entity type</label>
                  <select id="et" value={f.entity_type} onChange={(e) => set("entity_type", e.target.value)}>
                    <option value="operating_company">Operating company</option>
                    <option value="private_investment_company">Private investment company</option>
                    <option value="shell">Shell company</option>
                  </select></div>
              )}
            </div>
            <label className="check"><input type="checkbox" checked={f.entity_formation_documents}
              onChange={(e) => set("entity_formation_documents", e.target.checked)} />
              {f.registration_type === "trust" ? "Trust instrument attached" : "Formation documents attached"}</label>
            <label className="check"><input type="checkbox" checked={f.first_entity_account}
              onChange={(e) => set("first_entity_account", e.target.checked)} />First account for this entity at the custodian</label>
            <label className="check"><input type="checkbox" checked={f.bo_info_confirmed_current}
              onChange={(e) => set("bo_info_confirmed_current", e.target.checked)} />Client confirmed the beneficial ownership on file is current</label>
            <label className="check"><input type="checkbox" checked={f.beneficial_ownership_certification}
              onChange={(e) => set("beneficial_ownership_certification", e.target.checked)} />Beneficial ownership certification attached</label>
            <p className="small muted">Since FinCEN's February 13, 2026 relief, the certification is collected for the
              entity's first account, or when the client can't confirm the information on file is current.</p>
          </div>
        )}

        <div className="panel">
          <h2 style={{ marginTop: 0 }}>{entity ? "Owners, control person, and signers" : "Account holders"}</h2>
          {f.holders.map((h, i) => (
            <fieldset key={i} style={{ border: "1px solid var(--line)", borderRadius: 8, padding: "12px 14px", marginBottom: 12 }}>
              <legend className="small muted" style={{ padding: "0 6px" }}>Person {i + 1}</legend>
              <div className="row">
                <div className="field"><label htmlFor={`n${i}`}>Full legal name</label>
                  <input id={`n${i}`} type="text" value={h.name} onChange={(e) => setHolder(i, { name: e.target.value })} /></div>
                {entity && (
                  <div className="field"><label htmlFor={`r${i}`}>Role</label>
                    <select id={`r${i}`} value={h.role} onChange={(e) => setHolder(i, { role: e.target.value })}>
                      {ROLES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </select></div>
                )}
                {entity && (
                  <div className="field" style={{ flex: "0 1 130px" }}><label htmlFor={`p${i}`}>Ownership %</label>
                    <input id={`p${i}`} type="number" min={0} max={100} value={h.ownership_pct}
                      onChange={(e) => setHolder(i, { ownership_pct: Number(e.target.value) })} /></div>
                )}
              </div>
              <div className="row">
                <div className="field"><label htmlFor={`d${i}`}>Date of birth</label>
                  <input id={`d${i}`} type="date" value={h.date_of_birth} onChange={(e) => setHolder(i, { date_of_birth: e.target.value })} /></div>
                <div className="field"><label htmlFor={`t${i}`}>Tax ID (synthetic)</label>
                  <input id={`t${i}`} type="text" placeholder="900-00-0000" value={h.tax_id} onChange={(e) => setHolder(i, { tax_id: e.target.value })} /></div>
                <div className="field"><label htmlFor={`o${i}`}>Occupation</label>
                  <input id={`o${i}`} type="text" value={h.occupation} onChange={(e) => setHolder(i, { occupation: e.target.value })} /></div>
              </div>
              <div className="field"><label htmlFor={`a${i}`}>Residential address</label>
                <input id={`a${i}`} type="text" value={h.address} onChange={(e) => setHolder(i, { address: e.target.value })} /></div>
              <div className="row">
                <div className="field"><label htmlFor={`c${i}`}>Country of residence</label>
                  <input id={`c${i}`} type="text" value={h.country_of_residence} onChange={(e) => setHolder(i, { country_of_residence: e.target.value })} /></div>
                <div className="field"><label htmlFor={`na${i}`}>Nationality</label>
                  <input id={`na${i}`} type="text" value={h.nationality} onChange={(e) => setHolder(i, { nationality: e.target.value })} /></div>
              </div>
              <label className="check"><input type="checkbox" checked={h.is_pep} onChange={(e) => setHolder(i, { is_pep: e.target.checked })} />
                Holds or held a senior public role (politically exposed person)</label>
              {f.holders.length > 1 && (
                <button type="button" className="secondary small" onClick={() => set("holders", f.holders.filter((_, j) => j !== i))}>Remove person</button>
              )}
            </fieldset>
          ))}
          <button type="button" className="secondary small"
            onClick={() => set("holders", [...f.holders, blankHolder(entity ? "owner" : "account_holder")])}>Add a person</button>
        </div>

        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Signed documents</h2>
          <label className="check"><input type="checkbox" checked={f.client_signature} onChange={(e) => set("client_signature", e.target.checked)} />
            Client-signed account application</label>
          {pure && <label className="check"><input type="checkbox" checked={f.lpoa_signed} onChange={(e) => set("lpoa_signed", e.target.checked)} />
            Limited power of attorney for your firm</label>}
          {pure && <label className="check"><input type="checkbox" checked={f.fee_authorization} onChange={(e) => set("fee_authorization", e.target.checked)} />
            Written authorization to deduct advisory fees</label>}
          <div className="field" style={{ maxWidth: 260 }}>
            <label htmlFor="tax">Tax certification</label>
            <select id="tax" value={f.tax_form} onChange={(e) => set("tax_form", e.target.value)}>
              <option value="">Not attached</option>
              <option value="W-9">W-9 (US person)</option>
              <option value="W-8BEN">W-8BEN (foreign individual)</option>
              <option value="W-8BEN-E">W-8BEN-E (foreign entity)</option>
            </select>
          </div>
        </div>

        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Purpose and funding</h2>
          <div className="row">
            <div className="field"><label htmlFor="pu">Account purpose</label>
              <input id="pu" type="text" value={f.account_purpose} onChange={(e) => set("account_purpose", e.target.value)} /></div>
            <div className="field"><label htmlFor="fu">Expected initial funding (USD)</label>
              <input id="fu" type="number" min={0} step={1000} value={f.expected_initial_funding}
                onChange={(e) => set("expected_initial_funding", Number(e.target.value))} /></div>
          </div>
          <div className="row">
            <div className="field"><label htmlFor="sf">Source of funds</label>
              <input id="sf" type="text" value={f.source_of_funds} onChange={(e) => set("source_of_funds", e.target.value)} /></div>
            <div className="field"><label htmlFor="sw">Source of wealth</label>
              <input id="sw" type="text" value={f.source_of_wealth} onChange={(e) => set("source_of_wealth", e.target.value)} /></div>
          </div>
          <label className="check"><input type="checkbox" checked={f.third_party_funding} onChange={(e) => set("third_party_funding", e.target.checked)} />
            Funded by someone other than the account holder</label>
        </div>

        {error && <p className="error" role="alert">{error}</p>}
        <div className="actions">
          <button type="submit" disabled={busy || !f.holders.some((h) => h.name.trim())}>{busy ? "Submitting…" : "Submit application"}</button>
          <Link to="/portal"><button type="button" className="secondary">Cancel</button></Link>
        </div>
      </form>
    </>
  );
}
