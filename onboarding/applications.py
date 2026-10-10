"""
Application records for the onboarding module: the stated facts about an RIA
firm or a client account, before any rule has judged them.

As in the onboarding execution engine, facts are stored and judgments are
computed: an application says what was submitted, and the rule sets decide
what it means. All names and numbers in this project are synthetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from models import AccountGroup


@dataclass
class Person:
    """An individual on an application: owner, control person, account holder, and so on."""
    name: str
    role: str = "owner"             # owner | control_person | principal | account_holder | trustee | authorized_signer
    ownership_pct: float = 0.0
    date_of_birth: str | None = None     # ISO date
    country_of_residence: str = "United States"
    nationality: str = "United States"
    address: str | None = None
    tax_id: str | None = None            # synthetic SSN, ITIN, or foreign tax number
    occupation: str | None = None
    identity_verified: bool = False
    is_pep: bool = False
    pep_type: str | None = None          # foreign | domestic | international_org
    adverse_media: bool = False
    iar_registration_required: bool = False
    iar_registered: bool = False


@dataclass
class FirmApplication:
    """An RIA firm applying to custody its clients' assets on the platform."""
    legal_name: str
    entity_type: str = "LLC"                 # LLC | corporation | partnership
    registration: str = "SEC"                # SEC | state | none
    sec_file_number: str | None = None       # 801-xxxxx
    crd_number: str | None = None
    iapd_status: str | None = "approved"     # status from the IAPD lookup
    raum: float = 0.0                        # regulatory assets under management, Form ADV Item 5.F
    sec_registration_basis: str | None = None  # exemption relied on if RAUM is under $100M
    fiscal_year_end_month: int = 12
    adv_last_annual_amendment: date | None = None
    registration_date: date | None = None
    disciplinary_disclosures: bool = False
    principal_address: str | None = None
    ein: str | None = None
    formation_documents: bool = False
    ownership_chart: bool = False
    beneficial_ownership_certification: bool = False
    custodial_agreement_signed: bool = False
    reliance_contract: bool = False
    aml_certification_date: date | None = None
    people: list[Person] = field(default_factory=list)
    ownership_layers: int = 1
    jurisdictions: list[str] = field(default_factory=lambda: ["United States"])
    foreign_client_pct: float = 0.0
    adverse_media: bool = False


@dataclass
class Position:
    """One holding on a transfer statement or funding request."""
    security_id: str                 # CUSIP or internal identifier (synthetic)
    description: str
    asset_type: str                  # equity | etf | mutual_fund | bond | uit | alternative | annuity | private_placement
    quantity: float
    market_value: float = 0.0
    issuer: str | None = None


@dataclass
class AccountApplication:
    """A client account application submitted by the RIA."""
    application_id: str
    account_id: str
    group: AccountGroup
    registration_type: str = "individual"      # individual | joint | trust | llc | corporation | ira
    holders: list[Person] = field(default_factory=list)
    entity_name: str | None = None
    entity_ein: str | None = None
    entity_type: str | None = None             # operating_company | private_investment_company | shell | trust
    entity_formation_documents: bool = False
    bearer_shares: bool = False
    beneficial_ownership_certification: bool = False
    first_entity_account: bool = True          # first account for this legal entity customer
    bo_info_confirmed_current: bool = False    # customer confirmed earlier beneficial ownership info is current
    lpoa_signed: bool = False                  # limited power of attorney for the RIA
    fee_authorization: bool = False            # written authorization to deduct advisory fees
    client_signature: bool = False
    tax_form: str | None = None                # W-9 | W-8BEN | W-8BEN-E
    account_purpose: str | None = None
    source_of_funds: str | None = None
    source_of_wealth: str | None = None
    expected_initial_funding: float = 0.0
    third_party_funding: bool = False
    funding_positions: list[Position] = field(default_factory=list)

    @property
    def is_entity(self) -> bool:
        return self.registration_type in ("trust", "llc", "corporation")

    @property
    def primary(self) -> Person | None:
        for p in self.holders:
            if p.role == "account_holder":
                return p
        return self.holders[0] if self.holders else None
