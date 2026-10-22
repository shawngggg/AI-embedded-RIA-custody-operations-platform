"""The synthetic book (synthetic/book.py): deterministic, and its pieces agree with each other and with the rules."""
from datetime import date, timedelta

import pytest

import app  # noqa: F401  (puts the onboarding engine on the path)
from restrictions import Activity
from synthetic import book

AS_OF = date(2026, 10, 21)        # a Wednesday


def _accounts() -> list[dict]:
    def a(n, crd, days_ago, reg="individual", group="pure_ria", codes=(), household=None, purpose="Long-term growth",
          funding=600_000, name=None):
        return {"id": f"ACC-9{n:03d}", "name": name or f"Client {n}", "firm_crd": crd, "group": group,
                "registration_type": reg, "path": "A", "opened_on": AS_OF - timedelta(days=days_ago),
                "codes": list(codes), "household": household or f"H{n}", "purpose": purpose, "funding": funding}
    return [
        a(1, "310001", 90, household="H1", funding=1_400_000, name="Harold Example"),
        a(2, "310001", 50, reg="trust", household="H1", funding=3_200_000, name="Example Family Trust"),
        a(3, "310001", 80, reg="ira", purpose="Retirement (traditional IRA)", funding=450_000),
        a(4, "310001", 75, reg="joint", purpose="Balanced growth", funding=2_100_000),
        a(5, "310001", 70, purpose="Retirement income", funding=180_000),
        a(6, "310001", 3, codes=["DOCS"], funding=240_000),
        a(7, "310001", 40, group="self_directed", household="H1", funding=60_000),
        a(8, "310031", 110, funding=1_100_000),
        a(9, "310031", 60, reg="ira", purpose="Retirement (Roth IRA)", funding=300_000),
        a(10, "310031", 55, reg="joint", funding=900_000),
        a(11, "310003", 100, funding=800_000),
        a(12, "310003", 95, reg="ira", purpose="Retirement (traditional IRA)", funding=500_000),
        a(13, "310003", 1, codes=["CIPV"], funding=200_000),
        a(14, "310003", 30, codes=["DOCS"], funding=350_000),
    ]


FIRMS = {"310001": "Granite Peak Advisors LLC", "310031": "Timpanogos Wealth Partners LLC",
         "310003": "Bonneville Planning LLC"}


@pytest.fixture(scope="module")
def b():
    return book.build(_accounts(), FIRMS, AS_OF)


def test_the_book_is_deterministic(b):
    assert book.build(_accounts(), FIRMS, AS_OF) == b


def test_cusips_carry_a_valid_check_digit():
    assert book.cusip_check("03783310") == "0"           # a real CUSIP's check digit, to prove the method
    for s in book.SECURITIES:
        if s.cusip[0].isdigit():
            assert book.cusip_check(s.cusip[:8]) == s.cusip[8]


def test_business_days_skip_weekends_and_fixed_holidays():
    assert book.bday(date(2026, 10, 24)) == date(2026, 10, 23)            # Saturday rolls back to Friday
    assert book.bday(date(2026, 12, 24), 1) == date(2026, 12, 28)        # past Christmas and the weekend
    assert book.bdays_between(date(2026, 10, 16), date(2026, 10, 19)) == 1


def test_restriction_codes_do_what_the_catalog_says(b):
    accounts = {a["account"]: a for a in b["accounts"]}

    def blocked(acct, activity):
        return book.blocking({"codes": accounts[acct]["codes"]}, activity)
    traded = {x["account"] for x in b["allocations"]}
    assert not [x for x in traded if blocked(x, Activity.BUY) or blocked(x, Activity.SELL)]
    assert not [x for x in traded if accounts[x]["funding_in_transit"]]
    gone = [m for m in b["movements"] if m["direction"] == "Out" and m["account"]
            and not m["status"].startswith("Blocked") and blocked(m["account"], Activity.WITHDRAWAL)]
    assert gone == []
    assert any(m["status"] == "Blocked: CIPV" for m in b["movements"])
    for f in b["fees"]:
        if blocked(f["account"], Activity.FEE_DEDUCTION):
            assert f["status"] == "Can't debit"


