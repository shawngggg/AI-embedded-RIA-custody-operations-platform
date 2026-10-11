"""Turn engine dataclasses into JSON for the database and back."""
from __future__ import annotations

import dataclasses
from datetime import date, timedelta
from enum import Enum
from typing import Any

from account_changes import ChangeRequest, ChangeType
from applications import AccountApplication, FirmApplication, Person, Position
from models import AccountGroup

DATE_FIELDS = {
    FirmApplication: {"adv_last_annual_amendment", "registration_date", "aml_certification_date"},
}


def to_json(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return {f.name: to_json(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, (list, tuple)):
        return [to_json(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_json(v) for k, v in obj.items()}
    return obj


def _date(v):
    return date.fromisoformat(v) if isinstance(v, str) and v else (v or None)


def person_from(d: dict) -> Person:
    names = {f.name for f in dataclasses.fields(Person)}
    return Person(**{k: v for k, v in d.items() if k in names})


def firm_from(d: dict) -> FirmApplication:
    names = {f.name for f in dataclasses.fields(FirmApplication)}
    data = {k: v for k, v in d.items() if k in names}
    for k in DATE_FIELDS[FirmApplication]:
        if k in data:
            data[k] = _date(data[k])
    data["people"] = [person_from(p) for p in data.get("people", [])]
    return FirmApplication(**data)


def account_from(d: dict) -> AccountApplication:
    names = {f.name for f in dataclasses.fields(AccountApplication)}
    data = {k: v for k, v in d.items() if k in names}
    data["group"] = AccountGroup(data["group"])
    data["holders"] = [person_from(p) for p in data.get("holders", [])]
    data["funding_positions"] = [Position(**p) for p in data.get("funding_positions", [])]
    return AccountApplication(**data)


def change_from(d: dict) -> ChangeRequest:
    names = {f.name for f in dataclasses.fields(ChangeRequest)}
    data = {k: v for k, v in d.items() if k in names}
    data["account_group"] = AccountGroup(data["account_group"])
    data["change_type"] = ChangeType(data["change_type"])
    data["new_parties"] = [person_from(p) for p in data.get("new_parties", [])]
    data["recent_changes"] = [(ChangeType(k), _date(v)) for k, v in data.get("recent_changes", [])]
    return ChangeRequest(**data)


def shift_dates(obj: Any, delta: timedelta) -> Any:
    """Move every date in a dataclass tree by delta, so seeded data stays current."""
    if delta.days == 0:
        return obj
    if dataclasses.is_dataclass(obj):
        changes = {f.name: shift_dates(getattr(obj, f.name), delta) for f in dataclasses.fields(obj)}
        return dataclasses.replace(obj, **changes)
    if isinstance(obj, date):
        return obj + delta
    if isinstance(obj, list):
        return [shift_dates(x, delta) for x in obj]
    return obj
