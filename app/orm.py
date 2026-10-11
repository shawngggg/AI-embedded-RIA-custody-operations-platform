"""
Database tables for the MVP.

Cases hold the application and the people's decisions; every run of the
onboarding engine is replayed from them, so a case's result can always be
reproduced. The audit table is append-only: the app inserts into it and never
updates or deletes a row.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


# Roles. Internal teams work in the workbench; RIA users and clients in the portal.
ROLES = {
    "ops_analyst": "Operations analyst",
    "ops_supervisor": "Operations supervisor",
    "aml_compliance": "AML compliance",
    "sanctions": "Sanctions team",
    "periodic_review": "Periodic review team",
    "platform_admin": "Platform administrator",
    "ria_admin": "RIA administrator",
    "ria_user": "RIA user",
    "client": "Client",
}
INTERNAL_ROLES = {"ops_analyst", "ops_supervisor", "aml_compliance", "sanctions", "periodic_review",
                  "platform_admin"}
PORTAL_ROLES = {"ria_admin", "ria_user", "client"}
# Granting one of these needs a second platform administrator to approve
DECISION_ROLES = {"ops_supervisor", "aml_compliance", "sanctions", "platform_admin"}


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


class AiUsage(Base):
    __tablename__ = "ai_usage"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
