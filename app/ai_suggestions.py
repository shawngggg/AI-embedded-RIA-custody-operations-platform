"""
AI suggestions and what people do with them (PRD guardrail 6, and the MVP gate's AI suggestion acceptance metric).

Every value the AI reads from a firm's package and the controls gate lets through becomes a suggestion. The
onboarding and CIP teams check each one against the package (Shawn, Oct. 20, 2026): onboarding the firm and Form ADV
facts, CIP the identity facts (CIP_FIELDS). They accept it unchanged, edit it, or reject it, with a reason for an edit
or a rejection. Until every suggestion is reviewed the case waits in the AI review queue, and the rules don't run on
AI values; a licensed principal accepts the relationship at the end. The metric is the share of reviewed live suggestions accepted without edits;
the illustrative demo output is tracked apart and never counted.
"""
from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from extraction import FIRM_FIELDS, PERSON_ROLES, SYSTEM, _parse, build_prompt

from . import audit
from .orm import AiSuggestion, Case, Firm, User, iso, utcnow

# Rejecting a value leaves the field empty. These can be empty: the rules then treat the item as missing.
CLEARS_TO = {"sec_file_number": None, "crd_number": None, "adv_last_annual_amendment": None,
             "principal_address": None, "ein": None, "aml_certification_date": None,
             "formation_documents": False, "ownership_chart": False, "beneficial_ownership_certification": False,
             "custodial_agreement_signed": False, "reliance_contract": False}
# These always hold a value, and an empty one would read as a fact (registration "SEC", RAUM of zero): a wrong value
# is edited to what the package says, never rejected
CHOICES = {"registration": ["SEC", "state", "none"], "entity_type": ["LLC", "corporation", "partnership"]}
MIN_REASON = 10
# The CIP team reviews what identifies the firm and the people behind it (name, address, TIN, owners and control
# persons, and the formation and ownership documents); onboarding reviews the rest
CIP_FIELDS = {"legal_name", "principal_address", "ein", "people", "formation_documents", "ownership_chart",
              "beneficial_ownership_certification"}
REVIEW_TEAMS = {"onboarding": "Onboarding", "cip": "CIP"}
LABELS = {"legal_name": "Legal name", "entity_type": "Entity type", "registration": "Registration",
          "sec_file_number": "SEC file number", "crd_number": "CRD number", "raum": "RAUM (Form ADV Item 5.F)",
          "fiscal_year_end_month": "Fiscal year ends (month)", "adv_last_annual_amendment": "Last ADV annual amendment",
          "disciplinary_disclosures": "Disciplinary events (Form ADV Item 11)",
          "principal_address": "Principal place of business", "ein": "EIN",
          "formation_documents": "Formation documents in the package", "ownership_chart": "Ownership chart in the package",
          "beneficial_ownership_certification": "Beneficial ownership certification in the package",
          "custodial_agreement_signed": "Custodial agreement signed", "reliance_contract": "CIP reliance agreement signed",
          "aml_certification_date": "AML certification date", "people": "Owner or control person"}


def prompt_version() -> str:
    """A short fingerprint of the extraction instructions: a change to the prompt shows as a new version."""
    return hashlib.sha256((SYSTEM + build_prompt("")).encode()).hexdigest()[:10]


def team_for(field: str) -> str:
    return "cip" if field in CIP_FIELDS else "onboarding"


def may_review(user: User, field: str) -> bool:
    """The supervisor oversees both teams; an analyst reviews their own team's values."""
    return user.role == "ops_supervisor" or (user.role == "ops_analyst" and user.team == team_for(field))


def pending_teams(db: Session, case: Case) -> set[str]:
    return {team_for(f) for f in db.scalars(select(AiSuggestion.field).where(
        AiSuggestion.case_id == case.id, AiSuggestion.status == "pending")).all()}


def label(field: str) -> str:
    return LABELS.get(field) or FIRM_FIELDS[field][1]


def kind(field: str) -> str:
    return "person" if field == "people" else FIRM_FIELDS[field][0]


def show(field: str, value: dict | None) -> str | None:
    if value is None:
        return None
    if field == "people":
        return f"{value['name']} ({value['role'].replace('_', ' ')}, {float(value['ownership_pct']):g}%)"
    raw = str(value.get("value"))
    try:                                   # easier to check against the package: yes or no, and dollars
        if kind(field) == "boolean":
            return "Yes" if _parse("boolean", raw) else "No"
        if field == "raum":
            return f"${_parse('number', raw):,.0f}"
    except ValueError:
        pass
    return raw


