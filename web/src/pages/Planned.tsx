import { useAuth } from "../auth";
import Preview from "./Preview";

/**
 * Features the PRD places in later releases: a read-only preview on the synthetic book, then what each will do. No
 * working forms: nothing here changes anything.
 */
export type PlannedKey = "trading" | "billing";
export type OpsPlannedKey = PlannedKey;

const VIEWER = "https://shawngggg.github.io/AI-embedded-RIA-custody-operations-platform/";

type Plan = {
  title: string; release: 2 | 3; lead: string; does: string[]; who: string; client?: string; ai: string;
  map: [string, string]; map2?: [string, string]; why: string;
};

export const PLANS: Record<PlannedKey, Plan> = {
  trading: {
    title: "Trading", release: 3,
    lead: "Block orders and allocations from the RIA, and orders from the client in the self-directed account.",
    does: [
      "Submit block orders and allocations in the portal, or over FIX",
      "Execute every order in-house (no trade-aways) and allocate fills at one average price",
      "Enforce who may order: the RIA on pure RIA accounts, the client only in the self-directed account",
      "Enforce the trading permissions the RIA set; the custodian approves options levels and margin and supervises self-directed trading",
      "Reject borrowing and short sales in retirement accounts, settle by asset class, and cure fails",
    ],
    who: "RIA users, on the firm's pure RIA accounts.",
    client: "You, in your self-directed account.",
    ai: "Screens orders for anomalies, matches confirmations, and sorts settlement fails.",
    map: ["tp", "Trade processing"],
    why: "Orders are checked against positions and settled cash, and they settle through the ledger. Those arrive in release 2, so trading follows in release 3, along with options approval by a Registered Options Principal.",
  },
  billing: {
    title: "Fee billing", release: 3,
    lead: "Advisory and custody fees: post your firm's own advisory fees, or approve what the custodian calculates before it posts.",
    does: [
      "Post your own advisory fees, under your agreement with the client; the custodian needs your good-faith attestation, not a billing agreement",
      "Or send a fee file, or share your fee structure for the custodian to calculate",
      "Review and approve every fee the custodian will post, advisory or custody, before it posts",
      "Bill only periods that reconciliation has certified, with reasonableness checks, pro-ration, and refunds when an account closes mid-period",
      "Retrieve billing results and the fee report",
    ],
    who: "RIA users. Clients see each fee debit on their statements.",
    ai: "Parses fee schedules at setup and flags fees that look out of line.",
    map: ["ab", "Advisor billing"],
    why: "Fees debit through the ledger and bill only a period that reconciliation has certified, so billing follows both. It builds on an existing fee calculation engine.",
  },
};

// The same features from the operations side (Shawn's walkthrough, Oct. 13, 2026: ops needs to see them coming)
export const OPS_PLANS: Record<OpsPlannedKey, Plan> = {
  trading: {
    title: "Trade operations", release: 3,
    lead: "The trade team's workbench: orders, allocations, settlement, and fails.",
    does: [
      "Block orders and their allocations at one average price, matched to executions and confirmations",
      "Settlement by asset class: NSCC and DTC for equities and ETFs, OCC for options, Fund/SERV for mutual funds",
      "A fails queue with cure actions: re-instruct, buy in, or contact the counterparty, with Reg SHO close-out dates",
      "Options approvals by a Registered Options Principal (FINRA Rule 2360) and supervision of self-directed trading",
      "Breaks escalated by age and dollar value",
    ],
    who: "The trade team, with registered principals on the RIA service team for options approval and supervision.",
    ai: "Screens orders for anomalies, matches confirmations, and sorts settlement fails.",
    map: ["tp", "Trade processing"],
    why: "Trading needs positions, settled cash, and settlement from release 2, so it follows in release 3.",
  },
  billing: {
    title: "Billing operations", release: 3,
    lead: "The billing team's workbench: fee setup, fee runs, RIA approvals, and refunds.",
    does: [
      "Fee schedule setup and approval, with the client's fee authorization and, when the RIA posts its own fees, its good-faith attestation",
      "Period-end runs for advisory and custody fees, after reconciliation certifies the books",
      "Reasonableness checks and AI-flagged anomalies reviewed with the RIA",
      "Tracking of RIA approval for every fee the custodian will post, then maker-checker before debits post",
      "Refunds and corrections, including pro-rated refunds when an account closes mid-period",
    ],
    who: "The billing team, with the supervisor as checker on each fee run.",
    ai: "Parses fee schedules at setup and flags fees that look out of line against the account's value and history.",
    map: ["ab", "Advisor billing"],
    why: "Fees debit through the ledger and bill only certified periods, so billing follows the ledger and reconciliation.",
  },
};

export default function Planned({ which, audience = "portal" }: { which: OpsPlannedKey; audience?: "portal" | "ops" }) {
  const { user } = useAuth();
  const p = audience === "ops" ? OPS_PLANS[which] : PLANS[which as PlannedKey];
  const client = user.role === "client";
  return (
    <>
      <div className="planned-head">
        <h1>{p.title}</h1>
        <span className="pill t-quiet">Release {p.release} · planned</span>
      </div>
      <p className="muted lead">{p.lead} The workflow isn't built yet: the product plan puts it in release {p.release}.
        Below is a read-only preview on synthetic data, then the plan.</p>
      <Preview which={which} audience={audience} />
      <h2>The plan</h2>
      <div className="panel">
        <h2 style={{ marginTop: 0 }}>What it will do</h2>
        <ul className="planned-list">{p.does.map((d) => <li key={d}>{d}</li>)}</ul>
      </div>
      <div className="planned-grid">
        <div className="panel"><h3>Who uses it</h3><p>{client && p.client ? p.client : p.who}</p></div>
        <div className="panel"><h3>Where AI helps</h3><p>{p.ai} People and rules make the decisions.</p></div>
      </div>
      <div className="panel">
        <h3>Why it isn't here yet</h3>
        <p>{p.why}</p>
        <p className="small" style={{ marginBottom: 0 }}>The process is already mapped:{" "}
          <a href={`${VIEWER}#${p.map[0]}`} target="_blank" rel="noreferrer">{p.map[1]} process map</a>
          {p.map2 && <>{" "}and the{" "}
            <a href={`${VIEWER}#${p.map2[0]}`} target="_blank" rel="noreferrer">{p.map2[1]} process map</a></>}.</p>
      </div>
    </>
  );
}
