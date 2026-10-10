"""
Policy-as-code rules engine for the onboarding module (Milestone 2).

Carries forward the design of the onboarding execution engine (engine.py v2):
  - Rules live as data, not code. The evaluator is generic: rules grow, the
    engine does not.
  - Every rule carries a version, an effective date, an optional expiry date,
    and a cited source. Only the versions in force on the evaluation date
    apply, so a policy change is dated rather than overwritten and past
    decisions stay reproducible.
  - Defensive by default: a rule that names an unknown field or operator, or
    that can't compare its values, is skipped with a warning instead of
    crashing the run.

New in this module:
  - A rule can test several conditions at once ("all": every one must match).
  - evaluate_all() collects every matching rule instead of stopping at the
    first, so one review produces one complete list of findings (for example,
    every missing item goes to the RIA in a single request).
  - Computed fields are passed in by each rule set, so the engine itself holds
    no domain logic.
  - Each finding names the function that has to act on it (operations,
    compliance, sanctions team).

Rule content and citations are a reference design for a synthetic platform,
not legal advice or production compliance software.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Callable, Iterable

POLICY_VERSION = "2026.10"


class Disposition(Enum):
    CLEAR = "clear"
    REFER = "refer"          # needs items or a person's review before it can proceed
    ESCALATE = "escalate"    # goes to a second-line function (compliance, sanctions team)
    BLOCK = "block"          # cannot proceed


SEVERITY = {
    Disposition.CLEAR: 0,
    Disposition.REFER: 1,
    Disposition.ESCALATE: 2,
    Disposition.BLOCK: 3,
}


def worst(dispositions: Iterable[Disposition]) -> Disposition:
    """The most severe disposition in a set; CLEAR when the set is empty."""
    result = Disposition.CLEAR
    for d in dispositions:
        if SEVERITY[d] > SEVERITY[result]:
            result = d
    return result


@dataclass
class Decision:
    """One finding: the verdict, why, which rule version decided it, and its source."""
    disposition: Disposition
    reason: str
    rule_id: str
    rule_version: str
    source: str
    required_documents: list[str] = field(default_factory=list)
    owner: str = "operations"

    def to_dict(self) -> dict:
        return {
            "disposition": self.disposition.value,
            "reason": self.reason,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "source": self.source,
            "required_documents": list(self.required_documents),
            "owner": self.owner,
        }


@dataclass
class Evaluation:
    """The result of running a rule set: every finding plus the warnings raised."""
    as_of: date
    decisions: list[Decision]
    warnings: list[str] = field(default_factory=list)
    policy_version: str = POLICY_VERSION

    @property
    def disposition(self) -> Disposition:
        return worst(d.disposition for d in self.decisions)

    @property
    def required_documents(self) -> list[str]:
        seen: list[str] = []
        for d in self.decisions:
            for doc in d.required_documents:
                if doc not in seen:
                    seen.append(doc)
        return seen

    @property
    def rule_ids(self) -> list[str]:
        return [d.rule_id for d in self.decisions]

    def to_dict(self) -> dict:
        return {
            "evaluation_date": self.as_of.isoformat(),
            "policy_version": self.policy_version,
            "disposition": self.disposition.value,
            "decisions": [d.to_dict() for d in self.decisions],
            "required_documents": self.required_documents,
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# Which rule versions are in force
# ---------------------------------------------------------------------------

def in_force(rule: dict, as_of: date) -> bool:
    start = date.fromisoformat(rule["effective_date"])
    end = rule.get("expiry_date")
    if start > as_of:
        return False
    if end and as_of >= date.fromisoformat(end):
        return False
    return True


def active_rules(rules: list[dict], as_of: date) -> list[dict]:
    """
    The rule versions in force on the evaluation date, in library order.
    If more than one version of a rule is in force, the highest version wins.
    """
    chosen: dict[str, dict] = {}
    order: list[str] = []
    for rule in rules:
        if not in_force(rule, as_of):
            continue
        rid = rule["rule_id"]
        if rid not in chosen:
            order.append(rid)
            chosen[rid] = rule
        elif int(rule["version"]) > int(chosen[rid]["version"]):
            chosen[rid] = rule
    return [chosen[rid] for rid in order]


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------

def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in ("", "—")
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "equals": lambda a, b: a == b,
    "not_equals": lambda a, b: a != b,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
    "less_than": lambda a, b: a < b,
    "less_than_or_equal": lambda a, b: a <= b,
    "greater_than": lambda a, b: a > b,
    "greater_than_or_equal": lambda a, b: a >= b,
    "is_blank": lambda a, _b: _is_blank(a),
    "is_present": lambda a, _b: not _is_blank(a),
    "contains_any": lambda a, b: bool(set(a or []) & set(b)),
}

ComputedFields = dict[str, Callable[[Any, date], Any]]


def resolve_field(subject: Any, name: str, as_of: date,
                  computed: ComputedFields | None = None) -> tuple[bool, Any]:
    """
    Read the field a rule names. Computed fields come first, then attributes or
    dict keys, with dotted paths allowed ("ria.active"). Returns (found, value).
    """
    if computed and name in computed:
        return True, computed[name](subject, as_of)
    value = subject
    for part in name.split("."):
        if isinstance(value, dict):
            if part not in value:
                return False, None
            value = value[part]
        elif hasattr(value, part):
            value = getattr(value, part)
        else:
            return False, None
    return True, value


def conditions_of(rule: dict) -> list[dict]:
    if "all" in rule:
        return rule["all"]
    return [{"field": rule["field"], "operator": rule["operator"], "value": rule.get("value")}]


def check_condition(subject: Any, rule: dict, condition: dict, as_of: date,
                    computed: ComputedFields | None, warnings: list[str]) -> bool:
    label = f"rule {rule['rule_id']} v{rule['version']}"
    found, actual = resolve_field(subject, condition["field"], as_of, computed)
    if not found:
        warnings.append(f"{label} references unknown field '{condition['field']}'; skipped")
        return False
    op = OPERATORS.get(condition["operator"])
    if op is None:
        warnings.append(f"{label} uses unknown operator '{condition['operator']}'; skipped")
        return False
    try:
        return bool(op(actual, condition.get("value")))
    except TypeError:
        warnings.append(f"{label} could not compare '{condition['field']}'; skipped")
        return False


def matches(subject: Any, rule: dict, as_of: date,
            computed: ComputedFields | None, warnings: list[str]) -> bool:
    for condition in conditions_of(rule):
        if not check_condition(subject, rule, condition, as_of, computed, warnings):
            return False
    return True


def decision_from(rule: dict) -> Decision:
    return Decision(
        disposition=rule["disposition"],
        reason=rule["reason"],
        rule_id=rule["rule_id"],
        rule_version=str(rule["version"]),
        source=rule["source"],
        required_documents=list(rule.get("required_documents", [])),
        owner=rule.get("owner", "operations"),
    )


# ---------------------------------------------------------------------------
# Evaluators
# ---------------------------------------------------------------------------

def evaluate_all(subject: Any, rules: list[dict], as_of: date,
                 computed: ComputedFields | None = None) -> Evaluation:
    """Every rule in force that matches becomes a finding."""
    warnings: list[str] = []
    decisions = [decision_from(r) for r in active_rules(rules, as_of)
                 if matches(subject, r, as_of, computed, warnings)]
    return Evaluation(as_of=as_of, decisions=decisions, warnings=warnings)


def evaluate_first(subject: Any, rules: list[dict], as_of: date,
                   computed: ComputedFields | None = None,
                   default: Decision | None = None) -> Evaluation:
    """
    The first matching rule decides (library order is precedence), as in the
    onboarding execution engine. When nothing matches, the default decision
    applies, if one is given.
    """
    warnings: list[str] = []
    for rule in active_rules(rules, as_of):
        if matches(subject, rule, as_of, computed, warnings):
            return Evaluation(as_of=as_of, decisions=[decision_from(rule)], warnings=warnings)
    return Evaluation(as_of=as_of, decisions=[default] if default else [], warnings=warnings)


# ---------------------------------------------------------------------------
# Library checks
# ---------------------------------------------------------------------------

REQUIRED_KEYS = ("rule_id", "version", "effective_date", "source", "disposition", "reason")


def validate_rules(rules: list[dict]) -> list[str]:
    """Problems with a rule library: missing keys, bad dates, unknown operators, duplicates."""
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for i, rule in enumerate(rules):
        label = f"{rule.get('rule_id', f'#{i}')} v{rule.get('version', '?')}"
        for key in REQUIRED_KEYS:
            if key not in rule:
                problems.append(f"{label}: missing '{key}'")
        if "all" not in rule and not ("field" in rule and "operator" in rule):
            problems.append(f"{label}: needs a field and operator, or an 'all' list")
        for condition in conditions_of(rule) if ("all" in rule or "field" in rule) else []:
            if condition.get("operator") not in OPERATORS:
                problems.append(f"{label}: unknown operator '{condition.get('operator')}'")
        try:
            start = date.fromisoformat(rule["effective_date"])
            if rule.get("expiry_date") and date.fromisoformat(rule["expiry_date"]) <= start:
                problems.append(f"{label}: expiry date is not after the effective date")
        except (KeyError, ValueError, TypeError):
            problems.append(f"{label}: effective or expiry date is not an ISO date")
        if not isinstance(rule.get("disposition"), Disposition):
            problems.append(f"{label}: disposition must be a Disposition")
        key = (str(rule.get("rule_id")), str(rule.get("version")))
        if key in seen:
            problems.append(f"{label}: duplicate rule id and version")
        seen.add(key)
    return problems
