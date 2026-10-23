"""
Database tables for the MVP.

Cases hold the application and the people's decisions; every run of the
onboarding engine is replayed from them, so a case's result can always be
reproduced. The audit table is append-only: the app inserts into it and never
updates or deletes a row.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (JSON, BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, LargeBinary,
                        String, Text, text)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt) -> str | None:
    """ISO 8601 with the UTC offset. SQLite hands back naive datetimes; they were stored in UTC."""
    if not dt:
        return None
    if isinstance(dt, datetime) and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# Roles. Internal teams work in the workbench; RIA users and clients in the portal.
ROLES = {
    "ops_analyst": "Operations analyst",
    "ops_supervisor": "Operations supervisor",
    "aml_compliance": "AML compliance",
    "sanctions": "Sanctions team",
    "periodic_review": "Periodic review team",
    "ria_service": "RIA service team (licensed)",
    "ria_service_lead": "RIA service team lead (licensed)",
    "platform_admin": "Platform administrator",
    "ria_admin": "RIA administrator",
    "ria_user": "RIA user",
    "client": "Client",
}
INTERNAL_ROLES = {"ops_analyst", "ops_supervisor", "aml_compliance", "sanctions", "periodic_review",
                  "ria_service", "ria_service_lead", "platform_admin"}
PORTAL_ROLES = {"ria_admin", "ria_user", "client"}
# Granting one of these needs a second platform administrator to approve
DECISION_ROLES = {"ops_supervisor", "aml_compliance", "sanctions", "ria_service", "ria_service_lead", "platform_admin"}
LICENSED_ROLES = {"ria_service", "ria_service_lead"}
OPS_ROLES = {"ops_analyst", "ops_supervisor"}

# Teams inside custody operations (Shawn, Oct. 13, 2026). Each operations user belongs to one: it decides which
# queues are theirs and who takes in new applications. EDD sits with AML compliance and rolling review is its own
# team (separate roles); the relationship team, legal, sales, and engineering are outside the workbench for now.
OPS_TEAMS = {
    "onboarding": "Onboarding",
    "cip": "CIP and identity verification",
    "asset_transfer": "Asset transfer",
    "trade": "Trade",
    "billing": "Billing",
    "escheatment": "Escheatment",
    "ira": "IRA and retirement",
    "client_reporting": "Client reporting",
    "client_service": "Client service and account maintenance",
    # Release 2 (Oct. 22, 2026): the teams that run cash settlement and reconciliation, product acceptance, and RIA
    # transitions (maps 12, 13, 22, 23, 24)
    "settlement": "Settlement and reconciliation",
    "product": "Product acceptance",
    "transitions": "RIA transitions",
}

# FINRA registrations. The custodian runs the trading platform, so it is a FINRA member broker-dealer: the
# RIA service team, which works with RIAs on trading, transfers, and account acceptance, is licensed.
# Operations prepares cases and needs no license. (Synthetic in the demo.)
REGISTRATIONS = {
    "S7": "Series 7 (General Securities Representative)",
    "S63": "Series 63 (Uniform Securities Agent)",
    "S24": "Series 24 (General Securities Principal)",
    "S9_10": "Series 9/10 (General Securities Sales Supervisor)",
    "S4": "Series 4 (Registered Options Principal)",
}
SHORT_REGISTRATION = {"S7": "Series 7", "S63": "Series 63", "S24": "Series 24", "S9_10": "Series 9/10",
                      "S4": "Series 4"}
# Accepting an account (FINRA Rule 4512) is a principal's act; options approval (Rule 2360) needs Series 4.
PRINCIPAL_REGISTRATIONS = {"S24", "S9_10"}
# A principal registration builds on a representative registration
NEEDS_SERIES_7 = {"S24", "S9_10", "S4"}


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(300), nullable=True)
    role: Mapped[str] = mapped_column(String(40))
    firm_id: Mapped[int | None] = mapped_column(ForeignKey("firms.id"), nullable=True)
    client_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    demo_note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    registrations: Mapped[list] = mapped_column(JSON, default=list,          # e.g. ["S7", "S63", "S24"]
                                                server_default=text("'[]'"))
    individual_crd: Mapped[str | None] = mapped_column(String(20), nullable=True)
    team: Mapped[str | None] = mapped_column(String(40), nullable=True)       # OPS_TEAMS key, operations only
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RoleGrant(Base):
    """A request to give a user a role; decision roles need a second approver."""
    __tablename__ = "role_grants"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="pending")   # pending | approved | rejected
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    registrations: Mapped[list | None] = mapped_column(JSON, nullable=True)  # registrations granted with the role
    individual_crd: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Firm(Base):
    __tablename__ = "firms"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    crd: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    legal_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="ONBOARDING")    # ONBOARDING | ACTIVE | DECLINED
    application: Mapped[dict] = mapped_column(JSON)
    reliance_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_tier: Mapped[str | None] = mapped_column(String(10), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    firm_id: Mapped[int] = mapped_column(ForeignKey("firms.id"))
    client_key: Mapped[str] = mapped_column(String(80), index=True)   # identifies the client across accounts
    display_name: Mapped[str] = mapped_column(String(200))
    group: Mapped[str] = mapped_column(String(20))                    # pure_ria | self_directed
    registration_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="PENDING")  # PENDING | OPEN | CLOSED
    path: Mapped[str | None] = mapped_column(String(2), nullable=True)
    risk_tier: Mapped[str | None] = mapped_column(String(10), nullable=True)
    application: Mapped[dict] = mapped_column(JSON)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    change_history: Mapped[list] = mapped_column(JSON, default=list)   # [[change_type, iso date], ...]
    rep_code: Mapped[str | None] = mapped_column(String(20), nullable=True)   # the advisor's code at the RIA
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))                     # firm | account | change
    title: Mapped[str] = mapped_column(String(240))
    firm_id: Mapped[int | None] = mapped_column(ForeignKey("firms.id"), nullable=True)
    account_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    target_id: Mapped[str] = mapped_column(String(60), index=True)    # where restriction codes sit
    status: Mapped[str] = mapped_column(String(40), default="NEW")
    queue: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    application: Mapped[dict] = mapped_column(JSON)
    decisions: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)           # extraction result, change confirmation
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    queue_entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    manual_steps: Mapped[int] = mapped_column(Integer, default=0)
    nigo_count: Mapped[int] = mapped_column(Integer, default=0)


class CaseTransition(Base):
    __tablename__ = "case_transitions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    to_status: Mapped[str] = mapped_column(String(40))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PendingDecision(Base):
    """Maker-checker: one person proposes a decision, a second person approves it."""
    __tablename__ = "pending_decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    decision_type: Mapped[str] = mapped_column(String(40))
    value: Mapped[str] = mapped_column(String(40))
    reference: Mapped[str] = mapped_column(String(80))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")   # pending | approved | rejected
    proposed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Restriction(Base):
    __tablename__ = "restrictions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[str] = mapped_column(String(60), index=True)
    code: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str] = mapped_column(Text)
    owner: Mapped[str] = mapped_column(String(30))
    source: Mapped[str] = mapped_column(String(10))                   # case | manual | review
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True)
    placed_by: Mapped[str] = mapped_column(String(120))
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    removed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    removal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    authorization: Mapped[str | None] = mapped_column(String(80), nullable=True)


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_id: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    subject_name: Mapped[str] = mapped_column(String(200))
    risk_tier: Mapped[str] = mapped_column(String(10))
    next_review: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="scheduled")
    requested_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    response_due: Mapped[date | None] = mapped_column(Date, nullable=True)
    trigger: Mapped[str] = mapped_column(String(30), default="scheduled")
    restriction_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    history: Mapped[list] = mapped_column(JSON, default=list)


class AuditEvent(Base):
    """Append-only: rows are inserted, never updated or deleted."""
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_name: Mapped[str] = mapped_column(String(120))
    actor_role: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(60), index=True)
    target_type: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[str] = mapped_column(String(60), index=True)
    case_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class Document(Base):
    """
    A document received for a case: the evidence behind an item marked received. The file is kept with its
    SHA-256 fingerprint; a person in operations checks it and marks it in good order (or not).
    """
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    target_id: Mapped[str] = mapped_column(String(60), index=True)
    doc_type: Mapped[str] = mapped_column(String(40))
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    channel: Mapped[str] = mapped_column(String(20))                 # portal | email | fax | mail | intake
    received_on: Mapped[date] = mapped_column(Date)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_by_name: Mapped[str] = mapped_column(String(120))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(30), default="received")
    # received | in_good_order | not_in_good_order | superseded (another copy was accepted for the same item)
    reviewed_by_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    checks: Mapped[list] = mapped_column(JSON, default=list)        # automated pre-checks on the file
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class OpsRiskEvent(Base):
    """
    A risk event (operational, regulatory, or financial): logged so the owning team agrees a remediation path
    (training, a technology change, a procedure update, or a regulatory report) and closes it. See risk_register.
    """
    __tablename__ = "ops_risk_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(60))
    risk_type: Mapped[str] = mapped_column(String(20), default="operational", server_default="operational")
    source: Mapped[str] = mapped_column(String(20), default="manual", server_default="manual")
    dedupe_key: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True)
    # The function the risk pertains to (risk_register.AREAS): each person sees their own area's events
    area: Mapped[str] = mapped_column(String(30), default="onboarding", server_default="onboarding", index=True)
    reported_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)
    target_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    summary: Mapped[str] = mapped_column(Text)
    detected_by: Mapped[str] = mapped_column(String(120))
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    owner: Mapped[str] = mapped_column(String(40))                      # role that owns the follow-up
    status: Mapped[str] = mapped_column(String(30), default="open")     # open | remediation_agreed | closed
    remediation: Mapped[str | None] = mapped_column(String(30), nullable=True)  # training | technology | procedure | none
    remediation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Channel(Base):
    """A team channel. `roles` limits who can read it (sanctions data stays with the sanctions team); empty means
    every internal team member."""
    __tablename__ = "channels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(200), default="")
    roles: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChannelMessage(Base):
    """A message in a channel. Kept as written: messages are never edited or deleted (business records)."""
    __tablename__ = "channel_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    refs: Mapped[list] = mapped_column(JSON, default=list)        # [{"type": "case", "id": 16}, {"type": "account", ...}]
    mentions: Mapped[list] = mapped_column(JSON, default=list)    # user ids
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ChannelRead(Base):
    """How far each person has read each channel, for unread counts."""
    __tablename__ = "channel_reads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"))
    last_read_id: Mapped[int] = mapped_column(Integer, default=0)


class OutboundEmail(Base):
    """Every notice the custodian emails goes through this outbox first; a provider (Microsoft 365) sends it.
    In the demo, mail is held here: the recipients are synthetic."""
    __tablename__ = "outbound_emails"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    to_address: Mapped[str] = mapped_column(String(200))
    to_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    template: Mapped[str] = mapped_column(String(40))
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")     # held | queued | sent | failed
    provider: Mapped[str] = mapped_column(String(20), default="outbox")   # outbox | m365
    provider_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AiSuggestion(Base):
    """
    A value the AI proposed for a case that passed the controls gate, and what a person in onboarding did with it
    (PRD guardrail 6): accepted it unchanged, edited it, or rejected it. The AI suggestion acceptance metric is the
    share accepted unchanged; edits and rejections, with the reviewer's reason, are what the next evaluation round
    learns from. Reviewed rows outlive a demo reset (the case link is cleared, the record of the review stays).
    """
    __tablename__ = "ai_suggestions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)
    case_label: Mapped[str] = mapped_column(String(240))
    field: Mapped[str] = mapped_column(String(40))
    suggested: Mapped[dict] = mapped_column(JSON)       # {"value": text} or, for a person, {name, role, ownership_pct}
    quote: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(20))       # live | illustrative (hand-written demo output, not counted)
    model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")   # pending | accepted | edited | rejected
    final: Mapped[dict | None] = mapped_column(JSON, nullable=True)     # the value the reviewer set, same shape
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_by_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AiUsage(Base):
    __tablename__ = "ai_usage"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)


# --- Release 2: the ledger and what it serves ---------------------------------------------------------------------

class LedgerEntry(Base):
    """One balanced posting (custody/ledger.py validates it before it's written). `ref` makes a replay idempotent:
    a workflow lists every entry it should have posted, and the app writes only the ones it hasn't."""
    __tablename__ = "ledger_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ref: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)
    account_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    effective_date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(120))


