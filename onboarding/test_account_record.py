"""The account record beyond CIP, added after the industry-practice review (Oct. 25, 2026): a phone on file for
callbacks, IRA details, beneficiary shares, joint tenancy, FINRA Rule 3210 consent, and the trusted contact."""
from dataclasses import replace
from datetime import date

import pytest

from account_rules import evaluate_account
from applications import Person
from pipeline import Decisions, open_account
from sample_data import ACCOUNT_FIRMS, ACCOUNTS, FIRMS

AS_OF = date(2026, 10, 26)           # the review's rules are in force from Oct. 25, 2026
BEFORE = date(2026, 10, 20)


def rules_hit(app, as_of=AS_OF):
    return {d.rule_id for d in evaluate_account(app, "A", as_of).decisions}


def run(app):
    firm = FIRMS[ACCOUNT_FIRMS["maria_chen"]]
    return open_account(app, firm, AS_OF, decisions=Decisions())


def test_the_sample_accounts_carry_a_phone_on_file():
    app = ACCOUNTS["maria_chen"]
    assert app.holders[0].phone and app.holders[0].phone.startswith("801-555-01")
    assert rules_hit(app) == set()


def test_a_holder_without_a_phone_comes_back_for_one():
    app = replace(ACCOUNTS["maria_chen"], holders=[replace(ACCOUNTS["maria_chen"].holders[0], phone=None)])
    assert "ACC-008" in rules_hit(app)
    assert "ACC-008" not in rules_hit(app, BEFORE)          # dated: not in force before the review


def test_an_ira_needs_its_type_and_adoption_agreement():
    base = replace(ACCOUNTS["maria_chen"], registration_type="ira")
    assert {"ACC-009", "ACC-010"} <= rules_hit(base)
    ok = replace(base, ira_type="roth", ira_adoption_agreement=True)
    assert not {"ACC-009", "ACC-010"} & rules_hit(ok)


@pytest.mark.parametrize("bens,hit", [
    ([{"name": "A", "kind": "primary", "share": 60}, {"name": "B", "kind": "primary", "share": 40}], False),
    ([{"name": "A", "kind": "primary", "share": 60}, {"name": "B", "kind": "primary", "share": 30}], True),
    ([{"name": "A", "kind": "primary", "share": 100}, {"name": "C", "kind": "contingent", "share": 50}], True),
    ([], False),
])
def test_beneficiary_shares_add_up_to_100(bens, hit):
    app = replace(ACCOUNTS["maria_chen"], beneficiaries=bens)
    assert ("ACC-011" in rules_hit(app)) is hit


def test_a_joint_account_records_its_tenancy_and_shares():
    two = [replace(ACCOUNTS["maria_chen"].holders[0], ownership_pct=60),
           Person("Leo Chen", "account_holder", 30, date_of_birth="1984-01-02", address="1200 Oak St, Salt Lake City",
                  tax_id="900-00-2091", phone="801-555-0177")]
    joint = replace(ACCOUNTS["maria_chen"], registration_type="joint", holders=two)
    assert "ACC-012" in rules_hit(joint)
    tic = replace(joint, joint_tenancy="tenants_in_common")
    assert "ACC-013" in rules_hit(tic)                     # 60% + 30% isn't 100%
    assert not {"ACC-012", "ACC-013"} & rules_hit(replace(joint, joint_tenancy="jtwros"))


def test_a_holder_at_a_finra_member_needs_the_employers_consent():
    holder = replace(ACCOUNTS["maria_chen"].holders[0], finra_affiliated=True, finra_affiliation="Wasatch Securities")
    app = replace(ACCOUNTS["maria_chen"], holders=[holder])
    assert "ACC-014" in rules_hit(app)
    assert "ACC-014" not in rules_hit(replace(app, employer_consent=True))


def test_the_account_record_step_notes_the_trusted_contact_and_rule_144_without_holding_the_account():
    holder = replace(ACCOUNTS["maria_chen"].holders[0], public_company_control=True, public_company="Oquirrh Mining")
    app = replace(ACCOUNTS["maria_chen"], holders=[holder],
                  trusted_contact={"name": "Leo Chen", "relationship": "brother", "phone": "801-555-0177"})
    res = run(app)
    record = next(s for s in res.steps if s.name.startswith("Account record"))
    text = " ".join(record.detail)
    assert "Trusted contact: Leo Chen" in text and "Rule 144" in text and "801-555-01" in text
    assert res.status not in ("PENDING_ITEMS", "RETURNED")      # notes, not a hold
    declined = run(replace(ACCOUNTS["maria_chen"], trusted_contact_declined=True))
    assert "declined" in " ".join(next(s for s in declined.steps if s.name.startswith("Account record")).detail)
    missing = run(ACCOUNTS["maria_chen"])
    assert "reasonable efforts" in " ".join(next(s for s in missing.steps if s.name.startswith("Account record")).detail)
