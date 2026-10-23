"""
What every release 2 workflow returns: the onboarding engine's result (steps by lane, status, restriction codes), plus
the ledger entries the case should have posted, a summary for the screens, and what the app should do next.

A workflow is a pure function of the case's request, the decisions people approved, and the events recorded on it
(a rail's confirmation, a callback, a document arriving). The app replays it on every change, posts any entry it lists
that isn't posted yet (each has a stable reference, so a replay never posts twice), and records the events it asks for.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from pipeline import AI, AML, LEDGER, OPS, PRINCIPAL, RULES, OnboardingResult  # noqa: F401  (lanes re-exported)

COMPLIANCE = "Compliance and legal"
SETTLEMENT = "Ledger and account services"


@dataclass
class FlowResult(OnboardingResult):
    entries: list[dict] = field(default_factory=list)      # {ref, kind, description, legs: [Leg], on: date}
    summary: dict = field(default_factory=dict)            # label -> value, for the case page
    record: dict = field(default_factory=dict)             # events the app should record now: {type: {...}}
    expected: date | None = None                           # when the next external response is due
    spawn: list[dict] = field(default_factory=list)        # child cases to open once: {key, kind, title, application}
    effects: list[dict] = field(default_factory=list)      # changes to records outside the ledger, applied once

    def post(self, ref: str, kind: str, description: str, legs: list, on: date) -> None:
        if legs:
            self.entries.append({"ref": ref, "kind": kind, "description": description, "legs": legs, "on": on})

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["summary"] = dict(self.summary)
        d["expected"] = self.expected.isoformat() if self.expected else None
        d["entries"] = [{"ref": e["ref"], "kind": e["kind"], "description": e["description"],
                         "on": e["on"].isoformat()} for e in self.entries]
        return d


def events_of(events: list[dict], kind: str, **match) -> list[dict]:
    return [e for e in events or [] if e.get("type") == kind and all(e.get(k) == v for k, v in match.items())]


def last_event(events: list[dict], kind: str, **match) -> dict | None:
    found = events_of(events, kind, **match)
    return found[-1] if found else None


def on_date(e: dict | None, default: date) -> date:
    return date.fromisoformat(e["on"]) if e and e.get("on") else default
