"""
Demo data: synthetic firms, accounts, users, and cases left in every queue.

Sample dates are moved forward to today, so certifications, filings, and
review dates stay current however long the demo has been running.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from account_changes import ChangeType
from applications import AccountApplication, Person
from models import AccountGroup
from sample_data import (
    ACCOUNT_FIRMS, ACCOUNTS, FIRMS, TODAY as SAMPLE_DAY,
)

from . import audit
from .acceptance import accept_case
from .config import settings
from .documents import AUTOMATED_REVIEWER, FIELD_DOCS, checks_pass, mark, sample_document, store
from .db import Base, engine
from .orm import Account, Case, Firm, Review, User, utcnow
from .runner import run_case
from .serialize import shift_dates, to_json

DEMO_USERS = [
    ("ops.analyst", "Jamie Ortiz", "ops_analyst", None, None, "Onboarding: works intake, NIGO, and escalations"),
    ("ops.cip", "Taylor Brooks", "ops_analyst", None, None, "CIP: verifies the identity of new parties"),
    ("ops.service", "Elliot Shaw", "ops_analyst", None, None, "Client service: confirms account changes"),
    ("ops.supervisor", "Priya Raman", "ops_supervisor", None, None, "Approves operations decisions as checker"),
    ("aml.maker", "Dev Patel", "aml_compliance", None, None, "Proposes EDD and compliance decisions"),
    ("aml.checker", "Morgan Lee", "aml_compliance", None, None, "Approves EDD and compliance decisions"),
    ("sanctions.maker", "Alex Kim", "sanctions", None, None, "Proposes sanctions determinations"),
    ("sanctions.checker", "Sam Rivera", "sanctions", None, None, "Approves sanctions determinations"),
    ("review.analyst", "Casey Nguyen", "periodic_review", None, None, "Works reviews as they come due"),
    ("rs.lead", "Renee Alvarez", "ria_service_lead", None, None,
     "RIA service team lead (Series 7, 63, 24): approves rescissions"),
    ("rs.principal", "Avery Collins", "ria_service", None, None,
     "Registered principal (Series 7, 63, 24): accepts accounts"),
    ("rs.associate", "Noah Brennan", "ria_service", None, None,
     "Licensed representative (Series 7, 63): can't accept accounts"),
    ("admin.platform", "Jordan Blake", "platform_admin", None, None, "Adds team members and grants roles"),
    ("admin.second", "Riley Chen", "platform_admin", None, None, "Approves grants of decision roles"),
    ("granite.admin", "Dana Whitfield", "ria_admin", "310001", None, "Granite Peak Advisors: firm users"),
    ("granite.advisor", "Marcus Lee", "ria_user", "310001", None, "Granite Peak Advisors: requests and changes"),
    ("bonneville.advisor", "Sam Okafor", "ria_user", "310003", None, "Bonneville Planning: requests and changes"),
    ("client.farid", "Farid Hosseini", "client", None, "ACC-2003", "Client with a self-directed account"),
]

# Synthetic addresses on reserved example domains (RFC 2606): notices to them are held in the outbox
SEED_EMAILS = {"granite.admin": "dana.whitfield@granitepeak.example", "granite.advisor": "marcus.lee@granitepeak.example",
               "bonneville.advisor": "sam.okafor@bonneville.example", "client.farid": "farid.hosseini@client.example"}

# Operations teams (orm.OPS_TEAMS)
SEED_TEAMS = {"ops.analyst": "onboarding", "ops.cip": "cip", "ops.service": "client_service",
              "ops.supervisor": "onboarding"}

# Synthetic FINRA registrations and individual CRD numbers for the licensed RIA service team
SEED_REGISTRATIONS = {"rs.lead": (["S7", "S63", "S24"], "7700100"),
                      "rs.principal": (["S7", "S63", "S24"], "7700101"),
                      "rs.associate": (["S7", "S63"], "7700102")}

# Seeded cases the registered principal has already accepted. Juniper Ridge and Lena Park still wait.
# Rosa Delgado waits for items: her LPOA is regulatory, so the account holds. Owen Gallagher opens restricted from
# trading and fee deduction: only his fee authorization and tax form are outstanding, and neither is regulatory.
ACCEPTED_SEEDS = {"granite_peak", "bonneville", "maria_chen", "kenji_tanaka", "farid_hosseini_pure",
                  "farid_hosseini_sd", "whitfield_holdings", "owen_gallagher_restricted"}

# Seeded firm cases and the decisions that move them to their demo state
FIRM_SEEDS = ["granite_peak", "juniper_ridge", "bonneville", "lakeshore", "meridian_gate", "alta_vista", "cottonwood"]
ACCOUNT_SEEDS = ["maria_chen", "kenji_tanaka", "farid_hosseini_pure", "farid_hosseini_sd", "whitfield_holdings",
                 "ilya_sorvetkin", "elena_marsh_pep", "orphan_self_directed", "rosa_delgado_nigo",
                 "lena_park_waiting", "owen_gallagher_restricted"]

# How long ago each seeded case arrived, in hours, so SLA clocks and time-in-state look like a working
# queue: some cases comfortable, one close to its deadline, one past it.
AGE_HOURS = {
    "granite_peak": 720, "juniper_ridge": 504, "bonneville": 432, "lakeshore": 52, "meridian_gate": 9,
    "alta_vista": 70, "cottonwood": 200,
    "maria_chen": 300, "kenji_tanaka": 260, "farid_hosseini_pure": 240, "farid_hosseini_sd": 230,
    "whitfield_holdings": 190, "ilya_sorvetkin": 21, "elena_marsh_pep": 30, "orphan_self_directed": 40,
    "rosa_delgado_nigo": 128, "lena_park_waiting": 6, "owen_gallagher_restricted": 50, "CHG-0001": 72,
    "CHG-0002": 5,
}


def backdate(db: Session, case: Case, hours: float) -> None:
    """Move a freshly seeded case, its history, and what it touched back in time by `hours`."""
    from .orm import AuditEvent, CaseTransition, Document, OutboundEmail, Restriction
    shift = timedelta(hours=hours)
    for row in db.query(OutboundEmail).filter(OutboundEmail.case_id == case.id):
        row.created_at -= shift
    for row in db.query(Document).filter(Document.case_id == case.id):
        row.uploaded_at -= shift
        row.received_on = row.uploaded_at.date()
        if row.reviewed_at:
            row.reviewed_at -= shift
    for col in ("created_at", "updated_at", "queue_entered_at", "sla_due", "closed_at"):
        if getattr(case, col) is not None:
            setattr(case, col, getattr(case, col) - shift)
    for row in db.query(CaseTransition).filter(CaseTransition.case_id == case.id):
        row.at -= shift
    for row in db.query(AuditEvent).filter(AuditEvent.case_id == case.id):
        row.at -= shift
    for row in db.query(Restriction).filter(Restriction.case_id == case.id):
        row.placed_at -= shift
    if case.kind == "firm":
        firm = db.get(Firm, case.firm_id)
        if firm.activated_at:
            firm.activated_at -= shift
    elif case.kind == "account":
        account = db.get(Account, case.account_id)
        if account.opened_at:
            account.opened_at -= shift


def client_key(app: AccountApplication) -> str:
    if app.entity_ein:
        return f"EIN:{app.entity_ein}"
    p = app.primary
    return f"TIN:{p.tax_id}" if p and p.tax_id else f"NAME:{p.name if p else app.account_id}"


def account_display(app: AccountApplication) -> str:
    return app.entity_name or (app.primary.name if app.primary else app.account_id)


def _farid_pure() -> AccountApplication:
    sd = ACCOUNTS["farid_hosseini_sd"]
    return dataclasses.replace(sd, application_id="APP-1990", account_id="ACC-1990", group=AccountGroup.PURE_RIA,
                               lpoa_signed=True, fee_authorization=True, account_purpose="Retirement investing",
                               expected_initial_funding=320_000)


def _rosa_nigo() -> AccountApplication:
    """An application that arrives without the LPOA or a tax form: the LPOA is regulatory, so it waits for items."""
    return AccountApplication(
        "APP-2008", "ACC-2008", AccountGroup.PURE_RIA, "individual",
        [Person("Rosa Delgado", "account_holder", date_of_birth="1983-09-14", identity_verified=True,
                address="45 E 400 S, Provo, UT 84606", tax_id="900-00-2008", occupation="Teacher")],
        lpoa_signed=False, fee_authorization=True, client_signature=True, tax_form=None,
        account_purpose="College savings for two children", source_of_funds="Salary", source_of_wealth="Salary",
        expected_initial_funding=60_000)


def _lena_park() -> AccountApplication:
    """A clean application: every check passes, and it waits for the registered principal."""
    return AccountApplication(
        "APP-2009", "ACC-2009", AccountGroup.PURE_RIA, "individual",
        [Person("Lena Park", "account_holder", date_of_birth="1991-02-27", identity_verified=True,
                address="1820 S 1100 E, Salt Lake City, UT 84105", tax_id="900-00-2009", occupation="Pharmacist")],
        lpoa_signed=True, fee_authorization=True, client_signature=True, tax_form="W-9",
        account_purpose="Long-term investing", source_of_funds="Salary", source_of_wealth="Salary and savings",
        expected_initial_funding=210_000)


def _owen_gallagher() -> AccountApplication:
    """Every regulatory item is in, but the fee authorization and W-9 aren't: it opens restricted (DOCS)."""
    return AccountApplication(
        "APP-2010", "ACC-2010", AccountGroup.PURE_RIA, "individual",
        [Person("Owen Gallagher", "account_holder", date_of_birth="1976-06-03", identity_verified=True,
                address="2210 Harrison Blvd, Ogden, UT 84401", tax_id="900-00-2010", occupation="Civil engineer")],
        lpoa_signed=True, fee_authorization=False, client_signature=True, tax_form=None,
        account_purpose="Retirement investing", source_of_funds="Salary", source_of_wealth="Salary and savings",
        expected_initial_funding=145_000)


