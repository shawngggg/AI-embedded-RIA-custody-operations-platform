"""
Release 2 policy, as data: every threshold a workflow applies, with its version, effective date, and the source it rests
on. A step that applies one names its rule id, and the audit trail resolves the id to this record (app/traceability.py),
so a decision can be traced to the rule version in force.

Amounts and day counts marked "platform policy" are this reference design's choices, not any custodian's terms.
"""
from __future__ import annotations

BASELINE = "2026-01-01"
R2 = "2026-10-01"            # release 2 policies: in force from the start of the quarter the release ships in
REVIEW = "2026-10-25"        # from the industry-practice review

ACATS = "FINRA Rule 11870 (customer account transfers): validate within 1 business day, complete within 3 after"


def _r(rule_id, statement, source, effective=R2, version="1", **params):
    return {"rule_id": rule_id, "version": version, "effective_date": effective, "statement": statement,
            "source": source, "params": params}


RULES = [
    # Ledger
    _r("LED-001", "Every posting is double-entry, and the client accounts roll up to the RIA master accounts and the "
       "RIA masters to the general ledger; a posting that breaks the rollup is rejected",
       "Platform principle 2 (one three-layer ledger); cash settlement map 12"),
    _r("LED-002", "Pending transfers sit in in-transit suspense accounts, so the books balance mid-transfer",
       "Platform principle 3 (in transit is a ledger location); transfers map 08"),
    # Money movement and transfers (map 08)
    _r("MM-001", "The requester must have authority: the RIA on pure RIA accounts (under its LPOA); the client on the "
       "self-directed account (incoming always; outgoing with an RIA grant); a client-signed request on any of the "
       "client's accounts; a receiving firm's ACATS request on any account",
       "Platform principle 5; account model; transfers map 08"),
    _r("MM-002", "An active restriction code that blocks the activity stops the request",
       "Restriction code catalog (onboarding/restrictions.py)"),
    _r("MM-003", "Outgoing cash can't exceed the account's settled, unrestricted cash",
       "Platform policy: returnable ACH funds and pending items aren't available to send"),
    _r("MM-004", "A third-party payee, an address, bank, or contact change in the last 30 days, a deposit of "
       "low-priced securities (under $5), or an outgoing amount over $250,000 goes to a risk review",
       "FINRA Rule 3310 (AML program: monitoring for suspicious activity); platform policy (map 08 risk review)",
       days=30, low_price=5.0, large=250_000),
    _r("MM-005", "Controls gate: every outgoing release needs a maker and a checker; an analyst's dollar authority is "
       "$50,000, above which a supervisor checks; client self-service within its grant, with a passcode and a "
       "verified bank link, releases straight through up to $25,000",
       "Platform policy (map 08 controls gate)", analyst_authority=50_000, straight_through=25_000),
    _r("MM-006", "A callback to the client, on a phone number already on file, before releasing a client-signed "
       "request or any outgoing amount over $100,000",
       "Platform policy (map 08 controls gate)", callback_over=100_000),
    _r("MM-007", "Incoming ACH deposits are held as restricted for 2 business days after settlement, the window in "
       "which most returns (R01 included) arrive",
       "NACHA Operating Rules: most return reasons must be returned within 2 banking days of settlement",
       hold_days=2),
    _r("MM-008", "Rail timing: ACH settles the next business day; Fedwire the same day; journals at once; ACATS "
       "validates within 1 business day and completes within 3 after; an LOI re-registration can take up to 40 "
       "business days, with a follow-up after 20. ACH, Fedwire, and LOI count Federal Reserve business days; ACATS "
       "counts NSCC's (open on Columbus Day and Veterans Day)",
       ACATS + "; Federal Reserve Fedwire and FedACH, and the Federal Reserve holiday schedule; NSCC Important Notice "
       "A#9660 (2026 holiday schedule); LOI timing is a platform assumption",
       effective=REVIEW, version="2", ach_days=1, acats_validate=1, acats_complete=3, loi_days=40, loi_follow_up=20,
       calendars={"ACH": "fed", "Fedwire": "fed", "Journal": "fed", "LOI": "fed", "ACATS": "nscc"}),
    _r("MM-009", "A cash distribution from a traditional IRA withholds 10% for federal tax unless the client elects "
       "otherwise", "IRC 3405(b) (nonperiodic distributions; Form W-4R)", rate=0.10),
    _r("MM-010", "Positions move only after the transferability review; a position the platform can't hold waits for "
       "product acceptance, and a declined product stays at the delivering firm or is liquidated there",
       "Transferability review map 07; product acceptance map 24"),
    _r("MM-011", "An RIA may send a client's money to a third party only under the client's standing letter of "
       "authorization (SLOA): signed by the client, naming the payee and either the payee's address or account, "
       "verified by the custodian, with the payee details exactly as the client gave them (the RIA can't change "
       "them), and a payee who isn't the adviser or at the adviser's address. The custodian confirms a new SLOA in "
       "writing, notifies the client after each transfer, and reconfirms each SLOA every year; the client can end it",
       "SEC Division of Investment Management, Investment Adviser Association no-action letter (Feb. 21, 2017): the "
       "seven conditions under which an SLOA isn't custody under Advisers Act Rule 206(4)-2",
       effective=REVIEW, annual_days=365),
    _r("MM-012", "Fraud monitoring on money going out: a first payment over $10,000 to a destination not on file and "
       "never paid before; an ACH withdrawal over $5,000 to a bank link verified in the last 10 days; 3 or more "
       "outgoing requests within 5 business days; or outgoing money in 30 days over 90% of the account's value. Each "
       "goes to a risk review",
       "Nacha Operating Rules, fraud monitoring by ODFIs and originators (phase 1 March 20, 2026; phase 2 in June "
       "2026): risk-based processes to identify entries that are unauthorized or authorized under false pretenses. "
       "The signals and thresholds are platform policy",
       effective=REVIEW, first_payment_over=10_000, new_link_days=10, new_link_over=5_000, velocity_count=3,
       velocity_bdays=5, drain_days=30, drain_share=0.90),
    _r("MM-013", "Rail cutoffs (Eastern time): a wire released after 5:00 pm or an ACH after 3:00 pm, or any release "
       "on a day the rail is closed, goes out the next business day. The pending entries post at release, so the "
       "money can't be spent twice. Journals book at once: they never leave the custodian",
       "Platform policy, set ahead of the networks' own deadlines: Fedwire takes third-party transfers until "
       "6:45 pm ET and the last same-day ACH window closes at 4:45 pm ET (Federal Reserve, Dec. 23, 2019, effective "
       "March 2021)", effective=REVIEW, cutoffs={"Fedwire": "17:00", "ACH": "15:00"}),
    # Cash settlement (map 12)
    _r("STL-001", "Unapplied incoming cash is matched to an account from the wire memo or ACH addenda; a match at 0.90 "
       "confidence or above applies itself, anything lower waits in suspense for an analyst",
       "Platform policy (map 12)", threshold=0.90),
    _r("STL-002", "Money nobody can match goes back to the sender after 10 business days in suspense",
       "Platform policy (map 12)", return_after=10),
    # Position reconciliation (map 13)
    _r("REC-001", "Client, RIA master, general ledger, and external records (DTC, transfer agents, sponsors, the "
       "settlement bank) must agree: positions exactly, cash to the cent", "Platform policy (map 13)"),
    _r("REC-002", "Breaks route by type and value: a known pattern up to $25,000 is fixed automatically; a classified "
       "break gets a drafted resolution an analyst approves; an unexplained break or one over $100,000 is "
       "investigated by hand", "Platform policy (map 13)", auto_fix_max=25_000, human_over=100_000),
    _r("REC-003", "A break open more than 5 business days, or worth more than $10,000, goes to a supervisor",
       "SEC Rule 17a-13 (securities counts); short differences unresolved after 7 business days are a net capital "
       "deduction under Rule 15c3-1(c)(2)(v); platform policy escalates earlier", age_days=5, value=10_000),
    _r("REC-004", "The books are certified for a day only when no break is open except expected in-transit items",
       "Platform policy (map 13); billing bills only certified periods (map 14)"),
    # Client entitlements (map 05)
    _r("ENT-001", "Only the self-directed account takes client capabilities; retirement accounts get no borrowing or "
       "short selling; no grant while a code blocks the activity; options levels and margin stay custodian "
       "approvals; every grant has limits, approved destinations, and an expiry of a year or less",
       "Platform principle 5; client entitlements map 05", max_days=366),
    _r("ENT-002", "Every client request needs a one-time passcode and goes only to a verified bank link the grant "
       "approves, within the grant's per-request and daily limits", "Client entitlements map 05"),
    _r("ENT-003", "The RIA is alerted when a client request is over half the account's value, or the third in 5 "
       "business days; the RIA decides whether to revoke", "Client entitlements map 05", share=0.5, count=3, days=5),
    # Product acceptance (map 24)
    _r("PA-001", "Operations reviews each product request and recommends; risk and compliance decide and own the "
       "acceptance policy", "Product acceptance map 24"),
    _r("PA-002", "A mutual fund needs a Fund/SERV-eligible fund agreement and a Rule 22c-2 shareholder information "
       "agreement; an ETF needs DTC eligibility", "SEC Rule 22c-2; NSCC Fund/SERV; DTC eligibility"),
    _r("PA-003", "An alternative needs a sponsor that passes the flagged-list and adverse-media checks, audited "
       "financials, registration in the custodian's name, an administrator, a valuation source, and, for capital "
       "calls, a funding process", "Product acceptance map 24"),
    _r("PA-004", "A valuation more than 95 days old, or more than 20% away from the prior one, is checked by a person "
       "before it posts; illiquid products show the last value with its as-of date",
       "FINRA Rule 2231 (per-share estimated values for non-listed REITs and DPPs); platform policy",
       stale_days=95, change=0.20),
    # Account lifecycle (maps 16, 17, 22, 23)
    _r("OFF-001", "An account that's closing takes an outgoing-only code: no new purchases or deposits",
       "Client offboarding map 16"),
    _r("OFF-002", "The final advisory fee is pro-rated and deducted, or refunded if billed in advance, before the "
       "outgoing transfer validates, because validation freezes the account", ACATS + "; map 16 and map 22"),
    _r("OFF-003", "An account closes only at zero at all three layers; residual credits within 90 days follow the "
       "client to the receiving firm, and undeliverable residuals move to the unclaimed-property liability",
       "Client offboarding map 16; escheatment map 18 (platform policy for the 90-day window)", residual_days=90),
    _r("DEC-001", "Credible notice of a death restricts the account at once and ends the RIA's trading authority; a "
       "verified death certificate makes it a full lockdown",
       "Agency ends at the principal's death; deceased client account map 17"),
    _r("DEC-002", "Clear ownership (a TOD or beneficiary designation, or a surviving joint owner) settles on claim "
       "documents; otherwise the estate's representative is identified from letters testamentary or administration, "
       "or small-estate documents, under state law", "Deceased client account map 17"),
    _r("DEC-003", "Inherited positions take a date-of-death basis", "IRC 1014 (basis of property acquired from a "
       "decedent)"),
    _r("TRN-001", "Clients authorize a move to the platform themselves, with signed applications, LPOAs, and transfer "
       "forms; negative consent isn't available", "RIA firm transitions map 22"),
    _r("TRN-002", "The delivering firm sends cost-basis transfer statements within 15 days of settlement",
       "IRC 6045A; Treas. Reg. 1.6045A-1", days=15),
    _r("TRN-003", "Billing starts from each account's funding date, with a pro-rated first period",
       "RIA firm transitions map 22"),
    _r("TRN-004", "When an RIA leaves, residual credits are swept to the receiving firms for at least 6 months",
       "RIA firm transitions map 22", months=6),
    _r("ADV-001", "An LPOA isn't transferable: a new RIA needs a new client-signed LPOA, the old RIA can't block the "
       "move, and only a documented dispute or court order holds it", "Advisor moves and mergers map 23"),
    _r("ADV-002", "Fee cutover: the old RIA bills through the effective date, the new RIA from the re-link",
       "Advisor moves and mergers map 23"),
    _r("MRG-001", "A change of control assigns the advisory contracts, which needs client consent: new LPOAs or an "
       "approved negative-consent package; a rebrand or reorganization without one keeps the LPOAs",
       "Investment Advisers Act sections 202(a)(1) and 205(a)(2); negative consent is common practice, not codified"),
]

BY_ID = {r["rule_id"]: r for r in RULES}


def param(rule_id: str, name: str):
    return BY_ID[rule_id]["params"][name]


def library() -> list[dict]:
    """The rules in the shape the traceability library reads (rule_id, version, effective_date, source)."""
    return [{"rule_id": r["rule_id"], "version": r["version"], "effective_date": r["effective_date"],
             "source": r["source"], "statement": r["statement"]} for r in RULES]
