"""
Ongoing screening (onboarding/rescreening.py) against the live flagged list: the base list plus the updates the
sanctions team adds. Every addition rescreens everyone at once; the nightly batch rescreens everyone again. A new match
opens a screening case: the SANC hold goes on the account or firm, and the sanctions team decides under maker-checker.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from flagged_list import FlaggedEntry, Kind, Subject
from rescreening import Party, Population, alert_package, find_alerts, hit_key
from sample_data import FLAGGED_LIST

from . import audit
from .config import settings
from .orm import Account, Case, FlaggedListEntry, Firm, User

ENTITY_TYPES = ("trust", "llc", "corporation")


def current_list(db: Session) -> list[FlaggedEntry]:
    """The flagged list in force: the base list and every update added since."""
    added = [FlaggedEntry(e.entry_id, Kind(e.kind), e.name, tuple(e.aliases or ()), e.date_of_birth, e.country,
                          dict(e.identifiers or {}), e.program)
             for e in db.scalars(select(FlaggedListEntry).order_by(FlaggedListEntry.id)).all()]
    return list(FLAGGED_LIST) + added


def _person(target: str, i: int, p: dict, role: str) -> Subject:
    return Subject(f"{target}:{role}:{i}", Kind.INDIVIDUAL, p.get("name") or "", p.get("role") or role,
                   p.get("date_of_birth"), p.get("country_of_residence"), p.get("address"))


def population(db: Session) -> Population:
    """Every open account's holders, owners, and control persons, its beneficiaries, and its standing payees (SLOAs)
    and their banks; every active RIA firm, its owners, and its control persons."""
    pop = Population()
    for a in db.scalars(select(Account).where(Account.status.in_(("OPEN", "OPEN_RESTRICTED"))).order_by(Account.id)):
        app = a.application or {}
        add = lambda s, rel: pop.parties.append(Party(a.id, "account", a.display_name, s, rel))   # noqa: E731
        if a.registration_type in ENTITY_TYPES and app.get("entity_name"):
            add(Subject(f"{a.id}:entity", Kind.ENTITY, app["entity_name"], "account holder (entity)"), "account holder")
        for i, h in enumerate(app.get("holders") or []):
            add(_person(a.id, i, h, "holder"), (h.get("role") or "account_holder").replace("_", " "))
        bens = app.get("beneficiaries") or (app.get("maintenance") or {}).get("beneficiaries") or []
        for i, b in enumerate(bens if isinstance(bens, list) else []):
            if isinstance(b, dict) and b.get("name"):
                add(Subject(f"{a.id}:beneficiary:{i}", Kind.INDIVIDUAL, b["name"], "beneficiary"), "beneficiary")
        for i, s in enumerate(x for x in app.get("sloas") or [] if x.get("status") == "active"):
            kind = Kind.ENTITY if s.get("payee_kind") == "entity" else Kind.INDIVIDUAL
            add(Subject(f"{a.id}:sloa:{i}", kind, s.get("payee") or "", "standing payee",
                        address=s.get("payee_address")), f"standing payee ({s['id']})")
            if s.get("institution"):
                add(Subject(f"{a.id}:sloa_bank:{i}", Kind.INSTITUTION, s["institution"], "standing payee's bank",
                            identifiers={"aba": s["aba"]} if s.get("aba") else {}), f"standing payee's bank ({s['id']})")
    for f in db.scalars(select(Firm).where(Firm.status == "ACTIVE").order_by(Firm.id)):
        app = f.application or {}
        pop.parties.append(Party(f.crd, "firm", f.legal_name, Subject(f"{f.crd}:firm", Kind.ENTITY, f.legal_name,
                                                                       "RIA firm"), "RIA firm"))
        for i, p in enumerate(app.get("people") or []):
            pop.parties.append(Party(f.crd, "firm", f.legal_name, _person(f.crd, i, p, "person"),
                                     (p.get("role") or "person").replace("_", " ")))
    return pop


def _keys(case: Case) -> set[str]:
    hits = (case.application or {}).get("hits") if case.kind == "screening" else (case.result or {}).get("screening")
    return {hit_key(case.target_id, h) for h in hits or []}


def cleared_and_open(db: Session) -> tuple[set[str], set[str]]:
    """Matches already cleared as false positives for that client (at onboarding, on a change, or in an earlier
    rescreen), and matches already waiting on the sanctions team."""
    cleared, waiting = set(), set()
    for c in db.scalars(select(Case).where(Case.kind.in_(("screening", "account", "firm", "change")))).all():
        if (c.decisions or {}).get("sanctions") == "false_positive":
            cleared |= _keys(c)
        elif c.kind == "screening" and c.status == "PENDING_SANCTIONS":
            waiting |= _keys(c)
    return cleared, waiting


def run(db: Session, actor: User | None, trigger: str) -> dict:
    """Rescreen everyone; open a screening case for each account or firm with a new match."""
    from .runner import run_case
    flagged = current_list(db)
    pop = population(db)
    cleared, waiting = cleared_and_open(db)
    alerts = find_alerts(pop, flagged, settings.today(), cleared, waiting)
    targets = pop.targets()
    opened = []
    for target_id, hits in sorted(alerts.items()):
        parties = targets[target_id]
        first = parties[0]
        package = alert_package(first, hits, trigger, len(flagged), parties)
        account = db.get(Account, target_id) if first.target_kind == "account" else None
        firm = db.get(Firm, account.firm_id) if account else db.scalar(select(Firm).where(Firm.crd == target_id))
        case = Case(kind="screening", title=f"Ongoing screening: {first.target_name}", firm_id=firm.id if firm else None,
                    account_id=account.id if account else None, target_id=target_id, application=package,
                    decisions={}, extra={"events": []}, created_by=actor.id if actor else None)
        db.add(case)
        db.flush()
        audit.record(db, actor, "case_created", "case", case.id, case_id=case.id, kind="screening",
                     account=account.id if account else None)
        run_case(db, case, actor)
        opened.append({"case": case.id, "target": target_id, "name": first.target_name,
                       "hits": sorted({f"{h.subject.name} ~ {h.matched}" for h in hits})})
    summary = {"trigger": trigger, "on": settings.today().isoformat(), "parties": len(pop.parties),
               "targets": len(targets), "list_size": len(flagged), "alerts": opened}
    audit.record(db, actor, "rescreen_run", "screening", trigger, **{k: v for k, v in summary.items() if k != "alerts"},
                 alerts=[a["case"] for a in opened])
    return summary


def add_entry(db: Session, actor: User, data: dict) -> tuple[FlaggedListEntry, dict]:
    """A list update: the entry, then rescreening of everyone against the new list."""
    n = len(db.scalars(select(FlaggedListEntry)).all()) + 101
    entry = FlaggedListEntry(entry_id=f"FL-{n:03d}", kind=data["kind"], name=data["name"].strip(),
                             aliases=[a.strip() for a in data.get("aliases") or [] if a.strip()],
                             date_of_birth=data.get("date_of_birth") or None, country=data.get("country") or None,
                             identifiers={k: v for k, v in (data.get("identifiers") or {}).items() if v},
                             program=data.get("program") or "Synthetic list update",
                             source=data.get("source") or "Simulated list update (synthetic)",
                             added_by=actor.display_name)
    db.add(entry)
    db.flush()
    audit.record(db, actor, "flagged_entry_added", "flagged_entry", entry.entry_id, name=entry.name, kind=entry.kind,
                 program=entry.program)
    return entry, run(db, actor, "list_change")