TAX_DOC = {"W-9": "w9", "W-8BEN": "w8ben", "W-8BEN-E": "w8bene"}


def seed_documents(db: Session, case: Case, uploader: str, channel: str, ops_user: User) -> None:
    """Synthetic documents behind every item a seeded application already has."""
    app = case.application
    for field, types in FIELD_DOCS[case.kind].items():
        if field == "verify_people":
            required = [p for p in app.get("people", [])
                        if p.get("ownership_pct", 0) >= 25 or p.get("role") == "control_person"]
            if not required or not all(p.get("identity_verified") for p in required):
                continue
            doc_type = "identity_documents"
        elif not app.get(field):
            continue
        else:
            doc_type = TAX_DOC.get(app[field]) if field == "tax_form" else types[0]
        filename, data = sample_document(db, case, doc_type, settings.today())
        doc = store(db, case, None, doc_type, filename, data, channel, synthetic=True, actor_name=uploader)
        if doc_type == "identity_documents" or not checks_pass(doc.checks):
            mark(db, doc, "in_good_order", ops_user.display_name, ops_user)
        else:
            mark(db, doc, "in_good_order", AUTOMATED_REVIEWER, None)


def create_firm_case(db: Session, app, actor: User | None, extraction: dict | None = None) -> Case:
    key = app.crd_number or f"NEW-{uuid.uuid4().hex[:8].upper()}"
    firm = Firm(crd=key, legal_name=app.legal_name or "Unnamed firm", application=to_json(app))
    db.add(firm)
    db.flush()
    case = Case(kind="firm", title=f"RIA firm onboarding: {firm.legal_name}", firm_id=firm.id,
                target_id=f"FIRM-{key}", application=to_json(app), decisions={},
                extra={"extraction": extraction} if extraction else {}, created_by=actor.id if actor else None)
    db.add(case)
    db.flush()
    audit.record(db, actor, "case_created", "case", case.id, case_id=case.id, kind="firm", firm=firm.legal_name)
    return case


