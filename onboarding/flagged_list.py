"""
First-line flagged-list check (Milestone 4).

First-line operations checks the parties on an application or instruction
(owners, control persons, account holders), the financial institutions that
send or receive money, and the products being bought or transferred against
the flagged list issued by the sanctions team. On a possible match it places
a sanctions hold and escalates. It never decides: the sanctions team makes the
determination and is the only function that can release the hold (sanctions
escalation map, 06).

Matching, kept deliberately simple and explainable:
  - an exact identifier match (BIC, ABA routing number, CUSIP, ISIN, tax ID)
    is a hit
  - a name match after normalization (case, accents, punctuation, legal-entity
    suffixes, word order) at or above the threshold is a possible match
  - an address or residence in a comprehensively sanctioned jurisdiction is a
    hit
  - an entity whose flagged owners together hold 50% or more is a hit (OFAC's
    50 Percent Rule)

Secondary identifiers (date of birth, country) are compared and packaged as
evidence for the sanctions team; at the first line they never discount a name
match. The flagged list in sample_data.py is synthetic: none of its names
refer to real listed parties.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from enum import Enum

from restrictions import Function, RestrictionRegistry
from rules import in_force

NAME_THRESHOLD = 0.85
ESCALATION_SLA = timedelta(hours=24)   # platform policy for a first follow-up


class Kind(Enum):
    INDIVIDUAL = "individual"
    ENTITY = "entity"
    INSTITUTION = "institution"
    PRODUCT = "product"


@dataclass
class FlaggedEntry:
    entry_id: str
    kind: Kind
    name: str
    aliases: tuple = ()
    date_of_birth: str | None = None
    country: str | None = None
    identifiers: dict = field(default_factory=dict)
    program: str = "Synthetic program"


@dataclass
class Subject:
    """One party, institution, or product to check."""
    subject_id: str
    kind: Kind
    name: str
    role: str
    date_of_birth: str | None = None
    country: str | None = None
    address: str | None = None
    identifiers: dict = field(default_factory=dict)
    ownership_pct: float = 0.0


@dataclass
class Hit:
    subject: Subject
    match_type: str              # identifier | name | jurisdiction | ownership_50
    score: float
    matched: str                 # the list name or jurisdiction that matched
    entry_id: str | None = None
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"subject_id": self.subject.subject_id, "subject": self.subject.name,
                "role": self.subject.role, "match_type": self.match_type,
                "score": round(self.score, 3), "matched": self.matched,
                "entry_id": self.entry_id, "evidence": dict(self.evidence)}


# ---------------------------------------------------------------------------
# Name normalization and similarity
# ---------------------------------------------------------------------------

LEGAL_SUFFIXES = {
    "llc", "inc", "incorporated", "ltd", "limited", "lp", "llp", "corp", "corporation", "co",
    "company", "plc", "sa", "ag", "gmbh", "bv", "nv", "fze", "fzco", "jsc", "pjsc", "ooo",
    "sarl", "srl", "spa", "pte", "pty",
}


def normalize(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = text.replace("&", " and ").replace(".", "")   # "L.L.C." -> "llc"
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = [t for t in text.split() if t not in LEGAL_SUFFIXES]
    return " ".join(tokens)


def similarity(a: str, b: str) -> float:
    """Best of straight and word-order-insensitive comparison, plus full-name containment."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = na.split(), nb.split()
    straight = SequenceMatcher(None, na, nb).ratio()
    sorted_ratio = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    contained = 0.0
    shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(shorter) >= 2 and set(shorter) <= set(longer):
        contained = 0.95   # every word of a two-plus-word name appears in the other
    return max(straight, sorted_ratio, contained)


def _norm_id(value) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


# ---------------------------------------------------------------------------
# Comprehensively sanctioned jurisdictions, effective-dated
# ---------------------------------------------------------------------------

