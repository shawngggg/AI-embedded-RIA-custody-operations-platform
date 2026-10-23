"""
Runs the release 2 cases (transfers, entitlements, products, offboarding, deceased accounts, advisor moves, mergers,
RIA transitions) through the custody engine and applies what it decides:

1. builds the engine's view of today from the database (the account, its codes, its ledger balances, the security
   master, the client's grant and bank links, the child cases' statuses);
2. replays the case from its request, approved decisions, and recorded events;
3. records the events the engine asks for, posts each ledger entry it lists that isn't posted yet (by reference),
   opens the child cases it names once, and applies its effects (an account closed, re-linked, or its LPOA ended);
4. syncs status, queue, SLA clock, restriction codes, and notices like any case (runner.sync);
5. re-runs the parent when a child's status changes, so a transition or an offboarding moves on by itself.
"""
from __future__ import annotations

import contextvars
from datetime import datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from sample_data import FLAGGED_LIST
from transferability import SecurityRecord

from custody import entitlements, lifecycle, money, products
from custody.calendar import quarter_bounds
from custody.ledger import CASH

from . import audit, ledger
from .config import settings
from .orm import Account, BankLink, Case, Document, Firm, Grant, LedgerEntry, Restriction, Security, User, utcnow

R2_KINDS = {"transfer", "entitlement", "product", "offboarding", "deceased", "advisor_move", "merger", "transition"}
MAP = {"transfer": "Asset and money transfers (map 08)", "entitlement": "Client entitlements (map 05)",
       "product": "Product acceptance (map 24)", "offboarding": "Client offboarding (map 16)",
       "deceased": "Deceased client account (map 17)", "advisor_move": "Advisor moves (map 23)",
       "merger": "RIA mergers and rebrands (map 23)", "transition": "RIA firm transitions (map 22)"}
_running: contextvars.ContextVar[frozenset] = contextvars.ContextVar("r2_running", default=frozenset())


# --------------------------------------------------------------------------- the engine's view of today
def codes_on(db: Session, target: str) -> list[str]:
    return sorted({r.code for r in db.scalars(select(Restriction).where(Restriction.target_id == target,
                                                                        Restriction.active.is_(True))).all()})


def account_view(db: Session, a: Account) -> dict:
    firm = db.get(Firm, a.firm_id)
    app = a.application or {}
    maint = app.get("maintenance") or {}
    holders = [{"name": h.get("name"), "role": h.get("role")} for h in app.get("holders", [])]
    household = a.client_key
    if a.registration_type == "trust":             # a trust joins its trustee's household at the same RIA
        for h in app.get("holders", []):
            if h.get("role") == "trustee":
                other = db.scalar(select(Account).where(Account.firm_id == a.firm_id, Account.display_name == h["name"],
                                                        Account.registration_type != "trust"))
                if other:
                    household = other.client_key
    bens = app.get("beneficiaries") or maint.get("beneficiaries") or maint.get("beneficiary") or []
    return {"id": a.id, "name": a.display_name, "firm_crd": firm.crd, "firm": firm.legal_name, "group": a.group,
            "registration_type": a.registration_type, "status": a.status, "codes": codes_on(db, a.id),
            "owners": [h["name"] for h in holders if h.get("name")] or [a.display_name], "holders": holders,
            "lpoa": bool(app.get("lpoa_signed")) and not maint.get("lpoa_ended"), "household": household,
            "purpose": app.get("account_purpose") or "", "recent_changes": list(a.change_history or []),
            "beneficiaries": bens if isinstance(bens, list) else [bens], "rep_code": a.rep_code,
            "client_key": a.client_key}


def mark_decedent(view: dict, case: Case) -> dict:
    """On a deceased account's case, the named decedent (or the first holder) isn't a survivor."""
    name = (case.application or {}).get("decedent") or (view["holders"][0]["name"] if view["holders"] else None)
    view["holders"] = [{**h, "deceased": h.get("name") == name} for h in view["holders"]]
    return view