def create_account_case(db: Session, app: AccountApplication, firm: Firm, actor: User | None) -> Case:
    account = Account(id=app.account_id, firm_id=firm.id, client_key=client_key(app),
                      display_name=account_display(app), group=app.group.value,
                      registration_type=app.registration_type, application=to_json(app))
    db.add(account)
    db.flush()
    group = "self-directed" if app.group is AccountGroup.SELF_DIRECTED else "pure RIA"
    case = Case(kind="account", title=f"Account opening: {account.display_name} ({group})", firm_id=firm.id,
                account_id=account.id, target_id=account.id, application=to_json(app), decisions={},
                created_by=actor.id if actor else None)
    db.add(case)
    db.flush()
    audit.record(db, actor, "case_created", "case", case.id, case_id=case.id, kind="account", account=account.id)
    return case


def create_change_case(db: Session, change: dict, account: Account, actor: User | None) -> Case:
    label = change["change_type"].replace("_", " ")
    case = Case(kind="change", title=f"Account change: {label} on {account.display_name}", firm_id=account.firm_id,
                account_id=account.id, target_id=account.id, application=change, decisions={},
                created_by=actor.id if actor else None)
    db.add(case)
    db.flush()
    audit.record(db, actor, "case_created", "case", case.id, case_id=case.id, kind="change",
                 change_type=change["change_type"], account=account.id)
    return case


