import { useAuth } from "../auth";

/** Portal features the PRD places in later releases: shown as planned, with what they'll do, not as working forms. */
export type PlannedKey = "transfers" | "trading" | "billing";

const VIEWER = "https://shawngggg.github.io/AI-embedded-RIA-custody-operations-platform/";

type Plan = {
  title: string; release: 2 | 3; lead: string; does: string[]; who: string; client?: string; ai: string;
  map: [string, string]; why: string;
};

export const PLANS: Record<PlannedKey, Plan> = {
  transfers: {
    title: "Money movement and transfers", release: 2,
    lead: "Wires, ACH, journals, and ACATS or non-ACATS transfers, requested here and tracked until they settle.",
    does: [
      "Request wires, ACH, journals, and ACATS or non-ACATS transfers, and track each one's status",
      "Check the requester's permission and run the flagged-list check on every party",
      "Review the transferability of assets coming in or going out, and review deposits of low-priced securities",
      "Pass a controls gate before money moves: dollar authority, maker-checker, RIA confirmation, a one-time passcode from the client, or a callback on a signed request",
      "Post pending and suspense entries, route to the right payment rail, and wait for confirmation, a reject, or a timeout",
    ],
    who: "RIA users, on the firm's pure RIA accounts.",
    client: "You, on your self-directed account: deposits, and any outgoing movement your RIA has granted.",
    ai: "Reads transfer requests and forms, predicts likely rejects, and sorts exceptions for review.",
    map: ["tr", "Asset and money transfers"],
    why: "Every transfer posts to the three-layer ledger (client account, RIA master account, custodian general ledger), and the ledger is built in release 2. A transfer screen without it would take requests that go nowhere.",
  },
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

export default function Planned({ which }: { which: PlannedKey }) {
  const { user } = useAuth();
  const p = PLANS[which];
  const client = user.role === "client";
  return (
    <>
      <div className="planned-head">
        <h1>{p.title}</h1>
        <span className="pill t-quiet">Release {p.release} · planned</span>
      </div>
      <p className="muted lead">{p.lead} It isn't built yet: the product plan puts it in release {p.release}.</p>
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
          <a href={`${VIEWER}#${p.map[0]}`} target="_blank" rel="noreferrer">{p.map[1]} process map</a>.</p>
      </div>
    </>
  );
}
