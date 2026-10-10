"""Tests for the first-line flagged-list check (Milestone 4)."""
from datetime import date, datetime, timedelta, timezone

import pytest

from flagged_list import (
    FlaggedEntry, Kind, Subject, first_line_check, normalize, record_determination,
    screen, similarity,
)
from restrictions import Function, RestrictionError, RestrictionRegistry
from sample_data import FLAGGED_LIST

TODAY = date(2026, 10, 10)
AT = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)

LIST = [
    FlaggedEntry("FL-1", Kind.INDIVIDUAL, "Ilya Sorvetin", ("Ilia Sorvetine",), "1971-04-18", "Cyprus"),
    FlaggedEntry("FL-2", Kind.ENTITY, "Corvane Maritime Holdings Ltd", ("Corvane Maritime",)),
    FlaggedEntry("FL-3", Kind.INSTITUTION, "Banco Austral del Mar", identifiers={"bic": "BAMRPAPAXXX"}),
    FlaggedEntry("FL-4", Kind.PRODUCT, "Northwind Frontier Fund Class B", identifiers={"isin": "KYG0000NW001"}),
]


def person(name, **kw):
    return Subject(kw.pop("subject_id", f"person:{name}"), Kind.INDIVIDUAL, name, kw.pop("role", "account_holder"), **kw)


def test_normalize_drops_case_accents_punctuation_and_legal_suffixes():
    assert normalize("Córvane Maritime Holdings, L.T.D.") == "corvane maritime holdings"
    assert normalize("Granite Peak Advisors, L.L.C.") == "granite peak advisors"


def test_similarity_ignores_word_order():
    assert similarity("Sorvetin, Ilya", "Ilya Sorvetin") == 1.0


def test_exact_alias_and_spelling_variants_are_possible_matches():
    for name in ("Ilya Sorvetin", "ILIA SORVETINE", "Ilya Sorvyetin", "Ilya A. Sorvetin"):
        hits = screen([person(name)], LIST, TODAY)
        assert [h.entry_id for h in hits] == ["FL-1"], name


def test_unrelated_names_do_not_match():
    assert screen([person("Dana Whitfield"), person("Ilya Petrov")], LIST, TODAY) == []


def test_entity_names_match_without_the_legal_suffix():
    hits = screen([Subject("e1", Kind.ENTITY, "Corvane Maritime Holdings Limited", "owner")], LIST, TODAY)
    assert hits[0].entry_id == "FL-2" and hits[0].score == 1.0


def test_institution_identifier_matches_even_when_the_name_differs():
    bank = Subject("bank", Kind.INSTITUTION, "BAM Panama", "receiving_bank", identifiers={"bic": "bamr papa xxx"})
    hits = screen([bank], LIST, TODAY)
    assert hits[0].match_type == "identifier" and hits[0].evidence["identifier"] == "bic"


def test_products_are_checked_by_identifier():
    fund = Subject("p1", Kind.PRODUCT, "NW Frontier B", "product", identifiers={"isin": "KYG0000NW001"})
    assert screen([fund], LIST, TODAY)[0].entry_id == "FL-4"


def test_people_are_not_compared_with_entity_names():
    assert screen([person("Corvane Maritime")], LIST, TODAY) == []


def test_secondary_identifiers_are_evidence_not_a_discount():
    hit = screen([person("Ilya Sorvetin", date_of_birth="1988-01-02")], LIST, TODAY)[0]
    assert hit.evidence["date_of_birth"] == "mismatch" and hit.evidence["country"] == "not available"


def test_residence_in_a_comprehensively_sanctioned_country_is_a_hit():
    hits = screen([person("Sara Karimi", country="Iran")], LIST, TODAY)
    assert hits[0].match_type == "jurisdiction" and hits[0].matched == "Iran"


def test_syria_stops_being_a_jurisdiction_hit_on_july_1_2025():
    s = person("Layla Haddad", country="Syria")
    assert screen([s], LIST, date(2025, 6, 30))[0].matched == "Syria"
    assert screen([s], LIST, date(2025, 7, 1)) == []


def test_sanctioned_regions_are_found_in_the_address():
    s = person("Oleh Marchenko", country="Ukraine", address="12 Lenina St, Simferopol, Crimea")
    assert screen([s], LIST, TODAY)[0].matched == "Crimea"