def reset_database() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def seed(db: Session) -> None:
    today = settings.today()
    delta = timedelta(days=(today - SAMPLE_DAY).days)
    firms_by_crd: dict[str, Firm] = {}

    users = {}
    for username, name, role, crd, account_id, note in DEMO_USERS:
        regs, individual_crd = SEED_REGISTRATIONS.get(username, ([], None))
        u = User(username=username, display_name=name, role=role, is_demo=True, demo_note=note,
                 registrations=regs, individual_crd=individual_crd, team=SEED_TEAMS.get(username),
                 email=SEED_EMAILS.get(username))
        db.add(u)
        users[username] = u
    db.flush()
    system_ops = users["ops.analyst"]
    principal = users["rs.principal"]

    def accept_if_seeded(case: Case, key: str) -> None:
        if key in ACCEPTED_SEEDS and case.status == "PENDING_ACCEPTANCE":
            accept_case(db, case, principal, "accepted", reviewed=True)

    for key in FIRM_SEEDS:
        app = shift_dates(FIRMS[key], delta)
        case = create_firm_case(db, app, system_ops)
        firms_by_crd[app.crd_number] = db.get(Firm, case.firm_id)
        seed_documents(db, case, system_ops.display_name, "email", system_ops)
        run_case(db, case, system_ops)
        db.flush()
        accept_if_seeded(case, key)
        db.flush()
        backdate(db, case, AGE_HOURS[key])

    # RIA users belong to their firms before their accounts are seeded, so the account notices reach them
    for username, _name, _role, crd, _account, _note in DEMO_USERS:
        if crd:
            users[username].firm_id = firms_by_crd[crd].id
    db.flush()

    for key in ACCOUNT_SEEDS:
        if key == "farid_hosseini_pure":
            app, firm_key = _farid_pure(), "granite_peak"
        elif key == "rosa_delgado_nigo":
            app, firm_key = _rosa_nigo(), "bonneville"
        elif key == "lena_park_waiting":
            app, firm_key = _lena_park(), "granite_peak"
        elif key == "owen_gallagher_restricted":
            app, firm_key = _owen_gallagher(), "granite_peak"
        else:
            app, firm_key = ACCOUNTS[key], ACCOUNT_FIRMS[key]
        app = shift_dates(app, delta)
        firm = firms_by_crd[FIRMS[firm_key].crd_number]
        actor = users["granite.advisor"] if firm.crd == "310001" else users["bonneville.advisor"]
        if app.group is AccountGroup.SELF_DIRECTED:
            actor = system_ops
        case = create_account_case(db, app, firm, actor)
        seed_documents(db, case, actor.display_name, "portal", system_ops)
        run_case(db, case, actor)
        db.flush()
        accept_if_seeded(case, key)
        db.flush()
        backdate(db, case, AGE_HOURS[key])

    for username, *_rest in DEMO_USERS:
        u = users[username]
        crd, account_id = next((d[3], d[4]) for d in DEMO_USERS if d[0] == username)
        if crd:
            u.firm_id = firms_by_crd[crd].id
        if account_id:
            u.client_account_id = account_id

    # Account changes: one applied, one waiting on a fraud-pattern confirmation
    kenji = db.get(Account, "ACC-2002")
    applied = {"request_id": "CHG-0001", "account_id": kenji.id, "account_group": kenji.group,
               "account_firm": "310003", "change_type": ChangeType.CONTACT_INFO.value,
               "submitted_by_role": "ria_user", "submitted_by_firm": "310003",
               "details": {"email": "k.tanaka@example.com"}, "path": kenji.path or "B"}
    case = create_change_case(db, applied, kenji, users["bonneville.advisor"])
    run_case(db, case, users["bonneville.advisor"])
    db.flush()
    backdate(db, case, AGE_HOURS["CHG-0001"])
    kenji.change_history = [[ChangeType.CONTACT_INFO.value, (today - timedelta(days=3)).isoformat()]]

    maria = db.get(Account, "ACC-2001")
    maria.change_history = [[ChangeType.ADDRESS.value, (today - timedelta(days=6)).isoformat()]]
    suspicious = {"request_id": "CHG-0002", "account_id": maria.id, "account_group": maria.group,
                  "account_firm": "310001", "change_type": ChangeType.BANK_INSTRUCTION.value,
                  "submitted_by_role": "ria_user", "submitted_by_firm": "310001",
                  "new_bank": {"name": "Wasatch Community Bank", "aba": "124000054", "account_last4": "8812"},
                  "recent_changes": maria.change_history, "path": maria.path or "A"}
    case = create_change_case(db, suspicious, maria, users["granite.advisor"])
    run_case(db, case, users["granite.advisor"])
    db.flush()
    backdate(db, case, AGE_HOURS["CHG-0002"])

    # Reviews: one due now and one with a missed refresh deadline, so the queue has work
    for subject_id, days_ago in (("ACC-2002", 0), ("ACC-2004", 0)):
        review = db.query(Review).filter(Review.subject_id == subject_id).one_or_none()
        if review is None:
            continue
        if subject_id == "ACC-2002":
            review.next_review = today + timedelta(days=12)
        else:
            review.next_review = today - timedelta(days=40)
            review.status, review.requested_on = "requested", today - timedelta(days=40)
            review.response_due = today - timedelta(days=10)
            review.history = list(review.history or []) + [
                {"on": review.requested_on.isoformat(), "event": "refresh requested",
                 "response_due": review.response_due.isoformat()}]
    db.flush()
    from .risk_register import sweep_sla_breaches
    sweep_sla_breaches(db)          # seeded cases already past their SLA show in the risk register
    seed_messages(db, users)
    db.commit()


