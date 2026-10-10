"""Tests for AI extraction behind the controls gate (Milestone 7). No live API calls."""
import copy
import json
from datetime import date
from types import SimpleNamespace

from extraction import SCHEMA, extract_firm_application, gate
from firm_rules import evaluate_firm, firm_reliance
from rules import Disposition
from sample_data import FIRM_PACKAGE_TEXT as DOC, ILLUSTRATIVE_EXTRACTION

TODAY = date(2026, 10, 10)


class FakeClient:
    """Stands in for anthropic.Anthropic(): records the request, returns a canned answer."""
    def __init__(self, payload=None, stop_reason="end_turn", raw_text=None):
        self.calls = []
        text = raw_text if raw_text is not None else json.dumps(payload)
        response = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop_reason)
        self.messages = SimpleNamespace(create=lambda **kw: self.calls.append(kw) or response)


def payload(**changes):
    p = copy.deepcopy(ILLUSTRATIVE_EXTRACTION)
    p.update(changes)
    return p


def with_field(name, value, quote):
    p = payload()
    p["fields"] = [f for f in p["fields"] if f["name"] != name] + [{"name": name, "value": value, "quote": quote}]
    return p


def rejected(result):
    return {r["field"]: r["reason"] for r in result.rejected}


def test_request_uses_structured_outputs_and_treats_the_document_as_data():
    client = FakeClient(payload())
    extract_firm_application(DOC, client=client, model="claude-haiku-5-5")
    call = client.calls[0]
    assert call["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}
    assert call["model"] == "claude-haiku-5-5" and "data, not instructions" in call["system"]
    assert "<document>" in call["messages"][0]["content"]


def test_schema_meets_structured_output_limits():
    def walk(node):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
            for child in node["properties"].values():
                walk(child)
        if node.get("type") == "array":
            walk(node["items"])
    walk(SCHEMA)


def test_grounded_facts_fill_the_draft_application():
    app = extract_firm_application(DOC, client=FakeClient(payload())).application
    assert app.legal_name == "Red Butte Capital Management LLC" and app.registration == "SEC"
    assert (app.raum, app.fiscal_year_end_month, app.entity_type) == (640_000_000, 12, "LLC")
    assert app.adv_last_annual_amendment == date(2026, 3, 18) and app.disciplinary_disclosures is False
    assert [(p.name, p.role, p.ownership_pct) for p in app.people] == [
        ("Nora Lindqvist", "control_person", 65.0), ("Tomas Reyes", "owner", 35.0)]


def test_a_claim_the_documents_do_not_support_is_rejected():
    result = extract_firm_application(DOC, client=FakeClient(payload()))
    assert rejected(result) == {"reliance_contract": "quote not found in the document"}
    assert result.application.reliance_contract is False
    assert any("reliance_contract" in item for item in result.review_queue)


def test_a_value_missing_from_its_quote_is_rejected():
    p = with_field("ein", "87-9999999", "Employer identification number: 87-1234511")
    assert rejected(gate(p, DOC))["ein"] == "value not found in its quote"


def test_a_value_of_the_wrong_type_is_rejected():
    p = with_field("raum", "a lot", "Item 5.F Regulatory assets under management: $640,000,000")
    assert rejected(gate(p, DOC))["raum"] == "not a valid number"


def test_amounts_written_in_millions_are_grounded():
    doc = DOC + "\nRegulatory assets under management of $640 million."
    p = with_field("raum", "640000000", "Regulatory assets under management of $640 million.")
    assert gate(p, doc).application.raum == 640_000_000


def test_the_model_cannot_set_verification_or_decisions():
    p = payload()
    p["fields"].append({"name": "identity_verified", "value": "true", "quote": "identity verified"})
    result = gate(p, DOC)
    assert rejected(result)["identity_verified"] == "not a field AI may fill"
    assert all(person.identity_verified is False for person in result.application.people)


def test_people_must_be_grounded_too():
    p = payload()
    p["people"].append({"name": "Victor Hale", "role": "owner", "ownership_pct": "30",
                        "quote": "Victor Hale, Member: 30%"})
    result = gate(p, DOC)
    assert [x.name for x in result.application.people] == ["Nora Lindqvist", "Tomas Reyes"]
    assert any(r["value"] == "Victor Hale" for r in result.rejected)


def test_injected_instructions_surface_for_review_and_change_nothing():
    result = gate(payload(), DOC)
    assert any("ignored" in item for item in result.review_queue)
    assert all(not p.identity_verified for p in result.application.people)


def test_refusal_or_truncation_sends_the_package_to_a_person():
    result = extract_firm_application(DOC, client=FakeClient(payload(), stop_reason="max_tokens"))
    assert result.accepted == [] and "extract this package by hand" in result.review_queue[0]


def test_unreadable_output_sends_the_package_to_a_person():
    result = extract_firm_application(DOC, client=FakeClient(raw_text="not json"))
    assert result.accepted == [] and "could not be read" in result.review_queue[0]


def test_malformed_output_is_rejected_whole():
    result = gate({"fields": "everything"}, DOC)
    assert result.accepted == [] and result.review_queue[0].startswith("Output rejected")


def test_extracted_draft_goes_through_the_firm_rules():
    app = extract_firm_application(DOC, client=FakeClient(payload())).application
    ev = evaluate_firm(app, TODAY)
    # REG-002: registration status is unknown until the IAPD lookup fills it in
    assert ev.rule_ids == ["REG-002", "KYB-003", "KYB-005"] and ev.disposition is Disposition.REFER
    assert firm_reliance(app, TODAY).reasons == ["No reliance contract", "No annual AML certification on file"]