def test_block_orders_allocate_what_filled_and_settle_t_plus_1(b):
    for o in b["orders"]:
        allocs = [x for x in b["allocations"] if x["order"] == o["order"]]
        if o["filled"] is not None:
            assert round(sum(x["quantity"] for x in allocs), 3) == o["filled"]
        td = date.fromisoformat(o["trade_date"])
        assert o["settle_date"] == book.bday(td, 1).isoformat()
    assert any(o["entered_by"] == "Client (self-directed)" for o in b["orders"])
    assert not [x for x in b["allocations"] if x["side"] == "Buy" and x["amount"] and x["account"] in
                {a["account"] for a in b["accounts"] if a["group"] == "self_directed"} and x["order"].startswith("BLK")]


def test_the_three_layers_and_the_ledger_agree_except_the_open_break(b):
    s = b["settlement"]
    client_cash = round(sum(a["cash"] for a in b["accounts"]), 2)
    assert round(sum(x["client_cash"] for x in s["layers"]), 2) == client_cash
    assert all(x["client_cash"] == x["ria_master_cash"] for x in s["layers"])
    assert next(g for g in s["gl"] if g["account"] == "2100")["balance"] == client_cash
    r01 = next(x for x in b["breaks"] if x["id"] == "CB-R01")
    assert s["gap"] == -r01["value"]
    assert "CB-R01" in s["checks"][2]["detail"]


def test_fails_show_up_in_reconciliation_or_on_the_ledger(b):
    breaks = {x["id"] for x in b["breaks"]}
    gl = {g["account"]: g["balance"] for g in b["settlement"]["gl"]}
    for f in b["fails"]:
        if f["side"] == "Buy":
            assert f"RB-{f['id']}" in breaks
    assert gl["2300"] == round(sum(f["amount"] for f in b["fails"] if f["side"] == "Buy"), 2)
    assert gl["1300"] == round(sum(f["amount"] for f in b["fails"] if f["side"] == "Sell"), 2)
    ftd = next(f for f in b["fails"] if f["side"] == "Sell")
    assert ftd["close_out_by"] == book.bday(date.fromisoformat(ftd["settle_date"]), 3).isoformat()


def test_transfers_follow_acats_timing_and_the_transferability_rules(b):
    accounts = {a["account"]: a for a in b["accounts"]}
    for t in b["transfers"]:
        if t["type"] == "ACATS full transfer" and t["direction"] == "Incoming":
            done = t["status"].startswith("Completed")
            assert done == (t["business_days"] >= book.ACATS_DAYS)
            assert accounts[t["account"]]["funding_in_transit"] == (not done)
        if any(p["route"] == "liquidate" for p in t["positions"]) and not t["status"].startswith("Completed"):
            assert t["release"] == "partial"
    in_transit = {a for a, x in accounts.items() if x["funding_in_transit"]}
    assert not [p for p in b["positions"] if p["account"] in in_transit]
    assert any(t["type"].startswith("Non-ACATS") for t in b["transfers"])
    assert any(t["direction"] == "Outgoing" for t in b["transfers"])


def test_billing_tiers_households_and_waits_for_the_ria(b):
    fees = {(f["run"], f["account"]): f for f in b["fees"]}
    granite = [f for f in b["fees"] if f["firm_crd"] == "310001" and f["annual_rate"]]
    trust = next(f for f in granite if f["account"] == "ACC-9002")
    assert trust["tier_basis_value"] > trust["billable_value"]         # billed on the household's combined value
    assert trust["annual_rate"] < 0.01
    sd = next(f for f in b["fees"] if f["account"] == "ACC-9007")
    assert sd["advisory_fee"] == 0 and sd["custody_fee"] > 0
    catch_up = [r for r in b["billing_runs"] if r["kind"] == "Catch-up for new accounts"]
    assert catch_up and all(r["status"].startswith("Waiting for") for r in catch_up)
    assert not [f for (run, _), f in fees.items() if run.endswith("-NEW") and f["status"] == "Posted"]
    rejected = [f for f in b["fees"] if f["status"] == "Rejected line"]
    assert rejected and all(f["firm_crd"] == "310003" for f in rejected)
    assert any(f["status"] == "Refund due" for f in b["fees"])