def seed_messages(db: Session, users: dict) -> None:
    """Team channels, with a few synthetic conversations about the seeded cases."""
    from .orm import Channel, ChannelMessage
    from .routers.messages import ensure_channels, parse_refs
    ensure_channels(db)
    def cid(part: str) -> int:
        return db.scalar(select(Case.id).where(Case.title.contains(part)))
    rosa, lena, juniper, meridian = cid("Rosa Delgado"), cid("Lena Park"), cid("Juniper Ridge"), cid("Meridian Gate")
    now = utcnow()
    script = [
        ("general", "admin.platform", 26, "Welcome to team channels. Reference a case with its number (#12), an account "
                                          "or firm by its id (ACC-2001, FIRM-310001), and a person with @username."),
        ("onboarding", "ops.supervisor", 5, f"Rosa Delgado's application is past its 5-day window: case #{rosa}. "
                                            "@ops.analyst can you chase Bonneville for the LPOA and the W-9?"),
        ("onboarding", "ops.analyst", 4, "Called Sam Okafor at Bonneville this morning. The client signed the LPOA "
                                         "yesterday; it comes through the portal today."),
        ("ria-service", "rs.lead", 3, f"Lena Park (case #{lena}) and Juniper Ridge (case #{juniper}) are waiting for "
                                      "acceptance. @rs.principal can you take both today?"),
        ("sanctions", "sanctions.maker", 2, f"Meridian Gate (case #{meridian}): exact name and date-of-birth match on a "
                                            "55% owner against FL-001. I'll propose a true match once the evidence "
                                            "package is complete."),
    ]
    channels = {c.key: c for c in db.scalars(select(Channel)).all()}
    for key, username, hours_ago, body in script:
        refs, mentions = parse_refs(db, body)
        db.add(ChannelMessage(channel_id=channels[key].id, author_id=users[username].id, body=body, refs=refs,
                              mentions=mentions, created_at=now - timedelta(hours=hours_ago)))
    db.flush()


def ensure_seeded(db: Session) -> bool:
    """Seed an empty database. Returns True if it seeded."""
    Base.metadata.create_all(engine)
    if db.query(User).first() is not None:
        return False
    seed(db)
    return True