def security_master(db: Session, securities: list[Security] | None = None) -> tuple[dict, dict, dict]:
    """(transferability records, prices, locations) from the securities table."""
    recs, px, loc = {}, {}, {}
    for s in securities if securities is not None else db.scalars(select(Security)).all():
        recs[s.id] = SecurityRecord(s.id, s.name, s.asset_type, dtc_eligible=s.dtc_eligible,
                                    fundserv_eligible=s.fundserv_eligible,
                                    accepted_on_platform=s.accepted and not s.exit_deadline,
                                    sponsor_agreement=s.sponsor_agreement, proprietary_to=s.proprietary_to,
                                    restricted=s.restricted_legend, sponsor=s.sponsor, isin=s.isin)
        px[s.id], loc[s.id] = s.price, s.location
    return recs, px, loc


def product_states(db: Session, securities: list[Security] | None = None) -> dict[str, str]:
    out = {}
    for c in db.scalars(select(Case).where(Case.kind == "product")).all():
        sid = (c.application or {}).get("security_id")
        out[sid] = {"AVAILABLE": "accepted", "DECLINED": "declined"}.get(c.status, "pending")
    for s in securities if securities is not None else db.scalars(select(Security)).all():
        if s.accepted:
            out.setdefault(s.id, "accepted")
    return out


def bank_links(db: Session, account_id: str) -> dict:
    return {b.id: {"bank": b.bank, "last4": b.last4, "owner": b.owner, "status": b.status}
            for b in db.scalars(select(BankLink).where(BankLink.account_id == account_id)).all()}


def active_grant(db: Session, account_id: str, capability: str | None) -> dict | None:
    if not capability:
        return None
    g = db.scalar(select(Grant).where(Grant.account_id == account_id, Grant.capability == capability,
                                      Grant.status == "ACTIVE").order_by(Grant.id.desc()))
    if not g:
        return None
    return {"status": "active", "capability": g.capability, "per_request": g.per_request, "per_day": g.per_day,
            "destinations": list(g.destinations or []), "expires_on": g.expires_on.isoformat()}


def client_requests(db: Session, account_id: str, exclude: int | None = None) -> list[dict]:
    out = []
    for c in db.scalars(select(Case).where(Case.kind == "transfer", Case.account_id == account_id)).all():
        req = c.application or {}
        if c.id == exclude or (req.get("requester") or {}).get("role") != "client":
            continue
        sub = next((e for e in (c.extra or {}).get("events", []) if e.get("type") == "submitted"), None)
        if sub:
            out.append({"on": sub["on"], "amount": float(req.get("amount") or 0)})
    return out


def doc_types(db: Session, case: Case) -> list[str]:
    return sorted({d.doc_type for d in db.scalars(select(Document).where(Document.case_id == case.id)).all()
                   if d.status in ("received", "in_good_order")})


def children(db: Session, case: Case) -> dict:
    out = {}
    for key, cid in ((case.extra or {}).get("children") or {}).items():
        c = db.get(Case, cid)
        if c:
            out[key] = {"id": c.id, "status": c.status, "account_id": c.account_id,
                        "completed_on": c.closed_at.date().isoformat() if c.closed_at else None}
    return out


def fee_basis(db: Session, a: Account, view: dict) -> dict | None:
    """The RIA's fee schedule for the account (the synthetic schedules the billing preview uses), for a final fee."""
    from synthetic.book import FEE_SCHEDULES, _blended
    sched = FEE_SCHEDULES.get(view["firm_crd"])
    if not sched or a.group == "self_directed":
        return None
    value = ledger.account_value(db, a.id)
    start, end = quarter_bounds(settings.today())      # in advance: this quarter's bill; in arrears: unbilled days
    return {"annual_rate": round(_blended(sched["tiers"], value), 5), "timing": sched["timing"],
            "period_start": start.isoformat(), "period_end": end.isoformat(), "value": value}


def firms_view(db: Session) -> dict:
    return {f.crd: {"name": f.legal_name, "status": f.status} for f in db.scalars(select(Firm)).all()}


