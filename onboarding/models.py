from enum import Enum
from dataclasses import dataclass
from datetime import date


class AccountGroup(Enum):
    PURE_RIA = "pure_ria"
    SELF_DIRECTED = "self_directed"
class Path(Enum):
    A = "A"
    B = "B"

@dataclass
class RIAFirm:
    name: str
    active: bool
    sec_registered: bool
    reliance_contract: bool
    certification_date: date | None   

@dataclass
class Account:
    account_id: str
    ria: RIAFirm
    group: AccountGroup
    is_open: bool = True


def reliance_eligible(ria, today):
    if ria.certification_date is None:
        return False

    days_since_certification = (today - ria.certification_date).days

    if ria.sec_registered and ria.reliance_contract and 0 <= days_since_certification <= 365:
        return True

    return False

def determine_path(account, today):
    if account.group == AccountGroup.PURE_RIA and reliance_eligible(account.ria, today):
        return Path.A

    return Path.B



def can_open(new_account, existing_accounts):

    if not new_account.ria.active:
        return (False, "RIA not active")

    if new_account.group == AccountGroup.SELF_DIRECTED:

        has_open_ria = any(
            account.group == AccountGroup.PURE_RIA and account.is_open
            for account in existing_accounts
        )

        if not has_open_ria:
            return (
                False,
                "Self-directed account needs an open RIA-managed account"
            )

        has_open_self_directed = any(
            account.group == AccountGroup.SELF_DIRECTED and account.is_open
            for account in existing_accounts
        )

        if has_open_self_directed:
            return (False, "Client already has a self-directed account")

    return (True, "OK")
