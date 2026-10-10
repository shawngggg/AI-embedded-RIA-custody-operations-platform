"""
Restriction codes (Milestone 3).

A restriction code stops specific activity on an account and names the
function that owns it. Any function can place a code (first-line operations
places the sanctions hold, for example), but only the owning function can
remove it, and codes that need a decision from that function also need an
authorization reference, such as the sanctions team's determination.

Every placement, removal, and refused removal is written to an append-only
audit trail, so the record shows who tried what and when.

The catalog below is a reference design. Which activities each code blocks is
platform policy, noted per code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Function(Enum):
    OPERATIONS = "operations"
    PERIODIC_REVIEW = "periodic_review"
    AML_COMPLIANCE = "aml_compliance"
    SANCTIONS = "sanctions"
    FRAUD = "fraud"
    LEGAL = "legal"


class Activity(Enum):
    BUY = "buy"
    SELL = "sell"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    TRANSFER_OUT = "transfer_out"
    FEE_DEDUCTION = "fee_deduction"


ALL = frozenset(Activity)
OUTGOING = frozenset({Activity.WITHDRAWAL, Activity.TRANSFER_OUT})


@dataclass(frozen=True)
class CodeType:
    code: str
    description: str
    owner: Function
    blocks: frozenset
    requires_authorization: bool
    source: str


CATALOG = {c.code: c for c in [
    CodeType("CIPV", "Identity verification pending", Function.OPERATIONS, OUTGOING, False,
             "31 CFR 1023.220(a)(2)(ii) (verify within a reasonable time; platform policy limits "
             "outgoing activity until verified)"),
    CodeType("EDDP", "Enhanced due diligence pending approval", Function.AML_COMPLIANCE, ALL, True,
             "31 CFR 1023.210 (risk-based customer due diligence); platform policy"),
    CodeType("KYCR", "Periodic review overdue: no new activity until refreshed", Function.PERIODIC_REVIEW,
             frozenset({Activity.BUY, Activity.DEPOSIT}), False,
             "Platform policy (rolling review map, 03)"),
    CodeType("SANC", "Sanctions review hold", Function.SANCTIONS, ALL, True,
             "Platform policy (sanctions escalation map, 06); only the sanctions team releases"),
    CodeType("OFAC", "Blocked property: confirmed sanctions match", Function.SANCTIONS, ALL, True,
             "31 CFR Part 501; 31 CFR 501.603 (report blocked property to OFAC within 10 business days)"),
    CodeType("FRAUD", "Fraud hold", Function.FRAUD, OUTGOING, True,
             "Platform policy (fraud holds are the custodian's own decision)"),
    CodeType("LEGAL", "Legal process hold: levy, garnishment, or court order", Function.LEGAL, OUTGOING, True,
             "Platform policy (legal process is the custodian's own decision)"),
    CodeType("DECD", "Owner deceased: RIA authority has ended", Function.OPERATIONS,
             frozenset({Activity.BUY, Activity.SELL, Activity.WITHDRAWAL, Activity.TRANSFER_OUT,
                        Activity.FEE_DEDUCTION}), True,
             "Agency ends at the principal's death; platform policy (deceased client account map, 17)"),
]}


class RestrictionError(PermissionError):
    """A placement or removal the rules don't allow."""


@dataclass
class Restriction:
    restriction_id: str
    account_id: str
    code: str
    reason: str
    placed_by: str
    placed_by_function: Function
    placed_at: datetime
    active: bool = True
    removed_by: str | None = None
    removed_at: datetime | None = None
    removal_reason: str | None = None
    authorization: str | None = None

    @property
    def owner(self) -> Function:
        return CATALOG[self.code].owner

    def to_dict(self) -> dict:
        return {
            "restriction_id": self.restriction_id, "account_id": self.account_id, "code": self.code,
            "description": CATALOG[self.code].description, "owner": self.owner.value,
            "reason": self.reason, "placed_by": self.placed_by,
            "placed_by_function": self.placed_by_function.value,
            "placed_at": self.placed_at.isoformat(), "active": self.active,
            "removed_by": self.removed_by,
            "removed_at": self.removed_at.isoformat() if self.removed_at else None,
            "removal_reason": self.removal_reason, "authorization": self.authorization,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


@dataclass
class RestrictionRegistry:
    """In-memory restriction ledger with an append-only audit trail."""
    restrictions: dict[str, Restriction] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    _counter: int = 0

    def _log(self, event: str, r: Restriction | None, by: str, function: Function,
             at: datetime, **detail) -> None:
        record = {"event": event, "at": at.isoformat(), "by": by, "function": function.value}
        if r is not None:
            record.update({"restriction_id": r.restriction_id, "account_id": r.account_id, "code": r.code})
        record.update({k: v for k, v in detail.items() if v is not None})
        self.events.append(record)

    def place(self, account_id: str, code: str, reason: str, by: str, function: Function,
              at: datetime | None = None) -> Restriction:
        if code not in CATALOG:
            raise RestrictionError(f"Unknown restriction code '{code}'")
        if not reason or not reason.strip():
            raise RestrictionError("A restriction needs a reason")
        at = at or _now()
        self._counter += 1
        r = Restriction(f"R{self._counter:05d}", account_id, code, reason, by, function, at)
        self.restrictions[r.restriction_id] = r
        self._log("placed", r, by, function, at, reason=reason, owner=r.owner.value)
        return r

    def remove(self, restriction_id: str, by: str, function: Function, reason: str,
               authorization: str | None = None, at: datetime | None = None) -> Restriction:
        at = at or _now()
        r = self.restrictions.get(restriction_id)
        if r is None:
            raise RestrictionError(f"No restriction '{restriction_id}'")
        if not r.active:
            raise RestrictionError(f"Restriction {restriction_id} was already removed")
        code = CATALOG[r.code]
        if function is not code.owner:
            self._log("removal_denied", r, by, function, at,
                      detail=f"only {code.owner.value} can remove {r.code}")
            raise RestrictionError(f"Only {code.owner.value} can remove {r.code}; "
                                   f"{function.value} cannot")
        if code.requires_authorization and not (authorization and authorization.strip()):
            self._log("removal_denied", r, by, function, at,
                      detail=f"{r.code} removal needs an authorization reference")
            raise RestrictionError(f"Removing {r.code} needs an authorization reference")
        r.active = False
        r.removed_by, r.removed_at, r.removal_reason, r.authorization = by, at, reason, authorization
        self._log("removed", r, by, function, at, reason=reason, authorization=authorization)
        return r

    def active(self, account_id: str) -> list[Restriction]:
        return [r for r in self.restrictions.values() if r.account_id == account_id and r.active]

    def active_codes(self, account_id: str) -> list[str]:
        return [r.code for r in self.active(account_id)]

    def check(self, account_id: str, activity: Activity) -> tuple[bool, list[str]]:
        """Whether an activity is allowed, and which active codes block it."""
        blocking = [r.code for r in self.active(account_id) if activity in CATALOG[r.code].blocks]
        return (not blocking, blocking)

    def history(self, account_id: str) -> list[dict]:
        return [e for e in self.events if e.get("account_id") == account_id]
