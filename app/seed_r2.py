"""
Release 2 demo data: the security master, verified bank links, the opening ledger, and money movement in every state
the workbench handles, all built from the synthetic book (synthetic/book.py) so the ledger holds the positions the
trading and billing previews show.

- Accounts opened more than 15 business days ago carry their book positions and cash as an opening-balance entry
  (a conversion to the release 2 ledger). Newer accounts were funded the way real ones are: an opening ACH deposit and
  an ACATS transfer in, each a real case the settlement cycle settles on its dates. Trusts holding the non-traded
  REIT get it by an LOI re-registration.
- Then a transfer in each state: waiting for release, for a callback, for a risk review, for the sanctions team, for a
  fix, for a correction after a reject; overdue; in transit; completed; returned (R01, R03); blocked by a code.
- Money at the bank with no request (receipts), and differences the street reports (street items).
- Finally the day's settlement cycle and reconciliation run, as the settlement team would.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from synthetic import book as sb
from transferability import SECURITY_MASTER

from custody.calendar import bday
from custody.ledger import CASH, LOCATION_GL, client_move, units

from . import audit, ledger, recon, settlement
from .book import open_accounts
from .config import settings
from .orm import Account, BankLink, Case, PendingDecision, Security, User, utcnow
from .servicing import codes_on as servicing_codes, new_case
from .traceability import stamp

# Prices for the transferability rules' synthetic products (only SYN- ids: no made-up price for a real security)
SYN_PRICES = {"SYN-MF-03": 18.42, "SYN-PRV-01": 10.25, "SYN-REIT-01": 9.62, "SYN-LP-02": 1000.00,
              "SYN-ANN-05": 1.00, "SYN-RST-04": 12.10, "SYN-NWF-B": 7.75}
LOCATION_OF = {"mutual_fund": "Fund/SERV (transfer agent)", "alternative": "Sponsor (non-traded)",
               "annuity": "Sponsor (non-traded)"}
SETTLES = {"equity": "DTC (NSCC CNS)", "etf": "DTC (NSCC CNS)", "bond": "DTC", "mutual_fund": "Fund/SERV",
           "alternative": "Sponsor subscription", "annuity": "Insurer"}


def seed_securities(db: Session, today: date) -> None:
    for s in sb.SECURITIES:
        rec = SECURITY_MASTER.get(s.cusip)
        db.add(Security(id=s.cusip, ticker=s.ticker, name=s.name, asset_type=s.asset_type,
                        price=round(s.per_unit, 6), price_as_of=today, location=s.location, accepted=True,
                        dtc_eligible=s.location == "DTC", fundserv_eligible=s.asset_type == "mutual_fund",
                        sponsor_agreement=s.asset_type == "alternative", sponsor=rec.sponsor if rec else None,
                        settlement_method=SETTLES[s.asset_type],
                        valuation_source="Sponsor's quarterly NAV" if s.asset_type == "alternative" else
                        "Pricing vendor (synthetic)"))
    have = {s.cusip for s in sb.SECURITIES}
    for sid, rec in SECURITY_MASTER.items():
        if sid in have or not sid.startswith("SYN-"):
            continue
        db.add(Security(id=sid, name=rec.description, asset_type=rec.asset_type, price=SYN_PRICES.get(sid, 0.0),
                        price_as_of=today, location=LOCATION_OF.get(rec.asset_type, "DTC"),
                        accepted=rec.accepted_on_platform, dtc_eligible=rec.dtc_eligible,
                        fundserv_eligible=rec.fundserv_eligible, sponsor_agreement=rec.sponsor_agreement,
                        proprietary_to=rec.proprietary_to, restricted_legend=rec.restricted, sponsor=rec.sponsor,
                        isin=rec.isin, settlement_method=SETTLES.get(rec.asset_type)))
    db.flush()


def seed_bank_links(db: Session) -> dict[str, str]:
    """A verified bank link for every open account, the same bank the book's money movement uses."""
    out = {}
    for a in db.scalars(select(Account).where(Account.status == "OPEN")).all():
        r = sb._rng("money", a.id)
        bank, last4 = r.choice(sb.BANKS), str(r.randint(1000, 9999))
        link = BankLink(id=f"BL-{a.id[-4:]}", account_id=a.id, bank=bank, last4=last4, owner=a.display_name,
                        method="micro_deposits", status="verified", added_by="Conversion (release 2)",
                        verified_at=utcnow() - timedelta(days=90))      # long verified: MM-012 reads the date
        db.add(link)
        out[a.id] = link.id
    db.flush()
    return out


