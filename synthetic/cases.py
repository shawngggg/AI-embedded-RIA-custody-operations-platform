"""
More onboarding, CIP, and account-change cases for the demo, spread across every queue: opened accounts that form the
book (book.py), accounts open but restricted, identity checks pending or failed, a PEP alert, EDD, a sanctions near
match, missing items, and accounts waiting for the principal. All synthetic.

app/seed.py seeds them after the original scenarios. Tax IDs use 900-00-5xxx (a range no one is issued) so they never
collide with the walkthrough (4xxx), the gate review (31xx), or the tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from applications import AccountApplication, FirmApplication, Person
from models import AccountGroup


@dataclass
class AccountScenario:
    key: str
    firm: str                       # sample_data firm key, or a key in FIRMS below
    app: AccountApplication
    age_hours: float                # how long ago it arrived (opened accounts: roughly when they opened)
    accept: bool = False            # the principal accepts it once it reaches acceptance
    identity_issue: dict | None = None   # a failed identity check CIP records (app/identity_check.py)
    shows: str = ""


@dataclass
class ChangeScenario:
    key: str
    account_id: str
    change_type: str
    details: dict = field(default_factory=dict)
    new_parties: list[dict] = field(default_factory=list)
    client_signature: bool = False
    medallion: bool = False
    age_hours: float = 24
    shows: str = ""


# A third active RIA, so billing and settlement cover more than two firms. Path A: SEC-registered and reliance-eligible.
FIRMS = {
    "timpanogos": FirmApplication(
        legal_name="Timpanogos Wealth Partners LLC", registration="SEC", sec_file_number="801-120031",
        crd_number="310031", iapd_status="approved", raum=850_000_000, adv_last_annual_amendment=date(2026, 3, 12),
        principal_address="1100 N University Ave, Provo, UT 84604", ein="87-1234531", formation_documents=True,
        ownership_chart=True, beneficial_ownership_certification=True, custodial_agreement_signed=True,
        reliance_contract=True, aml_certification_date=date(2026, 2, 20),
        people=[Person("Helena Strand", "control_person", 55, identity_verified=True),
                Person("Gideon Marsh", "owner", 45, identity_verified=True)]),
}
FIRM_AGE_HOURS = {"timpanogos": 900}

_TOWNS = ["Salt Lake City, UT 84102", "Ogden, UT 84403", "Park City, UT 84060", "Logan, UT 84321",
          "St. George, UT 84770", "Boise, ID 83702", "Sandy, UT 84092", "Provo, UT 84604", "Bountiful, UT 84010",
          "Idaho Falls, ID 83402", "Lehi, UT 84043", "Heber City, UT 84032"]
_n = 0


def _holder(name: str, role: str = "account_holder", verified: bool = True, pct: float = 0.0, **kw) -> Person:
    global _n
    _n += 1
    return Person(name, role, pct, date_of_birth=kw.pop("dob", f"19{55 + _n % 35}-{1 + _n % 12:02d}-{1 + _n % 27:02d}"),
                  address=kw.pop("address", f"{100 + 37 * _n} E {200 + 10 * (_n % 9)} S, {_TOWNS[_n % len(_TOWNS)]}"),
                  tax_id=f"900-00-{5000 + _n}", occupation=kw.pop("occupation", "Retired"),
                  identity_verified=verified, **kw)


def _account(n: int, holders: list[Person], registration: str = "individual", *, funding: float, purpose: str,
             lpoa=True, fee=True, signature=True, tax="W-9", entity: dict | None = None,
             group: AccountGroup = AccountGroup.PURE_RIA) -> AccountApplication:
    return AccountApplication(
        f"APP-{3000 + n}", f"ACC-{3000 + n}", group, registration, holders,
        lpoa_signed=lpoa, fee_authorization=fee, client_signature=signature, tax_form=tax, account_purpose=purpose,
        source_of_funds="Salary and savings" if registration != "ira" else "Rollover from an employer plan",
        source_of_wealth="Career earnings and savings", expected_initial_funding=funding, **(entity or {}))


def _scenarios() -> list[AccountScenario]:
    S, A = AccountScenario, _account
    h = _holder
    return [
        # Granite Peak Advisors (Path A): opened, the core of the book
        S("gp_benson", "granite_peak", A(1, [h("Harold Benson", occupation="Retired civil engineer")],
          funding=1_400_000, purpose="Retirement income"), 1700, True),
        S("gp_ng", "granite_peak", A(2, [h("Priscilla Ng", occupation="Physician")],
          funding=2_300_000, purpose="Long-term growth"), 1500, True),
        S("gp_alvarado", "granite_peak", A(3, [h("Tomas Alvarado", occupation="Restaurant owner")],
          funding=640_000, purpose="Long-term growth"), 1300, True),
        S("gp_kowalski_ira", "granite_peak", A(4, [h("Wendy Kowalski", occupation="Teacher")], "ira",
          funding=410_000, purpose="Retirement (traditional IRA)"), 1100, True),
        S("gp_holm_joint", "granite_peak", A(5, [h("Gregory Holm", occupation="Pharmacist"),
                                                h("Anita Holm", occupation="Architect")], "joint",
          funding=1_850_000, purpose="Balanced growth"), 1000, True),
        S("gp_adeyemi_joint", "granite_peak", A(6, [h("Samuel Adeyemi", occupation="Software engineer"),
                                                   h("Ruth Adeyemi", occupation="Nurse")], "joint",
          funding=920_000, purpose="College and retirement savings"), 800, True),
        S("gp_pratt_ira", "granite_peak", A(7, [h("Douglas Pratt", occupation="Electrician")], "ira",
          funding=530_000, purpose="Retirement (traditional IRA)"), 700, True),
        S("gp_castellanos_roth", "granite_peak", A(8, [h("Leah Castellanos", occupation="Product manager")], "ira",
          funding=185_000, purpose="Retirement (Roth IRA)"), 600, True),
        S("gp_benson_trust", "granite_peak", A(9, [h("Harold Benson", "trustee", occupation="Retired civil engineer")],
          "trust", funding=3_100_000, purpose="Estate planning and income",
          entity={"entity_name": "Benson Family Trust", "entity_ein": "87-5550201", "entity_type": "trust",
                  "entity_formation_documents": True}), 1650, True, shows="Household with the Benson individual account"),
        S("gp_canyon_ridge", "granite_peak", A(10, [h("Victor Haines", "control_person", pct=100,
                                                     occupation="Real estate developer")], "llc",
          funding=2_750_000, purpose="Operating reserve investments",
          entity={"entity_name": "Canyon Ridge Holdings LLC", "entity_ein": "87-5550202",
                  "entity_type": "operating_company", "entity_formation_documents": True,
                  "beneficial_ownership_certification": True}), 500, True),
        # Timpanogos Wealth Partners (Path A): opened; it has no portal users, so its paperwork arrived by mail
        S("tw_fairweather", "timpanogos", A(11, [h("Imogen Fairweather", occupation="Attorney")],
          funding=1_250_000, purpose="Long-term growth"), 1400, True),
        S("tw_kulkarni", "timpanogos", A(12, [h("Rajesh Kulkarni", occupation="Cardiologist")],
          funding=2_900_000, purpose="Long-term growth"), 1200, True),
        S("tw_okoro", "timpanogos", A(13, [h("Beatrice Okoro", occupation="University professor")],
          funding=760_000, purpose="Balanced growth"), 900, True),
        S("tw_ferris_joint", "timpanogos", A(14, [h("Colin Ferris", occupation="Pilot"),
                                                 h("Mei Ferris", occupation="Dentist")], "joint",
          funding=1_600_000, purpose="Balanced growth"), 750, True),
        S("tw_lindgren_ira", "timpanogos", A(15, [h("Arthur Lindgren", occupation="Retired banker")], "ira",
          funding=980_000, purpose="Retirement (traditional IRA)"), 650, True),
        S("tw_price_ira", "timpanogos", A(16, [h("Yolanda Price", occupation="Accountant")], "ira",
          funding=320_000, purpose="Retirement (Roth IRA)"), 300, True),
        S("tw_kulkarni_trust", "timpanogos", A(17, [h("Rajesh Kulkarni", "trustee", occupation="Cardiologist")],
          "trust", funding=4_200_000, purpose="Estate planning",
          entity={"entity_name": "Kulkarni Living Trust", "entity_ein": "87-5550203", "entity_type": "trust",
                  "entity_formation_documents": True}), 1150, True, shows="Household with the Kulkarni account"),
        # Bonneville Planning (state-registered, so Path B: the custodian verifies identity): opened
        S("bp_henriksen", "bonneville", A(18, [h("Dale Henriksen", occupation="Farmer")],
          funding=870_000, purpose="Retirement income"), 1250, True),
        S("bp_quist_ira", "bonneville", A(19, [h("Marjorie Quist", occupation="Retired nurse")], "ira",
          funding=440_000, purpose="Retirement (traditional IRA)"), 950, True),
        S("bp_durant_joint", "bonneville", A(20, [h("Neil Durant", occupation="Contractor"),
                                                 h("Carla Durant", occupation="Office manager")], "joint",
          funding=510_000, purpose="Balanced growth"), 850, True),
        S("bp_whitlock", "bonneville", A(21, [h("Ezra Whitlock", occupation="Veterinarian")],
          funding=690_000, purpose="Long-term growth"), 400, True),
        # Open but restricted: a non-regulatory item outstanding (DOCS: no trading or fee deduction)
        S("gp_gallardo_docs", "granite_peak", A(22, [h("Fiona Gallardo", occupation="Graphic designer")],
          funding=230_000, purpose="Long-term growth", fee=False), 200, True,
          shows="Open restricted: fee authorization outstanding"),
        S("tw_feldman_docs", "timpanogos", A(23, [h("Oscar Feldman", occupation="Chef")],
          funding=150_000, purpose="Long-term growth", tax=None), 150, True,
          shows="Open restricted: tax form outstanding"),
        # Path B, opened with identity verification still pending (CIPV: no outgoing activity)
        S("bp_montoya_cipv", "bonneville", A(24, [h("Hazel Montoya", verified=False, occupation="Paralegal")],
          funding=180_000, purpose="Long-term growth"), 100, True, shows="Open; identity pending (CIPV)"),
        S("bp_asante_cipv", "bonneville", A(25, [h("Rupert Asante", verified=False, occupation="Engineer")],
          funding=260_000, purpose="Long-term growth"), 60, True, shows="Open; identity pending (CIPV)"),
        # Not in good order: waiting on the RIA
        S("gp_whitcombe_nigo", "granite_peak", A(26, [h("Clara Whitcombe", occupation="Marketing director")],
          funding=340_000, purpose="Long-term growth", lpoa=False), 30, shows="Items requested: LPOA"),
        S("tw_kirby_nigo", "timpanogos", A(27, [h("Desmond Kirby", occupation="Firefighter")],
          funding=210_000, purpose="Retirement savings", signature=False), 52, shows="Items requested: signature"),
        S("gp_calloway_nigo", "granite_peak", A(28, [h("Ruth Calloway", "trustee", occupation="Retired judge")],
          "trust", funding=1_900_000, purpose="Estate planning",
          entity={"entity_name": "Calloway Family Trust", "entity_ein": "87-5550204", "entity_type": "trust",
                  "entity_formation_documents": False}), 76, shows="Items requested: trust instrument"),
        # Failed identity checks (Path B): back to the RIA, or to AML compliance
        S("bp_fairfax_resubmit", "bonneville", A(29, [h("Lionel Fairfax", verified=False, occupation="Sales manager")],
          funding=300_000, purpose="Long-term growth"), 40,
          identity_issue={"kind": "wrong_document", "person": "Lionel Fairfax",
                          "note": "An expired driver's license; send a current government-issued photo ID"},
          shows="Identity check failed: resubmission requested"),
        S("bp_solberg_fraud", "bonneville", A(30, [h("Ingrid Solberg", verified=False, occupation="Consultant")],
          funding=1_100_000, purpose="Long-term growth"), 20,
          identity_issue={"kind": "suspected_fraud", "person": "Ingrid Solberg",
                          "note": "The passport number belongs to a different person in the verification service, and "
                                  "the date of birth doesn't match the application"},
          shows="Suspected fraudulent identity: held for AML compliance"),
        # Screening alerts: compliance's work
        S("tw_achterberg_pep", "timpanogos", A(31, [h("Gordon Achterberg", occupation="City council member",
                                                     is_pep=True, pep_type="domestic")],
          funding=380_000, purpose="Retirement savings"), 18, shows="Domestic PEP: AML compliance reviews the alert"),
        S("gp_crane_media", "granite_peak", A(32, [h("Bartholomew Crane", occupation="Investment promoter",
                                                  adverse_media=True)],
          funding=2_400_000, purpose="Long-term growth"), 45, shows="Adverse media: EDD"),
        S("gp_halloran_near", "granite_peak", A(33, [h("Teodor M. Halloran", occupation="Shipping executive",
                                                      dob="1971-11-30")],
          funding=950_000, purpose="Long-term growth"), 6, shows="Sanctions near match: the sanctions team decides"),
        # Ready for the principal
        S("gp_talbot_ready", "granite_peak", A(34, [h("Miriam Talbot", occupation="Librarian")],
          funding=290_000, purpose="Retirement savings"), 10, shows="Waiting for the principal"),
        S("gp_sorensen_ready", "granite_peak", A(35, [h("Patrick Sorensen", occupation="Surveyor")], "ira",
          funding=615_000, purpose="Retirement (traditional IRA)"), 22, shows="Waiting for the principal"),
        S("tw_rhee_ready", "timpanogos", A(36, [h("Celeste Rhee", occupation="Data scientist")],
          funding=470_000, purpose="Long-term growth"), 14, shows="Waiting for the principal"),
    ]


ACCOUNTS = _scenarios()

CHANGES = [
    ChangeScenario("chg_benson_address", "ACC-3001", "address",
                   {"new_address": "88 Arlington Dr, Salt Lake City, UT 84103"}, age_hours=200,
                   shows="Address change applied"),
    ChangeScenario("chg_ng_contact", "ACC-3002", "contact_info", {"email": "p.ng@example.com", "phone": "801-555-0142"},
                   age_hours=90, shows="Contact change applied"),
    ChangeScenario("chg_alvarado_beneficiary", "ACC-3003", "beneficiary",
                   {"beneficiaries": "Lucia Alvarado 50%, Mateo Alvarado 50%"}, client_signature=True, age_hours=60,
                   shows="Beneficiary change applied with the client-signed form"),
    ChangeScenario("chg_durant_owner", "ACC-3020", "registration", {"add_owner": "Paul Durant"},
                   new_parties=[{"name": "Paul Durant", "role": "owner", "ownership_pct": 0,
                                 "date_of_birth": "1998-04-11", "address": "77 N 400 E, Ogden, UT 84403",
                                 "tax_id": "900-00-5099"}],
                   client_signature=True, medallion=True, age_hours=12,
                   shows="New owner on a Path B account: CIP verifies identity"),
    ChangeScenario("chg_henriksen_retitle", "ACC-3018", "registration",
                   {"retitle": "Individual with transfer on death to Anna Henriksen"}, client_signature=True,
                   age_hours=30, shows="Returned to the RIA: medallion signature guarantee missing"),
]