def create_for(db: Session, case: Case, extraction: dict, mode: str, model: str | None,
               version: str | None) -> list[AiSuggestion]:
    """One suggestion per value the controls gate accepted. The gate adds people to its accepted list in the same
    order it adds them to the application, so the two line up."""
    people = iter(case.application.get("people", []))
    rows = []
    for item in extraction.get("accepted", []):
        if item["field"] == "people":
            p = next(people, None)
            if p is None:
                continue
            suggested = {"name": p["name"], "role": p["role"], "ownership_pct": p["ownership_pct"]}
        else:
            suggested = {"value": str(item["value"])}
        rows.append(AiSuggestion(case_id=case.id, case_label=f"#{case.id} {case.title.split(': ', 1)[-1]}",
                                 field=item["field"], suggested=suggested, quote=item.get("quote", ""), mode=mode,
                                 model=model, prompt_version=version))
    db.add_all(rows)
    db.flush()
    return rows


def actions(s: AiSuggestion, user: User, case: Case) -> list[str]:
    if s.status != "pending" or case.closed_at or not may_review(user, s.field):
        return []
    return ["accept", "edit", "reject"] if s.field == "people" or s.field in CLEARS_TO else ["accept", "edit"]


def to_dict(s: AiSuggestion, user: User | None = None, case: Case | None = None) -> dict:
    return {"id": s.id, "field": s.field, "label": label(s.field), "kind": kind(s.field), "team": team_for(s.field),
            "team_label": REVIEW_TEAMS[team_for(s.field)],
            "choices": CHOICES.get(s.field), "suggested": s.suggested, "suggested_text": show(s.field, s.suggested),
            "quote": s.quote, "mode": s.mode, "status": s.status, "final": s.final, "final_text": show(s.field, s.final),
            "reason": s.reason, "reviewed_by": s.reviewed_by_name, "reviewed_at": iso(s.reviewed_at),
            "actions": actions(s, user, case) if user is not None and case is not None else []}


def for_case(db: Session, case: Case, user: User) -> list[dict]:
    rows = db.scalars(select(AiSuggestion).where(AiSuggestion.case_id == case.id).order_by(AiSuggestion.id)).all()
    return [to_dict(s, user, case) for s in rows]


def _edited_value(s: AiSuggestion, value) -> tuple[dict, object]:
    """The reviewer's value in the suggestion's shape, and the value for the application. 422 if it isn't valid."""
    if s.field == "people":
        if not isinstance(value, dict):
            raise HTTPException(422, "Give the person's name, role, and ownership percentage")
        name = str(value.get("name") or "").strip()
        role = value.get("role")
        try:
            pct = float(value.get("ownership_pct") or 0)
        except (TypeError, ValueError):
            raise HTTPException(422, "The ownership percentage must be a number")
        if not name or len(name) > 120:
            raise HTTPException(422, "Give the person's name")
        if role not in PERSON_ROLES:
            raise HTTPException(422, f"The role must be one of: {', '.join(PERSON_ROLES)}")
        if not 0 <= pct <= 100:
            raise HTTPException(422, "The ownership percentage must be between 0 and 100")
        person = {"name": name, "role": role, "ownership_pct": pct}
        return person, person
    raw = str(value if value is not None else "").strip()
    try:
        parsed = _parse(kind(s.field), raw)
    except ValueError:
        hint = {"date": " (YYYY-MM-DD)", "number": "", "boolean": " (yes or no)"}.get(kind(s.field), "")
        raise HTTPException(422, f"Not a valid {kind(s.field)}{hint}")
    if s.field in CHOICES and parsed not in CHOICES[s.field]:
        raise HTTPException(422, f"Choose one of: {', '.join(CHOICES[s.field])}")
    if s.field == "fiscal_year_end_month" and not (isinstance(parsed, int) and 1 <= parsed <= 12):
        raise HTTPException(422, "The month must be 1 to 12")
    if s.field == "raum" and parsed < 0:
        raise HTTPException(422, "RAUM can't be negative")
    stored = parsed.isoformat() if isinstance(parsed, date) else parsed
    return {"value": raw}, stored


def _same(s: AiSuggestion, final: dict) -> bool:
    if s.field == "people":
        a, b = s.suggested, final
        return (a["name"].strip().lower(), a["role"], float(a["ownership_pct"])) == \
               (b["name"].strip().lower(), b["role"], float(b["ownership_pct"]))
    try:
        return _parse(kind(s.field), s.suggested["value"]) == _parse(kind(s.field), final["value"])
    except ValueError:
        return False


def _set_crd(db: Session, case: Case, crd: str | None) -> None:
    """The firm record and the case's restriction target follow the CRD number, as they do at intake."""
    firm = db.get(Firm, case.firm_id)
    if crd and db.scalar(select(Firm).where(Firm.crd == crd, Firm.id != firm.id)):
        raise HTTPException(409, f"A firm with CRD {crd} is already on the platform")
    firm.crd = crd or f"NEW-{uuid.uuid4().hex[:8].upper()}"
    case.target_id = f"FIRM-{firm.crd}"


