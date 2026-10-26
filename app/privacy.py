"""
Regulation S-P incident response (17 CFR 248.30(a)(3), as amended in 2024; compliance dates Dec. 3, 2025 for larger
firms and June 3, 2026 for smaller ones). The steps the rule requires, in order:

1. Assess: the nature and scope of the incident, the systems and the customer information involved.
2. Contain and control it.
3. Decide: notify every affected customer as soon as practicable, and no later than 30 days after becoming aware that
   sensitive customer information was, or is reasonably likely to have been, accessed or used without authorization;
   or document, after a reasonable investigation, that it hasn't been and isn't reasonably likely to be used in a way
   that causes substantial harm or inconvenience.
4. Send the notices: what happened, what information, what the firm has done, how to reach it, and what the customer
   can do (watch statements, a fraud alert or credit freeze, the FTC's identity theft site).

A service provider's incident counts too: the provider owes the custodian notice within 72 hours of becoming aware.
Each incident is also a regulatory risk event in the access and authentication area.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .config import settings
from .orm import Account, PrivacyIncident, User
from .risk_register import log_event

NOTICE_DAYS = 30
PROVIDER_HOURS = 72
SOURCE = "Regulation S-P, 17 CFR 248.30(a)(3) (incident response) and (a)(4) (customer notice), as amended in 2024"


def due(i: PrivacyIncident) -> date:
    return i.aware_on + timedelta(days=NOTICE_DAYS)


def provider_late(i: PrivacyIncident) -> bool | None:
    if not (i.provider_aware_at and i.provider_notified_at):
        return None
    aware = i.provider_aware_at if i.provider_aware_at.tzinfo else i.provider_aware_at.replace(tzinfo=timezone.utc)
    told = i.provider_notified_at if i.provider_notified_at.tzinfo else i.provider_notified_at.replace(tzinfo=timezone.utc)
    return told - aware > timedelta(hours=PROVIDER_HOURS)


def to_dict(i: PrivacyIncident) -> dict:
    today = settings.today()
    open_notice = i.determination != "no_notice" and not i.notices_sent_on
    return {"id": i.id, "ref": i.ref, "summary": i.summary, "status": i.status, "aware_on": i.aware_on.isoformat(),
            "reported_by": i.reported_by, "service_provider": i.service_provider,
            "provider_late": provider_late(i), "accounts": i.accounts, "information": i.information,
            "contained_on": i.contained_on.isoformat() if i.contained_on else None, "containment": i.containment,
            "determination": i.determination, "determination_reason": i.determination_reason,
            "determined_by": i.determined_by, "notice_due": due(i).isoformat(),
            "days_left": (due(i) - today).days if open_notice else None,
            "overdue": open_notice and today > due(i),
            "notices_sent_on": i.notices_sent_on.isoformat() if i.notices_sent_on else None,
            "notices_sent": i.notices_sent, "risk_event_id": i.risk_event_id}


def open_incident(db: Session, actor: User, *, summary: str, aware_on: date, accounts: list[str], information: str,
                  service_provider: str | None = None, provider_aware_at: datetime | None = None,
                  provider_notified_at: datetime | None = None) -> PrivacyIncident:
    n = len(db.scalars(select(PrivacyIncident.id)).all()) + 1
    i = PrivacyIncident(ref=f"PI-{n:03d}", summary=summary, aware_on=aware_on, reported_by=actor.display_name,
                        accounts=sorted(set(accounts)), information=information, service_provider=service_provider,
                        provider_aware_at=provider_aware_at, provider_notified_at=provider_notified_at)
    db.add(i)
    db.flush()
    e = log_event(db, actor, category="Possible data incident (Reg S-P)", risk_type="regulatory", source="privacy",
                  area="access", detected_by=actor.display_name,
                  summary=f"{i.ref}: {summary} Customer notice due by {due(i).isoformat()} unless the investigation "
                          "documents that harm isn't reasonably likely.", dedupe_key=f"privacy:{i.ref}")
    i.risk_event_id = e.id if e else None
    audit.record(db, actor, "privacy_incident_opened", "privacy_incident", i.ref, accounts=i.accounts,
                 aware_on=aware_on.isoformat(), provider=service_provider)
    return i


def contain(db: Session, actor: User, i: PrivacyIncident, note: str) -> None:
    i.status, i.contained_on, i.containment = "contained", settings.today(), note
    audit.record(db, actor, "privacy_incident_contained", "privacy_incident", i.ref, note=note)


def determine(db: Session, actor: User, i: PrivacyIncident, decision: str, reason: str) -> None:
    i.determination, i.determination_reason, i.determined_by = decision, reason, actor.display_name
    if decision == "no_notice":
        i.status = "closed"
    audit.record(db, actor, "privacy_incident_determined", "privacy_incident", i.ref, decision=decision,
                 reason=reason, basis=SOURCE)


def send_notices(db: Session, actor: User, i: PrivacyIncident) -> int:
    from .mail import client_notice
    sent = 0
    for acct_id in i.accounts:
        a = db.get(Account, acct_id)
        if a is None:
            continue
        sent += client_notice(db, a, template="privacy_notice", subject="Important: a security incident involving "
                              "your information",
                              text=f"We're writing about a security incident ({i.ref}) we became aware of on "
                                   f"{i.aware_on:%B} {i.aware_on.day}, {i.aware_on.year}.\n\nWhat happened: {i.summary}\n"
                                   f"What information: {i.information or 'account information'}\n"
                                   f"What we've done: {i.containment or 'We contained the incident.'}\n\n"
                                   "What you can do: watch your statements and tell us about anything you don't "
                                   "recognize; consider a fraud alert or a credit freeze with the credit bureaus; "
                                   "and see IdentityTheft.gov (the FTC) for steps to protect yourself.\n\n"
                                   "Call us at the number on your statement with any questions.")
    i.notices_sent_on, i.notices_sent, i.status = settings.today(), sent, "closed"
    audit.record(db, actor, "privacy_notices_sent", "privacy_incident", i.ref, notices=sent,
                 on_time=settings.today() <= due(i))
    return sent