def account_state(db: Session, case: Case, a: Account) -> lifecycle.AccountState:
    view = account_view(db, a)
    if case.kind == "deceased":
        view = mark_decedent(view, case)
    others = db.scalars(select(Account).where(Account.client_key == a.client_key, Account.id != a.id,
                                              Account.status == "OPEN")).all()
    return lifecycle.AccountState(
        as_of=settings.today(), account=view, balances=ledger.account_balances(db, a.id), prices=ledger.prices(db),
        children=children(db, case), fee=fee_basis(db, a, view),
        other_managed_open=sum(1 for o in others if o.group == "pure_ria"),
        self_directed_id=next((o.id for o in others if o.group == "self_directed"), None),
        documents=doc_types(db, case), firms=firms_view(db))


def firm_state(db: Session, case: Case, firm: Firm) -> lifecycle.AccountState:
    return lifecycle.AccountState(
        as_of=settings.today(), account={"id": f"FIRM-{firm.crd}", "name": firm.legal_name, "firm_crd": firm.crd,
                                         "status": firm.status},
        children=children(db, case), documents=doc_types(db, case), firms=firms_view(db))


def transfer_context(db: Session, case: Case) -> money.TransferContext:
    req = case.application
    a = db.get(Account, req["account_id"])
    view = account_view(db, a)
    balances = ledger.account_balances(db, a.id)
    cash = balances.get(CASH, {}).get("settled", 0.0)
    holdings = {k: v["settled"] for k, v in balances.items() if k != CASH and v.get("settled")}
    # Measure what's available as if this case hadn't posted yet: a replay after submission must not count the
    # case's own debit against it
    for asset, u in ledger.case_legs(db, case.id, a.id, "settled").items():
        if asset == CASH:
            cash -= ledger.rules.amount(u)
        else:
            holdings[asset] = holdings.get(asset, 0) - ledger.rules.amount(u)
    securities = db.scalars(select(Security)).all()
    recs, px, loc = security_master(db, securities)
    value = round(sum(sum(b.values()) * (1.0 if k == CASH else px.get(k, 0.0)) for k, b in balances.items()), 2)
    dest = db.get(Account, req["destination"]) if req.get("destination") else None
    return money.TransferContext(
        as_of=settings.today(), account=view, cash_available=cash, holdings=holdings,
        destination=account_view(db, dest) if dest else None, security_master=recs, prices=px, locations=loc,
        grant=active_grant(db, a.id, money.CAPABILITY.get(req["type"])), bank_links=bank_links(db, a.id),
        products=product_states(db, securities), flagged=FLAGGED_LIST,
        client_requests=client_requests(db, a.id, case.id), account_value=value)


# --------------------------------------------------------------------------- running a case
def run(db: Session, case: Case, actor: User | None):
    """Replay a release 2 case and apply the result. Re-runs (up to 3 times) while it opens children or changes
    records, so one action carries the case as far as it can go."""
    token = _running.set(_running.get() | {case.id})
    try:
        res = None
        for _ in range(3):
            res = _evaluate(db, case)
            changed = _apply(db, case, res, actor)
            if not changed:
                break
        return res
    finally:
        _running.reset(token)


def _evaluate(db: Session, case: Case):
    req, dec, ev = case.application or {}, case.decisions or {}, (case.extra or {}).get("events", [])
    today = settings.today()
    if case.kind == "transfer":
        return money.process_transfer(req, transfer_context(db, case), dec, ev)
    if case.kind == "entitlement":
        a = db.get(Account, req["account_id"])
        ctx = entitlements.GrantContext(today, account_view(db, a), req.get("requester_firm"), bank_links(db, a.id))
        return entitlements.process_grant(req, ctx, ev)
    if case.kind == "product":
        s = db.get(Security, req.get("security_id")) if req.get("security_id") else None
        already = {"accepted": s.accepted} if s else None
        return products.process_product(req, products.ProductContext(today, already, FLAGGED_LIST,
                                                                     doc_types(db, case)), dec, ev)
    if case.kind in ("offboarding", "deceased", "advisor_move"):
        a = db.get(Account, req["account_id"])
        fn = {"offboarding": lifecycle.process_offboarding, "deceased": lifecycle.process_deceased,
              "advisor_move": lifecycle.process_advisor_move}[case.kind]
        return fn(req, account_state(db, case, a), dec, ev)
    firm = db.scalar(select(Firm).where(Firm.crd == req["firm_crd"]))
    fn = lifecycle.process_merger if case.kind == "merger" else lifecycle.process_transition
    return fn(req, firm_state(db, case, firm), dec, ev)


