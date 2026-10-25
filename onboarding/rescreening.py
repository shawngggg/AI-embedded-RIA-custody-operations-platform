"""
Ongoing screening: everyone the custodian already serves, checked again against the flagged list whenever the list
changes and every night (the sanctions escalation subprocess, map 03).

Onboarding screens a party once. Sanctions lists change, so a client who was clear at opening can match tomorrow:
OFAC expects screening to continue for the life of the relationship (its 2022 finding against MidFirst Bank turned on
customers who weren't rescreened after list updates). The population is every open account's holders, owners, and
control persons, its beneficiaries, its standing payees (SLOAs) and their banks, and every active RIA firm and its
owners and control persons.

    find_alerts(parties, flagged, as_of, cleared, open_keys) -> {target: [Hit]}
    process_rescreen(alert, flagged, as_of, decisions, registry) -> OnboardingResult

A match the sanctions team already cleared as a false positive for that client isn't raised again; a new list entry,
or a changed name, is. A new match places the SANC hold on the account or firm at once and goes to the sanctions team,
which clears it (the hold lifts) or confirms it (the OFAC blocked-property code replaces the hold).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from flagged_list import FlaggedEntry, Hit, Kind, Subject, first_line_check, normalize, record_determination, screen
from pipeline import OPS, RULES, SANCTIONS, Decisions, OnboardingResult
from restrictions import Function, RestrictionRegistry

TRIGGERS = {"list_change": "The flagged list changed", "nightly": "Nightly rescreening"}


@dataclass
class Party:
    """One subject to rescreen, and the account or firm a match would hold."""
    target_id: str
    target_kind: str                     # account | firm
    target_name: str
    subject: Subject
    relationship: str = ""               # account holder, beneficiary, standing payee, ...


@dataclass
class Population:
    parties: list[Party] = field(default_factory=list)

    def targets(self) -> dict[str, list[Party]]:
        out: dict[str, list[Party]] = {}
        for p in self.parties:
            out.setdefault(p.target_id, []).append(p)
        return out


def hit_key(target_id: str, hit: Hit | dict) -> str:
    """What a determination clears: this target, this party, this list entry (or jurisdiction)."""
    if isinstance(hit, dict):
        name, entry = hit["subject"], hit.get("entry_id") or hit.get("matched")
    else:
        name, entry = hit.subject.name, hit.entry_id or hit.matched
    return f"{target_id}|{normalize(name)}|{entry}"


def find_alerts(population: Population, flagged: list[FlaggedEntry], as_of: date, cleared: set[str],
                open_keys: set[str] | None = None) -> dict[str, list[Hit]]:
    """New matches by target: hits not already cleared for that target and not already waiting on the sanctions
    team."""
    skip = cleared | (open_keys or set())
    out: dict[str, list[Hit]] = {}
    for target, parties in population.targets().items():
        hits = [h for h in screen([p.subject for p in parties], flagged, as_of) if hit_key(target, h) not in skip]
        if hits:
            out[target] = hits
    return out


def alert_package(target: Party, hits: list[Hit], trigger: str, list_size: int, parties: list[Party]) -> dict:
    """What a screening case stores: the target, the parties screened, and the hits that raised it."""
    return {"target_id": target.target_id, "target_kind": target.target_kind, "target_name": target.target_name,
            "trigger": trigger, "list_size": list_size, "hits": [h.to_dict() for h in hits],
            "subjects": [{"subject_id": p.subject.subject_id, "kind": p.subject.kind.value, "name": p.subject.name,
                          "role": p.subject.role, "date_of_birth": p.subject.date_of_birth,
                          "country": p.subject.country, "address": p.subject.address,
                          "identifiers": dict(p.subject.identifiers), "relationship": p.relationship}
                         for p in parties]}


def _subjects(alert: dict) -> list[Subject]:
    return [Subject(s["subject_id"], Kind(s["kind"]), s["name"], s["role"], s.get("date_of_birth"), s.get("country"),
                    s.get("address"), dict(s.get("identifiers") or {})) for s in alert["subjects"]]


def process_rescreen(alert: dict, flagged: list[FlaggedEntry], as_of: date, decisions: Decisions | None = None,
                     registry: RestrictionRegistry | None = None, by: str = "screening.batch") -> OnboardingResult:
    decisions = decisions or Decisions()
    registry = registry or RestrictionRegistry()
    target = alert["target_id"]
    res = OnboardingResult("screening", f"Ongoing screening: {alert['target_name']}", target, as_of, registry=registry)
    raised = alert["hits"]
    res.add(OPS, TRIGGERS.get(alert["trigger"], "Rescreening"), "Rescreened",
            [f"{len(alert['subjects'])} parties on this {alert['target_kind']} checked against {alert['list_size']} "
             "list entries", "OFAC expects screening for the life of the relationship, not only at opening"])
    named = {h["subject"] for h in raised}
    subjects = [s for s in _subjects(alert) if s.name in named]
    at = datetime.combine(as_of, datetime.min.time(), tzinfo=timezone.utc)
    screening = first_line_check(target, subjects, flagged, registry, by, as_of, at)
    res.screening = [h.to_dict() for h in screening.hits]
    if not screening.flagged:
        res.add(RULES, "First-line check against the current list", "No longer a match",
                ["The entry was removed from the list or changed; nothing to decide"])
        res.status = "CLEARED"
        return res
    esc = screening.escalation
    res.add(RULES, "First-line check against the current list", "Possible match; sanctions hold placed",
            [f"{h.subject.name} ({h.subject.role}) ~ {h.matched} ({h.match_type}, {h.score:.2f})"
             for h in screening.hits], ["SCR-001"])
    res.add(OPS, "Sanctions escalation (shared subprocess)", "Escalated to the sanctions team",
            [f"{esc.escalation_id}; follow up by {esc.due_at.isoformat()}", "Only the sanctions team can release the "
             "hold; outgoing money and trading stop until it does"])
    res.escalation = esc.package()
    if decisions.sanctions is None:
        res.status = "PENDING_SANCTIONS"
        return res
    record_determination(esc, registry, decisions.sanctions, "sanctions.team", Function.SANCTIONS,
                         decisions.sanctions_reference, at)
    res.escalation = esc.package()
    if decisions.sanctions == "true_match":
        res.add(SANCTIONS, "Determination", "True match: blocked; the sanctions team reports to OFAC",
                [f"Authorization {decisions.sanctions_reference}", "Hold replaced by blocked-property code OFAC"])
        res.status = "BLOCKED"
        return res
    res.add(SANCTIONS, "Determination", "False positive: hold released",
            [f"Authorization {decisions.sanctions_reference}",
             "This match won't be raised again for this client unless the entry or the name changes"])
    res.status = "CLEARED"
    return res