SANCTIONED_JURISDICTIONS = [
    {
        "rule_id": "JUR-001", "version": "1", "effective_date": "2024-01-01", "expiry_date": "2025-07-01",
        "source": "OFAC programs: Cuba (31 CFR Part 515), Iran (31 CFR Part 560), North Korea "
                  "(31 CFR Part 510), Syria (31 CFR Part 542); Crimea (E.O. 13685); so-called DNR "
                  "and LNR regions (E.O. 14065)",
        "countries": ["Cuba", "Iran", "North Korea", "Syria"],
        "regions": ["Crimea", "Donetsk People's Republic", "Luhansk People's Republic"],
    },
    {
        "rule_id": "JUR-001", "version": "2", "effective_date": "2025-07-01",
        "source": "OFAC programs: Cuba (31 CFR Part 515), Iran (31 CFR Part 560), North Korea "
                  "(31 CFR Part 510); Syria program terminated by E.O. 14312; Crimea (E.O. 13685); "
                  "so-called DNR and LNR regions (E.O. 14065)",
        "countries": ["Cuba", "Iran", "North Korea"],
        "regions": ["Crimea", "Donetsk People's Republic", "Luhansk People's Republic"],
    },
]


def jurisdictions_in_force(as_of: date) -> dict:
    current = None
    for version in SANCTIONED_JURISDICTIONS:
        if in_force(version, as_of) and (current is None or int(version["version"]) > int(current["version"])):
            current = version
    return current or {"countries": [], "regions": [], "source": "", "version": "0", "rule_id": "JUR-001"}


# ---------------------------------------------------------------------------
# Screening
# ---------------------------------------------------------------------------

def _compare(subject_value, entry_value) -> str:
    if not subject_value or not entry_value:
        return "not available"
    return "match" if normalize(str(subject_value)) == normalize(str(entry_value)) else "mismatch"


def _evidence(subject: Subject, entry: FlaggedEntry, score: float) -> dict:
    return {"name_score": round(score, 3),
            "date_of_birth": _compare(subject.date_of_birth, entry.date_of_birth),
            "country": _compare(subject.country, entry.country),
            "list_program": entry.program}


def screen_subject(subject: Subject, flagged: list[FlaggedEntry], as_of: date) -> list[Hit]:
    hits: list[Hit] = []
    for entry in flagged:
        shared = set(subject.identifiers) & set(entry.identifiers)
        id_match = [k for k in shared if _norm_id(subject.identifiers[k]) == _norm_id(entry.identifiers[k])]
        if id_match:
            hits.append(Hit(subject, "identifier", 1.0, entry.name, entry.entry_id,
                            {**_evidence(subject, entry, 1.0), "identifier": ", ".join(sorted(id_match))}))
            continue
        if subject.kind is not entry.kind and {subject.kind, entry.kind} != {Kind.ENTITY, Kind.INSTITUTION}:
            continue
        best_name, best = entry.name, 0.0
        for name in (entry.name, *entry.aliases):
            s = similarity(subject.name, name)
            if s > best:
                best_name, best = name, s
        if best >= NAME_THRESHOLD:
            hits.append(Hit(subject, "name", best, best_name, entry.entry_id, _evidence(subject, entry, best)))

    rule = jurisdictions_in_force(as_of)
    place = " ".join(filter(None, [subject.country, subject.address]))
    for country in rule["countries"]:
        if subject.country and normalize(subject.country) == normalize(country):
            hits.append(Hit(subject, "jurisdiction", 1.0, country, None,
                            {"rule": f"{rule['rule_id']} v{rule['version']}", "source": rule["source"]}))
    for region in rule["regions"]:
        if place and normalize(region) in normalize(place):
            hits.append(Hit(subject, "jurisdiction", 1.0, region, None,
                            {"rule": f"{rule['rule_id']} v{rule['version']}", "source": rule["source"]}))
    return hits