def _approve(db: Session, case: Case, decision_type: str, value: str, ref: str, maker: User, checker: User,
             note: str) -> None:
    """A seeded maker-checker decision, recorded exactly as the approvals page records one."""
    from .runner import run_case
    d = PendingDecision(case_id=case.id, decision_type=decision_type, value=value, reference=ref, note=note,
                        proposed_by=maker.id, status="approved", decided_by=checker.id, decided_at=utcnow())
    db.add(d)
    db.flush()
    audit.record(db, maker, "decision_proposed", "case", case.id, case_id=case.id, decision_type=decision_type,
                 value=value, reference=ref, **stamp(case, decision_type))
    audit.record(db, checker, "decision_approved", "case", case.id, case_id=case.id, decision_type=decision_type,
                 value=value, reference=ref, note=note, proposed_by=maker.id, **stamp(case, decision_type))
    case.decisions = {**(case.decisions or {}), decision_type: value, f"{decision_type}_reference": ref}
    run_case(db, case, checker)


def _when(d: date) -> datetime:
    return datetime.combine(d, time(15, 0), tzinfo=timezone.utc)


def bank_on_file(account: Account, institution: str, last4: str) -> None:
    """The client's own bank account, on file since opening: a wire there isn't a first payment to a new destination
    (MM-012)."""
    app = dict(account.application or {})
    app["bank_instructions"] = list(app.get("bank_instructions") or []) + [
        {"name": institution, "account_last4": last4, "status": "verified", "source": "Account opening"}]
    account.application = app


def seed_sloa(db: Session, account: Account, details: dict, ops: User, supervisor: User) -> Case:
    """A standing letter of authorization the client signed and mailed in: client service keys it, checks the signed
    form (the signature review), and the change applies (custody MM-011)."""
    import uuid
    from .documents import mark, sample_document, store
    from .runner import run_case
    from .seed import create_change_case
    change = {"request_id": f"CHG-{uuid.uuid4().hex[:6].upper()}", "account_id": account.id,
              "account_group": account.group, "account_firm": ledger.firm_of(db)[account.id],
              "change_type": "standing_loa", "submitted_by_role": "ops_analyst", "details": details,
              "client_signature": True, "recent_changes": account.change_history or [], "path": account.path or "B"}
    case = create_change_case(db, change, account, ops)
    filename, data = sample_document(db, case, "sloa", settings.today())
    doc = store(db, case, None, "sloa", filename, data, "mail", synthetic=True, actor_name=ops.display_name)
    mark(db, doc, "in_good_order", ops.display_name, ops, "Signature compared with the account application")
    run_case(db, case, ops)
    db.flush()
    if case.status == "PENDING_CONFIRMATION":
        _approve(db, case, "confirmation", "confirmed", "SLOA-SEED-01", ops, supervisor,
                 "Signature matches the account application; client confirmed the payee on a callback to the number "
                 "on file")
    return case


def transfer(db: Session, kind: str, account: Account, request: dict, actor: User | None, *, submitted: date | None = None,
             simulate: dict | None = None) -> Case:
    """Open a transfer case as its requester would, already submitted on `submitted` when given."""
    from .runner import run_case
    from custody import money
    app = {"type": kind, "account_id": account.id, **request}
    c = new_case(db, "transfer", f"{money.TYPES[kind]['label']}: {account.display_name}", app, actor, account=account)
    events = [{"type": "submitted", "n": 1, "on": submitted.isoformat()}] if submitted else []
    c.extra = {"events": events, **({"simulate": simulate} if simulate else {})}
    if submitted:
        c.created_at = _when(submitted)
    run_case(db, c, actor)
    db.flush()
    return c