class LedgerLeg(Base):
    """A leg of an entry at one layer: client (ACC-...), master (MASTER-crd), or gl (a chart number). Units are
    millionths of a dollar or of a share, so every amount is exact."""
    __tablename__ = "ledger_legs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("ledger_entries.id"), index=True)
    layer: Mapped[str] = mapped_column(String(10))
    account: Mapped[str] = mapped_column(String(40), index=True)
    asset: Mapped[str] = mapped_column(String(20))
    bucket: Mapped[str] = mapped_column(String(12))
    units: Mapped[int] = mapped_column(BigInteger)


class Security(Base):
    """The security master: what the platform can hold, where, and at what price (synthetic)."""
    __tablename__ = "securities"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    ticker: Mapped[str | None] = mapped_column(String(12), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    asset_type: Mapped[str] = mapped_column(String(20))
    price: Mapped[float] = mapped_column(Float, default=0.0)
    price_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    location: Mapped[str] = mapped_column(String(40), default="DTC")
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    dtc_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    fundserv_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    sponsor_agreement: Mapped[bool] = mapped_column(Boolean, default=False)
    proprietary_to: Mapped[str | None] = mapped_column(String(80), nullable=True)
    restricted_legend: Mapped[bool] = mapped_column(Boolean, default=False)
    sponsor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    isin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    settlement_method: Mapped[str | None] = mapped_column(String(60), nullable=True)
    valuation_source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    purchase_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    exit_deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    policy_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Valuation(Base):
    """A valuation statement or NAV for an illiquid product; a person checks it when PA-004 flags it."""
    __tablename__ = "valuations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    security_id: Mapped[str] = mapped_column(ForeignKey("securities.id"), index=True)
    value: Mapped[float] = mapped_column(Float)
    as_of: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="posted")     # posted | flagged | rejected
    checks: Mapped[list] = mapped_column(JSON, default=list)
    entered_by: Mapped[str] = mapped_column(String(120))
    decided_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BankLink(Base):
    """A bank account in the client's name: money moves to or from it only once it's verified (map 05)."""
    __tablename__ = "bank_links"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    bank: Mapped[str] = mapped_column(String(120))
    last4: Mapped[str] = mapped_column(String(4))
    owner: Mapped[str] = mapped_column(String(200))
    method: Mapped[str] = mapped_column(String(20), default="micro_deposits")   # micro_deposits | instant
    status: Mapped[str] = mapped_column(String(20), default="pending")         # pending | verified | failed | closed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    added_by: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Grant(Base):
    """A client capability the RIA granted on the self-directed account, mirrored from its entitlement case."""
    __tablename__ = "grants"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), unique=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    capability: Mapped[str] = mapped_column(String(20))
    per_request: Mapped[float] = mapped_column(Float)
    per_day: Mapped[float] = mapped_column(Float)
    destinations: Mapped[list] = mapped_column(JSON, default=list)
    expires_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(30))       # the entitlement case's status
    granted_by: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OneTimeCode(Base):
    """A one-time passcode for a client's self-service request (ENT-002). Only its hash is kept."""
    __tablename__ = "one_time_codes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(40))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BankReceipt(Base):
    """Money that arrived at the settlement bank without a request: an incoming wire or ACH credit, matched to an
    account by its memo (STL-001), or held in suspense until someone applies or returns it."""
    __tablename__ = "bank_receipts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    received_on: Mapped[date] = mapped_column(Date, index=True)
    channel: Mapped[str] = mapped_column(String(20))           # Fedwire | ACH
    amount: Mapped[float] = mapped_column(Float)
    sender: Mapped[str] = mapped_column(String(200))
    memo: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="unapplied")   # unapplied | applied | returned
    account_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    decided_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class SettlementRun(Base):
    """One run of the daily cash settlement cycle (map 12), with what each step found."""
    __tablename__ = "settlement_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_date: Mapped[date] = mapped_column(Date, index=True)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    run_by: Mapped[str] = mapped_column(String(120))
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class StreetItem(Base):
    """A difference the street (DTC, a transfer agent, a sponsor, the settlement bank) reports against the books,
    from the simulator that stands in for their files, and its handling as a reconciliation break (map 13)."""
    __tablename__ = "street_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    location: Mapped[str] = mapped_column(String(10))          # GL location account: 1500 | 1510 | 1520 | 1010
    asset: Mapped[str] = mapped_column(String(20))
    units: Mapped[int] = mapped_column(BigInteger)             # street minus books
    pattern: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(Text)
    account_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    case_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opened_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="open")     # open | proposed | resolved
    route: Mapped[str | None] = mapped_column(String(20), nullable=True)
    proposal: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    escalated: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReconRun(Base):
    """One reconciliation run (map 13): what matched, the breaks, and whether the books were certified."""
    __tablename__ = "recon_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_date: Mapped[date] = mapped_column(Date, index=True)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    run_by: Mapped[str] = mapped_column(String(120))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    certified_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    certified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