def test_flagged_owners_holding_50_percent_make_the_entity_a_hit():
    firm = Subject("firm", Kind.ENTITY, "Meridian Gate Capital LLC", "applicant")
    a = person("Ilya Sorvetin", role="owner", ownership_pct=30)
    b = Subject("o2", Kind.ENTITY, "Corvane Maritime", "owner", ownership_pct=25)
    hits = screen([firm, a, b], LIST, TODAY, entity_id="firm")
    fifty = [h for h in hits if h.match_type == "ownership_50"]
    assert fifty and fifty[0].subject.subject_id == "firm" and fifty[0].evidence["flagged_ownership_pct"] == 55


def test_flagged_ownership_under_50_percent_is_not_an_entity_hit():
    firm = Subject("firm", Kind.ENTITY, "Meridian Gate Capital LLC", "applicant")
    hits = screen([firm, person("Ilya Sorvetin", role="owner", ownership_pct=30)], LIST, TODAY, entity_id="firm")
    assert [h.match_type for h in hits] == ["name"]


def test_no_hit_means_no_hold():
    reg = RestrictionRegistry()
    result = first_line_check("ACCT-1", [person("Dana Whitfield")], LIST, reg, "ops.analyst", TODAY, AT)
    assert not result.flagged and result.escalation is None and reg.active("ACCT-1") == []


def test_a_hit_places_a_sanctions_hold_and_opens_an_escalation():
    reg = RestrictionRegistry()
    result = first_line_check("ACCT-1", [person("Ilia Sorvetin")], LIST, reg, "ops.analyst", TODAY, AT)
    esc = result.escalation
    assert reg.active_codes("ACCT-1") == ["SANC"] and reg.active("ACCT-1")[0].owner is Function.SANCTIONS
    assert esc.status == "pending" and esc.due_at == AT + timedelta(hours=24)
    assert esc.package()["hits"][0]["entry_id"] == "FL-1"


def test_escalation_is_overdue_after_the_follow_up_window():
    reg = RestrictionRegistry()
    esc = first_line_check("ACCT-1", [person("Ilya Sorvetin")], LIST, reg, "ops", TODAY, AT).escalation
    assert not esc.is_overdue(AT + timedelta(hours=23))
    assert esc.is_overdue(AT + timedelta(hours=24))


def test_first_line_cannot_record_the_determination():
    reg = RestrictionRegistry()
    esc = first_line_check("ACCT-1", [person("Ilya Sorvetin")], LIST, reg, "ops", TODAY, AT).escalation
    with pytest.raises(RestrictionError):
        record_determination(esc, reg, "false_positive", "ops.analyst", Function.OPERATIONS, "N/A", AT)
    assert esc.status == "pending" and reg.active_codes("ACCT-1") == ["SANC"]


def test_false_positive_releases_the_hold():
    reg = RestrictionRegistry()
    esc = first_line_check("ACCT-1", [person("Ilya Sorvetkin")], LIST, reg, "ops", TODAY, AT).escalation
    record_determination(esc, reg, "false_positive", "sanctions.lead", Function.SANCTIONS, "DET-0412", AT)
    assert esc.status == "released" and reg.active("ACCT-1") == []


def test_true_match_replaces_the_hold_with_blocked_property():
    reg = RestrictionRegistry()
    esc = first_line_check("ACCT-1", [person("Ilya Sorvetin")], LIST, reg, "ops", TODAY, AT).escalation
    record_determination(esc, reg, "true_match", "sanctions.lead", Function.SANCTIONS, "DET-0413", AT)
    assert esc.status == "blocked" and reg.active_codes("ACCT-1") == ["OFAC"]
    with pytest.raises(ValueError, match="already blocked"):
        record_determination(esc, reg, "false_positive", "sanctions.lead", Function.SANCTIONS, "DET-0414", AT)


def test_sample_flagged_list_is_synthetic_and_well_formed():
    assert len(FLAGGED_LIST) >= 5
    assert all(e.program.startswith("Synthetic") for e in FLAGGED_LIST)
    assert len({e.entry_id for e in FLAGGED_LIST}) == len(FLAGGED_LIST)