def _apply(db: Session, case: Case, res, actor: User | None) -> bool:
    from .runner import sync
    changed = False
    extra = dict(case.extra or {})
    if res.record:
        extra["events"] = list(extra.get("events", [])) + [{"type": t, **p} for t, p in res.record.items()]
        changed = True
    case.extra = extra
    # A replay lists every entry the case should have posted; write only the ones it hasn't (one lookup, not one each)
    refs: dict[str, dict] = {}
    for e in res.entries:
        refs.setdefault(f"C{case.id}:{e['ref']}", e)
    done = set(db.scalars(select(LedgerEntry.ref).where(LedgerEntry.ref.in_(refs)))) if refs else set()
    new = [(ref, e) for ref, e in refs.items() if ref not in done]
    firms = ledger.firm_of(db, {leg.account for _r, e in new for leg in e["legs"] if leg.layer == "client"}) \
        if new else {}
    for ref, e in new:
        posted = ledger.post(db, ref, e["kind"], e["description"], e["legs"], e["on"], actor=actor, case_id=case.id,
                             account_id=case.account_id, relink=e.get("relink"), firms=firms)
        changed = changed or posted is not None
    for s in res.spawn:
        changed = _spawn(db, case, s, actor) or changed
    for eff in res.effects:
        changed = _effect(db, case, eff, actor) or changed
    previous = case.status
    sync(db, case, res, actor)
    if res.expected and case.queue == "in_transit":
        case.sla_due = datetime.combine(res.expected, time(23, 0), tzinfo=timezone.utc)
    if case.kind == "entitlement":
        _mirror_grant(db, case)
    if previous != case.status:
        notify_parents(db, case)
    return changed


def notify_parents(db: Session, case: Case) -> None:
    """A child's status changed: re-run each parent that isn't already running."""
    running = _running.get()
    for pid in (case.extra or {}).get("parents", []):
        parent = db.get(Case, pid)
        if parent is not None and parent.id not in running and parent.kind in R2_KINDS:
            run(db, parent, None)


def _link_parent(child: Case, parent: Case) -> None:
    extra = dict(child.extra or {})
    extra["parents"] = sorted(set(extra.get("parents", [])) | {parent.id})
    child.extra = extra


def _spawn(db: Session, case: Case, s: dict, actor: User | None) -> bool:
    kids = dict((case.extra or {}).get("children") or {})
    if s["key"] in kids:
        return False
    child = None
    if s["kind"] == "product":
        child = db.scalar(select(Case).where(Case.kind == "product", Case.target_id == s["security_id"]))
        if child is None:
            sec = db.get(Security, s["security_id"])
            app = {"security_id": s["security_id"], "description": s.get("description") or (sec.name if sec else ""),
                   "asset_type": s.get("asset_type") or (sec.asset_type if sec else "alternative"),
                   "sponsor": sec.sponsor if sec else None, "requested_by": f"Transfer #{case.id}",
                   "source": "From an incoming transfer's transferability review",
                   "reason": "The transfer can't move this position until product acceptance decides"}
            child = new_case(db, "product", f"Product acceptance: {app['description']}", app, actor,
                             firm_id=case.firm_id, target_id=s["security_id"])
    elif s["kind"] == "transfer":
        child = _spawn_transfer(db, case, s, actor)
    elif s["kind"] == "offboarding":
        a = db.get(Account, s["account_id"])
        app = {"account_id": a.id, "trigger": s.get("trigger", "ria_notice"), "reason": s.get("reason"),
               "requested_by": f"Case #{case.id}"}
        child = new_case(db, "offboarding", f"Client offboarding: {a.display_name}", app, actor, account=a)
    elif s["kind"] == "account":
        from .seed import create_account_case
        from .serialize import account_from
        app = account_from(s["application"])
        firm = db.scalar(select(Firm).where(Firm.crd == case.application["firm_crd"]))
        child = create_account_case(db, app, firm, actor)
    if child is None:
        return False
    _link_parent(child, case)
    kids[s["key"]] = child.id
    case.extra = {**(case.extra or {}), "children": kids}
    db.flush()
    from .runner import run_case
    run_case(db, child, actor)
    return True