def seed_release2(db: Session, users: dict) -> None:
    today = settings.today()
    seed_securities(db, today)
    links = seed_bank_links(db)
    accounts, firms = open_accounts(db)
    book = sb.build(accounts, firms, today)
    by_name = {s.name: s.cusip for s in sb.SECURITIES} | {r.description: k for k, r in SECURITY_MASTER.items()}
    rows = {r["account"]: r for r in book["accounts"]}
    held: dict[str, list[dict]] = {}
    for p in book["positions"]:
        held.setdefault(p["account"], []).append(p)
    acats_in = {t["account"]: t for t in book["transfers"] if t["id"].startswith("ACATS-IN")}
    lois = {t["account"]: t for t in book["transfers"] if t["id"].startswith("LOI")}
    leaving = next((t for t in book["transfers"] if t["id"].startswith("ACATS-OUT")), None)
    firm_of = ledger.firm_of(db)
    ops = users.get("ops.transfers")
    sup = users["ops.supervisor"]
    ria_for = {"310001": users.get("granite.advisor"), "310003": users.get("bonneville.advisor")}

    # 1. The opening ledger: older accounts by conversion, newer ones by deposit and ACATS
    for acct_id, row in sorted(rows.items()):
        a = db.get(Account, acct_id)
        if acct_id in acats_in:
            continue
        legs = []
        for p in held.get(acct_id, []):
            if acct_id in lois and p["cusip"] == "SYN-REIT-01":
                continue                                    # arrives by its own LOI below
            legs += client_move(acct_id, firm_of[acct_id], p["cusip"], units(p["quantity"]), "settled",
                                LOCATION_GL[p["location"]])
        if row["cash"] > 0:
            legs += client_move(acct_id, firm_of[acct_id], CASH, units(row["cash"]), "settled", "1010")
        if legs:
            opened = min(a.opened_at.date() if a.opened_at else today, today)
            ledger.post(db, f"OPEN-{acct_id}", "opening_balance", "Opening balance: positions and cash brought onto "
                        "the release 2 ledger (synthetic conversion)", legs, opened, account_id=acct_id, firms=firm_of)
    for acct_id, t in sorted(acats_in.items()):
        a = db.get(Account, acct_id)
        who = {"role": "client"} if a.group == "self_directed" else {"role": "ria"}
        actor = ria_for.get(firm_of[acct_id]) if who["role"] == "ria" else None
        requested = date.fromisoformat(t["requested"])
        deposit = round(max(rows[acct_id]["cash"], 500.0), 2)
        transfer(db, "ach_deposit", a, {"amount": deposit, "bank_link_id": links[acct_id], "requester": who,
                                        "note": "Opening deposit"}, actor, submitted=requested)
        positions = []
        for x in t["positions"]:
            sid = by_name.get(x["security"])
            if sid and x["quantity"]:
                positions.append({"security_id": sid, "description": x["security"], "quantity": x["quantity"],
                                  "asset_type": SECURITY_MASTER[sid].asset_type if sid in SECURITY_MASTER else
                                  db.get(Security, sid).asset_type,
                                  "market_value": round(x["quantity"] * SYN_PRICES.get(sid, 0), 2)})
        transfer(db, "acats_in", a, {"positions": positions, "requester": who,
                                     "counterparty": {"institution": t["other_firm"]}}, actor, submitted=requested)
    for acct_id, t in sorted(lois.items()):
        a = db.get(Account, acct_id)
        x = t["positions"][0]
        transfer(db, "loi_in", a, {"positions": [{"security_id": "SYN-REIT-01", "description": x["security"],
                                                  "quantity": x["quantity"], "asset_type": "alternative"}],
                                   "requester": {"role": "ria"}, "counterparty": {"institution": t["other_firm"]}},
                 ria_for.get(firm_of[acct_id]), submitted=date.fromisoformat(t["requested"]))
    db.flush()
    settlement.deliver_rail_answers(db, users.get("ops.settlement") or sup)     # the funding history settles

    # 2. A transfer in every state
    def acct(acct_id: str) -> Account | None:
        a = db.get(Account, acct_id)
        return a if a is not None and a.status == "OPEN" else None

    used: set[str] = set()
    funding = {c.account_id for c in db.scalars(select(Case).where(Case.kind == "transfer")).all()
               if c.status != "COMPLETED"}             # accounts whose funding transfer hasn't finished

    def pick(*, kinds=("individual", "joint"), min_cash=0.0, firm: str | None = None, ira: str | None = None,
             incoming: bool = False):
        """An open pure RIA account with enough settled cash and no other scenario on it; for money going out, also
        no recent change (MM-004 would send it to a risk review)."""
        for a in db.scalars(select(Account).where(Account.status == "OPEN", Account.group == "pure_ria")
                            .order_by(Account.id)).all():
            purpose = ((a.application or {}).get("account_purpose") or "").lower()
            if a.id in used or a.registration_type not in kinds or a.id in funding or \
                    (a.change_history and not incoming):
                continue
            if firm and firm_of[a.id] != firm or (ira and ira not in purpose):
                continue
            if servicing_codes(db, a.id) or ledger.settled(db, a.id)[0] < min_cash:
                continue
            used.add(a.id)
            return a
        return None

    def ria(a: Account) -> User | None:
        return ria_for.get(firm_of[a.id])

    days = lambda n: bday(today, -n)                        # noqa: E731
    if leaving and acct(leaving["account"]):
        a = acct(leaving["account"])
        cash, holdings = ledger.settled(db, a.id)
        positions = [{"security_id": k, "quantity": q, "description": db.get(Security, k).name,
                      "asset_type": db.get(Security, k).asset_type} for k, q in sorted(holdings.items())]
        transfer(db, "acats_out", a, {"positions": positions, "cash": round(cash, 2),
                                      "requester": {"role": "receiving_firm"},
                                      "counterparty": {"institution": leaving["other_firm"]}}, ops,
                 submitted=days(1))
    if acct("ACC-3005"):                    # proceeds of a business sale arrive; $250,000 goes out: callback first
        a = acct("ACC-3005")
        bank_on_file(a, "Great Salt Lake Trust Company", "6620")
        r = settlement.receive(db, channel="Fedwire", amount=300_000.00, sender="Bear River Savings Bank",
                               memo="FBO ACC-3005 Gregory Holm: proceeds of business sale", on=days(3),
                               actor=users.get("ops.settlement") or sup)
        settlement.apply_receipt(db, r, a.id, users.get("ops.settlement") or sup, "Memo names the account",
                                 automatic=True)
        transfer(db, "wire_out", a, {"amount": 250_000.0, "requester": {"role": "ria"},
                                     "counterparty": {"owner_name": a.display_name,
                                                      "institution": "Great Salt Lake Trust Company",
                                                      "account_last4": "6620"}}, ria(a))
    if acct("ACC-3006"):                                    # within an analyst's dollar authority
        a = acct("ACC-3006")
        bank_on_file(a, "Wasatch Community Bank", "4471")
        transfer(db, "wire_out", a, {"amount": 18_000.0, "requester": {"role": "ria"},
                                     "counterparty": {"owner_name": a.display_name,
                                                      "institution": "Wasatch Community Bank", "account_last4": "4471"}},
                 ria(a))
    a = pick(min_cash=50_000, firm="310031")                # a signed request by mail to a third party
    if a:
        transfer(db, "wire_out", a, {"amount": 45_000.0, "requester": {"role": "signed_form"}, "client_signed": True,
                                     "counterparty": {"owner_name": "Harbor Title & Escrow (synthetic)",
                                                      "owner_kind": "entity", "institution": "Sawtooth National Bank",
                                                      "account_last4": "3302"}}, ops)
    if acct("ACC-2004"):                                    # the flagged list stops it for the sanctions team
        a = acct("ACC-2004")
        cash, _ = ledger.settled(db, a.id)
        if cash >= 40_000:                                  # a client-signed request by mail, to a foreign company
            transfer(db, "wire_out", a, {"amount": 40_000.0, "requester": {"role": "signed_form"}, "client_signed": True,
                                         "counterparty": {"owner_name": "Corvane Maritime Holdings Ltd",
                                                          "owner_kind": "entity", "institution": "Banco Austral del Mar",
                                                          "bic": "BAMRPAPAXXX", "country": "Panama",
                                                          "account_last4": "0917"}}, ops)
    if acct("ACC-3024"):                                    # CIPV stops outgoing money
        a = acct("ACC-3024")
        transfer(db, "ach_withdrawal", a, {"amount": 1_000.0, "bank_link_id": links.get(a.id),
                                           "requester": {"role": "ria"}}, ria(a))
    used.update({"ACC-3005", "ACC-3006", "ACC-2004", "ACC-3024", "ACC-3001", "ACC-3009"})
    if acct("ACC-3001") and acct("ACC-3009"):               # same household: a journal books at once
        transfer(db, "journal", acct("ACC-3001"), {"amount": 25_000.0, "destination": "ACC-3009",
                                                   "requester": {"role": "ria"}}, ria(acct("ACC-3001")))
    a = pick(kinds=("ira",), ira="traditional", min_cash=8_000)   # IRA distribution with 10% withholding
    if a:
        c = transfer(db, "ach_withdrawal", a, {"amount": 6_000.0, "bank_link_id": links.get(a.id),
                                               "requester": {"role": "ria"}, "note": "IRA distribution"},
                     ria(a), submitted=days(3))
        _approve(db, c, "release", "released", "REL-SEED-01", ops, users["ops.money"],
                 "Within dollar authority; destination is the client's verified link")
    a = pick(min_cash=30_000)                               # a completed wire, released last week
    if a:
        bank_on_file(a, "Lakeview Federal Credit Union", "1188")
        c = transfer(db, "wire_out", a, {"amount": 25_000.0, "requester": {"role": "ria"},
                                         "counterparty": {"owner_name": a.display_name,
                                                          "institution": "Lakeview Federal Credit Union",
                                                          "account_last4": "1188"}}, ria(a), submitted=days(5))
        _approve(db, c, "release", "released", "REL-SEED-02", ops, users["ops.money"],
                 "Within dollar authority; first-party destination")
    a = acct("ACC-3009")                                    # a trust pays tuition under its SLOA: the RIA picks the payee
    if a:
        details = {"payee": "Alpine Ridge School (synthetic)", "payee_kind": "entity",
                   "payee_address": "2200 Ridge Rd, Sandy, UT 84092", "institution": "Sawtooth National Bank",
                   "account_last4": "5521", "schedule": "Each semester"}
        sloa = seed_sloa(db, a, details, users.get("ops.service") or ops, sup)
        if sloa.status == "APPLIED":
            transfer(db, "wire_out", a, {"amount": 12_500.0, "requester": {"role": "ria"}, "sloa_id": f"SLOA-{sloa.id}",
                                         "counterparty": {"owner_name": details["payee"], "owner_kind": "entity",
                                                          "institution": details["institution"],
                                                          "account_last4": details["account_last4"]}}, ria(a))
    a = pick(incoming=True)                                              # an ACH deposit the bank returned (R01)
    if a:
        transfer(db, "ach_deposit", a, {"amount": 7_500.0, "bank_link_id": links.get(a.id),
                                        "requester": {"role": "ria"}}, ria(a), submitted=days(4),
                 simulate={"return": "R01"})
    a = pick(min_cash=5_000)                                # an ACH credit returned (R03)
    if a:
        c = transfer(db, "ach_withdrawal", a, {"amount": 3_200.0, "bank_link_id": links.get(a.id),
                                               "requester": {"role": "ria"}}, ria(a), submitted=days(4),
                     simulate={"return": "R03"})
        _approve(db, c, "release", "released", "REL-SEED-03", ops, users["ops.money"],
                 "Within dollar authority")
    unbooked_return = None
    a = pick(incoming=True)                                              # settled and on hold; the return reaches the bank first
    if a:
        unbooked_return = transfer(db, "ach_deposit", a, {"amount": 7_500.0, "bank_link_id": links.get(a.id),
                                                          "requester": {"role": "ria"}}, ria(a), submitted=days(2))
    a = pick(incoming=True)                                              # settled; returnable funds held 2 business days
    if a:
        transfer(db, "ach_deposit", a, {"amount": 15_000.0, "bank_link_id": links.get(a.id),
                                        "requester": {"role": "ria"}}, ria(a), submitted=days(1))
    if acct("ACC-2003"):                                    # the client's own deposit to the self-directed account
        a = acct("ACC-2003")
        transfer(db, "ach_deposit", a, {"amount": 2_500.0, "bank_link_id": links.get(a.id),
                                        "requester": {"role": "client"}}, users.get("client.farid"))
    a = pick(kinds=("individual",), incoming=True)                         # the pre-screen catches a title that won't match
    if a:
        first, last = a.display_name.split()[0], a.display_name.split()[-1]
        transfer(db, "acats_in", a, {"requester": {"role": "ria"}, "title_on_form": f"{first} A. {last} Individual",
                                     "counterparty": {"institution": "Prior custodian B (synthetic)"},
                                     "positions": [_pos(db, "STUM", 120), _pos(db, "CDVG", 80)]}, ria(a))
    a = pick(incoming=True)                                              # the delivering firm takes exception to the title
    if a:
        transfer(db, "acats_in", a, {"requester": {"role": "ria"},
                                     "counterparty": {"institution": "Prior custodian A (synthetic)"},
                                     "positions": [_pos(db, "SIEQ", 200), _pos(db, "SSTR", 300)]}, ria(a),
                 submitted=days(2), simulate={"reject": "title"})
    a = pick(incoming=True)                                              # past its settlement date with no answer: chase it
    if a:
        transfer(db, "acats_in", a, {"requester": {"role": "ria"},
                                     "counterparty": {"institution": "Prior broker-dealer C (synthetic)"},
                                     "positions": [_pos(db, "UNTH", 60), _pos(db, "SAGB", 150)]}, ria(a),
                 submitted=days(7), simulate={"hold": True})
    db.flush()

    # 3. Money at the bank with no request, and what the street reports
    actor = users.get("ops.settlement") or sup
    a = pick(kinds=("individual", "joint", "trust"))
    if a:
        settlement.receive(db, channel="Fedwire", amount=125_000.00, sender="Wasatch Community Bank",
                           memo=f"FBO {a.id} {a.display_name}: proceeds from sale of residence", on=today, actor=actor)
    a = pick(kinds=("joint", "individual", "llc"))
    if a:
        typo = a.id[:4] + a.id[4:].replace("0", "O", 1)
        settlement.receive(db, channel="Fedwire", amount=48_250.00, sender="Harbor Title & Escrow (synthetic)",
                           memo=f"FBO {typo} closing proceeds, file 26-1184", on=days(1), actor=actor)
    settlement.receive(db, channel="ACH", amount=1_500.00, sender="Alder Creek Services LLC (synthetic)",
                       memo="INV 44821 REFUND", on=days(12), actor=actor)
    rdgix = next((s.cusip for s in sb.SECURITIES if s.ticker == "RDGIX"), None)
    held_rdgix = sum(u for _l, _a, asset, b, u in ledger.balances(db, layer="client") if asset == rdgix and b == "settled")
    if rdgix and held_rdgix:
        recon.report(db, location="1510", asset=rdgix, difference=round(held_rdgix / 1_000_000 * 0.0042, 3),
                     pattern="dividend_reinvestment",
                     note="RDGIX reinvested its dividend at the transfer agent; holders not yet booked",
                     opened_on=days(1))
    recon.report(db, location="1010", asset=CASH, difference=-35.00, pattern="bank_fee",
                 note="Settlement bank's monthly wire fee debited", opened_on=today)
    sflx = next((s.cusip for s in sb.SECURITIES if s.ticker == "SFLX"), None)
    recon.report(db, location="1500", asset=sflx, difference=12, pattern="unexplained",
                 note="DTC shows 12 more SFLX shares than the books", opened_on=days(7))
    stum = next((s.cusip for s in sb.SECURITIES if s.ticker == "STUM"), None)
    at_dtc = {a: q for (loc, a), q in ledger.location_books(db).items() if loc == "1500" and a not in (stum, sflx)}
    if at_dtc:                                       # DTC short of the books: out of control until it's traced
        short = max(at_dtc, key=at_dtc.get)
        ticker = db.get(Security, short).ticker or short
        recon.report(db, location="1500", asset=short, difference=-40, pattern="unexplained",
                     note=f"DTC shows 40 fewer {ticker} shares than the books: a possession-or-control deficit "
                          "(Rule 15c3-3) until it's traced", opened_on=today)
    if unbooked_return is not None:
        recon.report(db, location="1010", asset=CASH, difference=-7_500.00, pattern="ach_return_unbooked",
                     note=f"R01 return on {unbooked_return.account_id}'s ACH deposit debited at the bank",
                     case_id=unbooked_return.id, account_id=unbooked_return.account_id, opened_on=today)
    late = next((c for c in db.scalars(select(Case).where(Case.kind == "transfer", Case.status == "COMPLETED")).all()
                 if (c.application or {}).get("type") == "acats_in"), None)
    if late is not None:
        recon.report(db, location="1500", asset=stum, difference=30, pattern="late_delivery",
                     note=f"30 STUM delivered at DTC after the transfer to {late.account_id} settled",
                     account_id=late.account_id, case_id=late.id, opened_on=days(2))
    db.flush()

    # 4. The special reserve bank account (Rule 15c3-3): the required reserve plus a cushion, then the day's cycle
    import math
    from . import protection
    need = protection.compute(db)["reserve"]["required"]
    protection.deposit(db, actor, math.ceil(need * 1.05 / 10_000) * 10_000,
                       "opening deposit: the required reserve plus a 5% cushion")
    settlement.run_cycle(db, actor)
    recon.run(db, actor)
    db.flush()


def _pos(db: Session, ticker: str, qty: float) -> dict:
    s = db.scalar(select(Security).where(Security.ticker == ticker))
    return {"security_id": s.id, "description": s.name, "quantity": qty, "asset_type": s.asset_type}
