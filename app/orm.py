"""
Database tables for the MVP.

Cases hold the application and the people's decisions; every run of the
onboarding engine is replayed from them, so a case's result can always be
reproduced. The audit table is append-only: the app inserts into it and never
updates or deletes a row.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, LargeBinary, String, Text, text
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
    "client_service": "Client service",
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


class AiUsage(Base):
    __tablename__ = "ai_usage"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