def _spawn_transfer(db: Session, case: Case, s: dict, actor: User | None) -> Case | None:
    account_id = s.get("account_id") or case.account_id
    if s.get("account_key"):
        opened = db.get(Case, ((case.extra or {}).get("children") or {}).get(s["account_key"]))
        account_id = opened.account_id if opened else None
    a = db.get(Account, account_id) if account_id else None
    if a is None:
        return None
    kind = s.get("type") or ("loi_in" if s["key"] == "loi" else "acats_in")
    positions = s.get("positions") or []
    if s.get("full"):
        cash, holdings = ledger.settled(db, a.id)
        positions = [{"security_id": k, "quantity": q} for k, q in holdings.items() if q > 0]
        s = {**s, "cash": max(0.0, cash)}
    secs = {x.id: x for x in db.scalars(select(Security)).all()}
    positions = [{**p, "description": p.get("description") or (secs[p["security_id"]].name if p["security_id"] in secs
                                                                  else p["security_id"]),
                  "asset_type": p.get("asset_type") or (secs[p["security_id"]].asset_type
                                                        if p["security_id"] in secs else "equity")}
                 for p in positions]
    app = {"type": kind, "account_id": a.id, "positions": positions, "requester": {"role": "system"},
           "parent_case": case.id, "cash": s.get("cash") or 0, "amount": s.get("amount") or 0,
           "title_on_form": s.get("title_on_form")}
    if s.get("receiving_firm"):
        app["counterparty"] = {"institution": s["receiving_firm"]}
    if s.get("contra_firm"):
        app["counterparty"] = {"institution": s["contra_firm"]}
    if s.get("destination"):
        app["destination"] = s["destination"]
    title = f"{money.TYPES[kind]['label']}: {a.display_name}"
    return new_case(db, "transfer", title, app, actor, account=a)


def new_case(db: Session, kind: str, title: str, application: dict, actor: User | None, *, account: Account | None = None,
             firm_id: int | None = None, target_id: str | None = None) -> Case:
    c = Case(kind=kind, title=title, firm_id=account.firm_id if account else firm_id,
             account_id=account.id if account else None, target_id=target_id or (account.id if account else title[:60]),
             application=application, decisions={}, extra={"events": []}, created_by=actor.id if actor else None)
    db.add(c)
    db.flush()
    audit.record(db, actor, "case_created", "case", c.id, case_id=c.id, kind=kind,
                 account=account.id if account else None)
    return c