def review(db: Session, case: Case, s: AiSuggestion, user: User, action: str, value=None,
           reason: str | None = None) -> None:
    """Record what the reviewer did, apply an edit or a rejection to the application, and re-run the case."""
    from .runner import run_case
    if not may_review(user, s.field):
        team = REVIEW_TEAMS[team_for(s.field)]
        audit.refuse(db, user, "ai_suggestion_review", f"The {team} team reviews this value", case_id=case.id,
                     suggestion=s.id, field=s.field)
    if s.status != "pending":
        raise HTTPException(409, f"Already {s.status} by {s.reviewed_by_name}")
    if case.closed_at:
        raise HTTPException(409, "This case is closed")
    if action not in ("accept", "edit", "reject"):
        raise HTTPException(422, "Accept, edit, or reject")
    if action == "reject" and not (s.field == "people" or s.field in CLEARS_TO):
        raise HTTPException(422, f"{label(s.field)} always needs a value: edit it to what the package says")
    reason = (reason or "").strip()
    if action != "accept" and len(reason) < MIN_REASON:
        raise HTTPException(422, "Say what the package shows instead (a sentence is enough)")

    app = dict(case.application)
    final = None
    if action == "edit":
        final, stored = _edited_value(s, value)
        if _same(s, final):
            raise HTTPException(422, "That's the AI's value: accept it instead")
        if s.field == "people":
            people = [dict(p) for p in app.get("people", [])]
            i = next((i for i, p in enumerate(people) if p["name"] == s.suggested["name"]), None)
            if i is None:
                raise HTTPException(409, f"{s.suggested['name']} is no longer on the application")
            people[i].update(stored)
            app["people"] = people
        else:
            app[s.field] = stored
    elif action == "reject":
        if s.field == "people":
            app["people"] = [p for p in app.get("people", []) if p["name"] != s.suggested["name"]]
        else:
            app[s.field] = CLEARS_TO[s.field]

    if s.field == "crd_number" and action != "accept":
        _set_crd(db, case, app.get("crd_number"))
    if s.field == "legal_name" and action == "edit":
        firm = db.get(Firm, case.firm_id)
        firm.legal_name = app["legal_name"]
        case.title = f"RIA firm onboarding: {firm.legal_name}"

    s.status = {"accept": "accepted", "edit": "edited", "reject": "rejected"}[action]
    s.final, s.reason = final, reason or None
    s.reviewed_by, s.reviewed_by_name, s.reviewed_at = user.id, user.display_name, utcnow()
    case.application = app
    if action != "accept":
        case.manual_steps += 1          # a correction to the AI's work is a manual step
    audit.record(db, user, "ai_suggestion_reviewed", "case", case.id, case_id=case.id, suggestion=s.id,
                 field=s.field, outcome=s.status, mode=s.mode, suggested=show(s.field, s.suggested),
                 final=show(s.field, final), reason=reason or None)
    run_case(db, case, user)


def _tally(rows: list[AiSuggestion]) -> dict:
    reviewed = [r for r in rows if r.status != "pending"]
    counts = {k: sum(r.status == k for r in rows) for k in ("accepted", "edited", "rejected")}
    return {"suggestions": len(rows), "reviewed": len(reviewed), "pending": len(rows) - len(reviewed), **counts,
            "share_accepted": round(counts["accepted"] / len(reviewed), 3) if reviewed else None,
            "cases": len({r.case_id or f"reset:{r.case_label}" for r in rows})}


def summary(db: Session, changes: bool = False) -> dict:
    """The acceptance metric. `changes` adds the recent edits and rejections with their values and reasons, for the
    team leads; the platform administrator, who has no case files, sees counts only."""
    rows = db.scalars(select(AiSuggestion).order_by(AiSuggestion.id)).all()
    live = [r for r in rows if r.mode == "live"]
    by_field, by_prompt = defaultdict(list), defaultdict(list)
    for r in live:
        by_field[r.field].append(r)
        by_prompt[(r.prompt_version, r.model)].append(r)
    out = {"live": _tally(live), "illustrative": _tally([r for r in rows if r.mode != "live"]),
           "by_field": [{"field": f, "label": label(f), **_tally(rs)} for f, rs in sorted(by_field.items())],
           "by_prompt": [{"prompt_version": v, "model": m, **_tally(rs)} for (v, m), rs in by_prompt.items()]}
    if changes:
        recent = sorted((r for r in rows if r.status in ("edited", "rejected")),
                        key=lambda r: r.reviewed_at or r.created_at, reverse=True)[:20]
        out["recent_changes"] = [{"id": r.id, "case_id": r.case_id, "case": r.case_label, "mode": r.mode,
                                  "field": r.field, "label": label(r.field), "status": r.status,
                                  "suggested": show(r.field, r.suggested), "final": show(r.field, r.final),
                                  "reason": r.reason, "by": r.reviewed_by_name, "at": iso(r.reviewed_at)}
                                 for r in recent]
    return out
