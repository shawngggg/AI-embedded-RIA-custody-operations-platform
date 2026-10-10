"""Tests for the generic rules engine (Milestone 2)."""
from dataclasses import dataclass
from datetime import date

from rules import (
    Decision, Disposition, active_rules, evaluate_all, evaluate_first,
    in_force, validate_rules, worst,
)

TODAY = date(2026, 10, 10)


@dataclass
class Subject:
    country: str = "United States"
    score: int | None = 10
    tags: tuple = ()
    note: str = ""


def rule(rule_id, version="1", effective="2026-01-01", expiry=None, **kw):
    r = {"rule_id": rule_id, "version": version, "effective_date": effective,
         "source": "test source", "disposition": kw.pop("disposition", Disposition.REFER),
         "reason": kw.pop("reason", f"{rule_id} matched")}
    if expiry:
        r["expiry_date"] = expiry
    r.update(kw)
    return r


def test_rule_not_in_force_before_effective_date():
    assert not in_force(rule("A", effective="2026-11-01", field="country", operator="is_present"), TODAY)


def test_expiry_date_is_exclusive():
    r = rule("A", effective="2025-01-01", expiry="2026-10-10", field="country", operator="is_present")
    assert in_force(r, date(2026, 10, 9))
    assert not in_force(r, TODAY)


def test_highest_version_in_force_wins():
    lib = [rule("A", "1", "2025-01-01", field="country", operator="is_present"),
           rule("A", "2", "2026-01-01", field="country", operator="is_present")]
    assert [r["version"] for r in active_rules(lib, TODAY)] == ["2"]
    assert [r["version"] for r in active_rules(lib, date(2025, 6, 1))] == ["1"]


def test_evaluate_all_collects_every_match_and_takes_the_worst():
    lib = [rule("A", field="country", operator="equals", value="United States"),
           rule("B", field="score", operator="less_than", value=50, disposition=Disposition.ESCALATE),
           rule("C", field="score", operator="greater_than", value=50)]
    ev = evaluate_all(Subject(), lib, TODAY)
    assert ev.rule_ids == ["A", "B"]
    assert ev.disposition is Disposition.ESCALATE


def test_evaluate_first_stops_at_first_match():
    lib = [rule("A", field="country", operator="equals", value="United States"),
           rule("B", field="score", operator="less_than", value=50)]
    assert evaluate_first(Subject(), lib, TODAY).rule_ids == ["A"]


def test_evaluate_first_uses_default_when_nothing_matches():
    default = Decision(Disposition.CLEAR, "standard", "DEF-000", "1", "policy")
    ev = evaluate_first(Subject(), [rule("A", field="score", operator="greater_than", value=99)],
                        TODAY, default=default)
    assert ev.rule_ids == ["DEF-000"] and ev.disposition is Disposition.CLEAR


def test_no_findings_means_clear():
    assert evaluate_all(Subject(), [], TODAY).disposition is Disposition.CLEAR


def test_all_conditions_must_match():
    r = rule("A", all=[{"field": "country", "operator": "equals", "value": "United States"},
                       {"field": "score", "operator": "greater_than", "value": 50}])
    assert evaluate_all(Subject(), [r], TODAY).decisions == []
    assert evaluate_all(Subject(score=60), [r], TODAY).rule_ids == ["A"]


def test_unknown_field_warns_and_skips():
    ev = evaluate_all(Subject(), [rule("A", field="nope", operator="is_present")], TODAY)
    assert ev.decisions == [] and "unknown field 'nope'" in ev.warnings[0]


def test_unknown_operator_warns_and_skips():
    ev = evaluate_all(Subject(), [rule("A", field="country", operator="sounds_like", value="x")], TODAY)
    assert ev.decisions == [] and "unknown operator" in ev.warnings[0]


def test_comparing_none_warns_instead_of_crashing():
    ev = evaluate_all(Subject(score=None), [rule("A", field="score", operator="less_than", value=5)], TODAY)
    assert ev.decisions == [] and "could not compare" in ev.warnings[0]


def test_dotted_paths_and_dicts():
    subject = {"ria": {"active": False}}
    ev = evaluate_all(subject, [rule("A", field="ria.active", operator="equals", value=False)], TODAY)
    assert ev.rule_ids == ["A"]


def test_computed_fields_are_resolved_first():
    computed = {"double": lambda s, as_of: s.score * 2}
    ev = evaluate_all(Subject(score=30), [rule("A", field="double", operator="equals", value=60)],
                      TODAY, computed)
    assert ev.rule_ids == ["A"]


def test_blank_and_present_operators():
    lib = [rule("BLANK", field="note", operator="is_blank"), rule("TAGS", field="tags", operator="is_present")]
    assert evaluate_all(Subject(note="  "), lib, TODAY).rule_ids == ["BLANK"]
    assert evaluate_all(Subject(note="x", tags=("a",)), lib, TODAY).rule_ids == ["TAGS"]


def test_contains_any():
    r = rule("A", field="tags", operator="contains_any", value=["pep", "media"])
    assert evaluate_all(Subject(tags=("media",)), [r], TODAY).rule_ids == ["A"]
    assert evaluate_all(Subject(tags=("other",)), [r], TODAY).rule_ids == []


def test_required_documents_are_deduplicated_in_order():
    lib = [rule("A", field="country", operator="is_present", required_documents=["X", "Y"]),
           rule("B", field="country", operator="is_present", required_documents=["Y", "Z"])]
    assert evaluate_all(Subject(), lib, TODAY).required_documents == ["X", "Y", "Z"]


def test_worst_orders_by_severity():
    assert worst([Disposition.REFER, Disposition.BLOCK, Disposition.ESCALATE]) is Disposition.BLOCK
    assert worst([]) is Disposition.CLEAR


def test_validate_rules_catches_library_problems():
    bad = [{"rule_id": "A", "version": "1", "effective_date": "2026-13-01", "source": "s",
            "disposition": "refer", "reason": "r", "field": "x", "operator": "near"},
           rule("B", field="x", operator="is_present"), rule("B", field="x", operator="is_present")]
    problems = validate_rules(bad)
    assert any("not an ISO date" in p for p in problems)
    assert any("unknown operator 'near'" in p for p in problems)
    assert any("must be a Disposition" in p for p in problems)
    assert any("duplicate" in p for p in problems)


def test_evaluation_serializes_for_the_audit_log():
    ev = evaluate_all(Subject(), [rule("A", field="country", operator="is_present")], TODAY)
    d = ev.to_dict()
    assert d["disposition"] == "refer" and d["decisions"][0]["rule_id"] == "A"
    assert d["evaluation_date"] == "2026-10-10"