def _effect(db: Session, case: Case, eff: dict, actor: User | None) -> bool:
    t = eff["type"]
    if t == "close_account":
        a = db.get(Account, eff["account_id"])
        if a.status == "CLOSED":
            return False
        ledger.require_zero_or_raise(db, a.id)
        a.status, a.closed_at = "CLOSED", utcnow()
        for g in db.scalars(select(Grant).where(Grant.account_id == a.id, Grant.status != "REVOKED")).all():
            g.status = "REVOKED"
        audit.record(db, actor, "account_closed", "account", a.id, case_id=case.id)
        return True
    if t == "end_lpoa":
        a = db.get(Account, eff["account_id"])
        app = dict(a.application or {})
        maint = dict(app.get("maintenance") or {})
        if maint.get("lpoa_ended"):
            return False
        maint["lpoa_ended"] = settings.today().isoformat()
        app["maintenance"] = maint
        a.application = app
        audit.record(db, actor, "lpoa_ended", "account", a.id, case_id=case.id)
        return True
    if t == "relink":
        a = db.get(Account, eff["account_id"])
        firm = db.scalar(select(Firm).where(Firm.crd == eff["firm_crd"]))
        if a.firm_id == firm.id:
            return False
        if case.kind == "merger":                 # a merger's relink posts its ledger move here, per account
            old = db.get(Firm, a.firm_id)
            bal = {(asset, bucket): ledger.rules.units(q) for asset, b in ledger.account_balances(db, a.id).items()
                   for bucket, q in b.items() if q}
            legs = ledger.rules.relink(a.id, old.crd, firm.crd, bal)
            if legs:
                ledger.post(db, f"C{case.id}:relink-{a.id}", "relink", f"{a.id} moves from MASTER-{old.crd} to "
                            f"MASTER-{firm.crd}; no assets move", legs, settings.today(), actor=actor,
                            case_id=case.id, account_id=a.id, relink=(a.id, old.crd, firm.crd))
        old_firm = a.firm_id
        a.firm_id, a.rep_code = firm.id, eff.get("rep_code") or a.rep_code
        app = dict(a.application or {})
        maint = dict(app.get("maintenance") or {})
        maint.pop("lpoa_ended", None)
        app["maintenance"], app["lpoa_signed"] = maint, True
        a.application = app
        audit.record(db, actor, "account_relinked", "account", a.id, case_id=case.id, from_firm=old_firm,
                     to_firm=firm.crd, rep_code=a.rep_code)
        return True
    if t == "rename_firm":
        f = db.scalar(select(Firm).where(Firm.crd == eff["firm_crd"]))
        if not eff.get("name") or f.legal_name == eff["name"]:
            return False
        old, f.legal_name = f.legal_name, eff["name"]
        audit.record(db, actor, "firm_renamed", "firm", f.crd, case_id=case.id, previous=old, name=f.legal_name)
        return True
    if t == "offboard_firm":
        f = db.scalar(select(Firm).where(Firm.crd == eff["firm_crd"]))
        if f.status == "OFFBOARDED":
            return False
        f.status = "OFFBOARDED"
        for u in db.scalars(select(User).where(User.firm_id == f.id)).all():
            u.active = False
        audit.record(db, actor, "firm_offboarded", "firm", f.crd, case_id=case.id)
        return True
    if t == "accept_security":
        s = db.get(Security, eff["security_id"])
        if s is None:
            req = case.application
            s = Security(id=eff["security_id"], name=req.get("description") or eff["security_id"],
                         asset_type=req.get("asset_type", "alternative"), price=float(req.get("price") or 0),
                         location="Fund/SERV (transfer agent)" if req.get("asset_type") == "mutual_fund"
                         else "DTC" if req.get("asset_type") == "etf" else "Sponsor (non-traded)",
                         sponsor=req.get("sponsor"), valuation_source=req.get("valuation_source"))
            db.add(s)
        if s.accepted:
            return False
        s.accepted, s.settlement_method = True, eff.get("settlement")
        kind = s.asset_type
        s.dtc_eligible = s.dtc_eligible or kind == "etf"
        s.fundserv_eligible = s.fundserv_eligible or kind == "mutual_fund"
        s.sponsor_agreement = s.sponsor_agreement or kind not in ("etf", "mutual_fund", "equity")
        audit.record(db, actor, "security_accepted", "security", s.id, case_id=case.id)
        return True
    raise ValueError(f"Unknown effect {t}")


def _mirror_grant(db: Session, case: Case) -> None:
    req = case.application
    g = db.scalar(select(Grant).where(Grant.case_id == case.id))
    if case.status == "DECLINED":
        if g:
            g.status = "DECLINED"
        return
    if g is None:
        g = Grant(case_id=case.id, account_id=req["account_id"], capability=req["capability"],
                  per_request=float(req["per_request"]), per_day=float(req["per_day"]),
                  destinations=list(req.get("destinations") or []),
                  expires_on=datetime.fromisoformat(req["expires_on"]).date(), status=case.status,
                  granted_by=req.get("requested_by") or "RIA")
        db.add(g)
    g.status = case.status