def screen(subjects: list[Subject], flagged: list[FlaggedEntry], as_of: date,
           entity_id: str | None = None) -> list[Hit]:
    """
    Check every subject. If entity_id names the entity whose owners are among
    the subjects, flagged owners holding 50% or more in total make the entity
    itself a hit.
    """
    hits: list[Hit] = []
    for s in subjects:
        hits.extend(screen_subject(s, flagged, as_of))
    if entity_id:
        entity = next((s for s in subjects if s.subject_id == entity_id), None)
        flagged_owners = {h.subject.subject_id: h.subject for h in hits
                          if h.subject.ownership_pct > 0 and h.match_type in ("identifier", "name")}
        total = sum(s.ownership_pct for s in flagged_owners.values())
        if entity and total >= 50:
            hits.append(Hit(entity, "ownership_50", 1.0,
                            ", ".join(s.name for s in flagged_owners.values()), None,
                            {"flagged_ownership_pct": total,
                             "source": "OFAC 50 Percent Rule guidance (Aug. 13, 2014)"}))
    return hits


# ---------------------------------------------------------------------------
# Escalation to the sanctions team
# ---------------------------------------------------------------------------

@dataclass
class Escalation:
    escalation_id: str
    target_id: str               # account or firm the hold sits on
    hits: list[Hit]
    restriction_id: str
    opened_at: datetime
    due_at: datetime
    status: str = "pending"      # pending | released | blocked
    determination: str | None = None
    determined_by: str | None = None
    authorization: str | None = None

    def is_overdue(self, now: datetime) -> bool:
        return self.status == "pending" and now >= self.due_at

    def package(self) -> dict:
        """What the first line sends to the sanctions team."""
        return {"escalation_id": self.escalation_id, "target_id": self.target_id,
                "restriction_id": self.restriction_id, "opened_at": self.opened_at.isoformat(),
                "follow_up_due": self.due_at.isoformat(), "status": self.status,
                "hits": [h.to_dict() for h in self.hits]}


@dataclass
class ScreeningResult:
    hits: list[Hit]
    escalation: Escalation | None = None

    @property
    def flagged(self) -> bool:
        return bool(self.hits)


_escalation_counter = 0


def first_line_check(target_id: str, subjects: list[Subject], flagged: list[FlaggedEntry],
                     registry: RestrictionRegistry, by: str, as_of: date,
                     at: datetime | None = None, entity_id: str | None = None) -> ScreeningResult:
    """Screen; on any hit, place a sanctions hold owned by the sanctions team and escalate."""
    global _escalation_counter
    hits = screen(subjects, flagged, as_of, entity_id)
    if not hits:
        return ScreeningResult(hits=[])
    at = at or datetime.now(timezone.utc).replace(microsecond=0)
    names = sorted({h.subject.name for h in hits})
    hold = registry.place(target_id, "SANC", f"Flagged-list match: {', '.join(names)}",
                          by, Function.OPERATIONS, at)
    _escalation_counter += 1
    esc = Escalation(f"ESC-{_escalation_counter:05d}", target_id, hits, hold.restriction_id,
                     at, at + ESCALATION_SLA)
    return ScreeningResult(hits=hits, escalation=esc)


def record_determination(esc: Escalation, registry: RestrictionRegistry, determination: str,
                         by: str, function: Function, authorization: str,
                         at: datetime | None = None) -> Escalation:
    """
    Apply the sanctions team's determination. A false positive releases the
    hold; a true match replaces it with a blocked-property code. The registry
    refuses either change from anyone but the sanctions team.
    """
    if determination not in ("false_positive", "true_match"):
        raise ValueError("determination must be 'false_positive' or 'true_match'")
    if esc.status != "pending":
        raise ValueError(f"Escalation {esc.escalation_id} is already {esc.status}")
    at = at or datetime.now(timezone.utc).replace(microsecond=0)
    if determination == "false_positive":
        registry.remove(esc.restriction_id, by, function, "Sanctions determination: false positive",
                        authorization, at)
        esc.status = "released"
    else:
        registry.remove(esc.restriction_id, by, function,
                        "Sanctions determination: true match; replaced by blocked-property code",
                        authorization, at)
        registry.place(esc.target_id, "OFAC", "Confirmed match; property blocked. Sanctions team "
                       "reports to OFAC within 10 business days", by, function, at)
        esc.status = "blocked"
    esc.determination, esc.determined_by, esc.authorization = determination, by, authorization
    return esc
