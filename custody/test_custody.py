"""Release 2 engine tests: the ledger, money movement, settlement, reconciliation, entitlements, products, lifecycle."""
from datetime import date, timedelta

import pytest

import custody  # noqa: F401
from sample_data import FLAGGED_LIST
from transferability import SECURITY_MASTER, SecurityRecord

from custody import entitlements, ledger, lifecycle, money, policy, products, recon, settlement
from custody.calendar import add_bdays, bday
from custody.ledger import CASH, Leg, client_move, gl_move, units

AS_OF = date(2026, 10, 21)          # a Wednesday
FIRMS = {"ACC-1": "310001", "ACC-2": "310001", "ACC-3": "310003", "ACC-SD": "310001"}
STUM = "990201AA0"
MASTER = {**SECURITY_MASTER, STUM: SecurityRecord(STUM, "Summit Total US Market ETF", "etf", dtc_eligible=True,
                                                  accepted_on_platform=True)}


def acct(id_="ACC-1", **kw):
    base = {"id": id_, "name": "Maria Chen", "firm_crd": FIRMS.get(id_, "310001"), "group": "pure_ria",
            "registration_type": "individual", "status": "OPEN", "codes": [], "owners": ["Maria Chen"], "lpoa": True,
            "household": "H1", "recent_changes": []}
    base.update(kw)
    return base


def ctx(**kw):
    base = dict(as_of=AS_OF, account=acct(), cash_available=50_000, holdings={STUM: 100},
                security_master=MASTER, prices={STUM: 268.4}, flagged=FLAGGED_LIST,
                bank_links={"BL-1": {"bank": "Wasatch Community Bank", "last4": "2290", "owner": "Maria Chen",
                                     "status": "verified"}})
    base.update(kw)
    return money.TransferContext(**base)


def balanced(res):
    for e in res.entries:
        errs = ledger.validate(e["legs"], FIRMS, relink_of=e.get("relink"))
        assert not errs, (e["ref"], errs)
    return res


def run(req, c=None, decisions=None, events=None):
    return balanced(money.process_transfer(req, c or ctx(), decisions, events))


def ria(kind, **kw):
    return {"type": kind, "account_id": "ACC-1", "requester": {"role": "ria"}, **kw}


# --- ledger -----------------------------------------------------------------------------------
def test_a_posting_that_breaks_the_rollup_is_rejected():
    good = client_move("ACC-1", "310001", CASH, units(100), "settled", "1010")
    assert ledger.validate(good, FIRMS) == []
    bad = good[:-1]                                         # no contra: the GL doesn't balance
    assert any("don't balance" in e for e in ledger.validate(bad, FIRMS))
    wrong_master = [Leg("client", "ACC-1", CASH, "settled", units(100)), Leg("master", "MASTER-310003", CASH,
                    "settled", units(100)), Leg("gl", "2100", CASH, "settled", -units(100)),
                    Leg("gl", "1010", CASH, "settled", units(100))]
    assert ledger.validate(wrong_master, FIRMS)


def test_the_proof_finds_a_master_out_of_step():
    rows = [("client", "ACC-1", CASH, "settled", 5), ("master", "MASTER-310001", CASH, "settled", 5),
            ("gl", "2100", CASH, "settled", -5), ("gl", "1010", CASH, "settled", 5)]
    assert all(ledger.proof(rows, FIRMS)[k] for k in ("clients_equal_masters", "masters_equal_gl", "gl_balances"))
    off = rows + [("master", "MASTER-310001", CASH, "settled", 1)]
    assert not ledger.proof(off, FIRMS)["clients_equal_masters"]


def test_a_relink_moves_only_masters():
    legs = ledger.relink("ACC-1", "310001", "310003", {(CASH, "settled"): 10})
    assert ledger.validate(legs, FIRMS, relink_of=("ACC-1", "310001", "310003")) == []
    assert ledger.validate(legs + gl_move("1010", "2190", CASH, 1), FIRMS, relink_of=("ACC-1", "310001", "310003"))


# --- money movement (map 08) --------------------------------------------------------------------
def test_authority_follows_the_account_model():
    sd = acct("ACC-SD", group="self_directed")
    assert run(ria("wire_out", amount=100), ctx(account=sd)).status == "REJECTED"           # RIA view-only on SD
    client_out = {"type": "ach_withdrawal", "account_id": "ACC-1", "amount": 100, "requester": {"role": "client"}}
    assert "through the RIA" in run(client_out).requested_items[0]
    assert run(ria("wire_out", amount=100), ctx(account=acct(lpoa=False))).status == "REJECTED"
    ops = {"type": "wire_out", "amount": 100, "requester": {"role": "ops"}}
    assert run(ops).status == "REJECTED"


def test_codes_block_what_the_catalog_says():
    res = run(ria("wire_out", amount=100), ctx(account=acct(codes=["CIPV"])))
    assert res.status == "BLOCKED" and "CIPV" in res.steps[-1].outcome
    dep = ria("ach_deposit", amount=100, bank_link_id="BL-1")
    assert run(dep, ctx(account=acct(codes=["CIPV"]))).status != "BLOCKED"                  # CIPV allows deposits
    assert run(dep, ctx(account=acct(codes=["OUTO"]))).status == "BLOCKED"


def test_outgoing_cash_needs_settled_cash_and_release_under_maker_checker():
    assert run(ria("wire_out", amount=60_000)).status == "REJECTED"
    req = ria("wire_out", amount=20_000, counterparty={"owner_name": "Maria Chen", "institution": "Wasatch Bank"})
    res = run(req)
    assert res.status == "PENDING_RELEASE" and "asset transfer analyst" in res.steps[-1].outcome
    big = ria("wire_out", amount=150_000, counterparty={"owner_name": "Maria Chen", "institution": "Wasatch Bank"})
    res = run(big, ctx(cash_available=500_000), decisions={"release": "released"})
    assert res.status == "PENDING_CALLBACK"                       # over $100,000: a callback first
    res = run(big, ctx(cash_available=500_000), events=[{"type": "callback", "confirmed": True, "by": "x",
                                                         "on": "2026-10-21"}])
    assert res.status == "PENDING_RELEASE" and "supervisor" in res.steps[-1].outcome


def test_a_released_wire_posts_pending_then_settles_and_balances():
    req = ria("wire_out", amount=20_000, counterparty={"owner_name": "Maria Chen", "institution": "Wasatch Bank"})
    res = run(req, decisions={"release": "released"})
    assert res.status == "AWAITING_SETTLEMENT" and res.record["submitted"]["n"] == 1
    assert [e["ref"] for e in res.entries] == ["submit-1"]
    ev = [{"type": "submitted", "n": 1, "on": "2026-10-21"},
          {"type": "rail", "n": 1, "outcome": "confirmed", "on": "2026-10-21"}]
    res = run(req, decisions={"release": "released"}, events=ev)
    assert res.status == "COMPLETED" and [e["ref"] for e in res.entries] == ["submit-1", "settle-1"]


HARBOR = {"owner_name": "Harbor Title & Escrow", "owner_kind": "entity", "institution": "Wasatch Bank",
          "account_last4": "3302"}
SLOA = {"id": "SLOA-7", "payee": "Harbor Title & Escrow", "payee_address": "40 W 200 S, Salt Lake City, UT",
        "institution": "Wasatch Bank", "account_last4": "3302", "status": "active", "effective_on": "2026-10-01"}


def test_a_clients_signed_request_to_a_third_party_goes_to_risk_review():
    req = {"type": "wire_out", "account_id": "ACC-1", "amount": 5_000, "requester": {"role": "signed_form"},
           "client_signed": True, "counterparty": HARBOR}
    assert run(req).status == "PENDING_RISK_REVIEW"
    assert run(req, decisions={"risk_review": "rejected"}).status == "REJECTED"
    assert run(req, decisions={"risk_review": "approved"}).status == "PENDING_CALLBACK"


def test_an_ria_pays_a_third_party_only_under_the_clients_sloa():
    req = ria("wire_out", amount=5_000, counterparty=HARBOR)
    res = run(req)
    assert res.status == "REJECTED" and "standing letter of authorization" in res.requested_items[0]
    covered = run({**req, "sloa_id": "SLOA-7"}, ctx(account=acct(sloas=[SLOA])))
    assert covered.status == "PENDING_RELEASE"                       # verified at setup: no third-party risk review
    assert any(s.name.startswith("Standing letter") and "MM-011" in s.rule_ids for s in covered.steps)
    changed = {**req, "sloa_id": "SLOA-7", "counterparty": {**HARBOR, "account_last4": "9999"}}
    assert "exactly" in run(changed, ctx(account=acct(sloas=[SLOA]))).requested_items[0]
    ended = ctx(account=acct(sloas=[{**SLOA, "status": "ended", "ended_on": "2026-10-15"}]))
    assert "isn't active" in run({**req, "sloa_id": "SLOA-7"}, ended).requested_items[0]
    done = run({**req, "sloa_id": "SLOA-7"}, ctx(account=acct(sloas=[SLOA])), {"release": "released"},
               [{"type": "submitted", "n": 1, "on": "2026-10-21"},
                {"type": "rail", "n": 1, "outcome": "confirmed", "on": "2026-10-21"}])
    assert done.status == "COMPLETED" and any(s.name.startswith("Transfer notice to the client") for s in done.steps)


def test_fraud_signals_read_the_basis_recorded_at_intake():
    basis = {"on": "2026-10-21", "account_value": 200_000, "known_destination": False, "outgoing": []}
    mine = {"owner_name": "Maria Chen", "institution": "Lakeview Federal Credit Union", "account_last4": "1188"}
    first = ria("wire_out", amount=12_000, counterparty=mine, screen_basis=basis)
    assert run(first).status == "PENDING_RISK_REVIEW"
    assert any("First payment" in d for s in run(first).steps for d in s.detail)
    assert run({**first, "screen_basis": {**basis, "known_destination": True}}).status == "PENDING_RELEASE"
    assert run({**first, "screen_basis": None}).status == "PENDING_RELEASE"     # from before the rule
    busy = {**basis, "known_destination": True, "outgoing": [{"on": "2026-10-19", "amount": 1_000},
                                                             {"on": "2026-10-20", "amount": 1_000}]}
    assert any("3 outgoing requests" in x for x in money.fraud_signals({**first, "screen_basis": busy}))
    drain = {**basis, "known_destination": True, "account_value": 13_000}
    assert any("of the account's" in x for x in money.fraud_signals({**first, "screen_basis": drain}))
    new_link = ria("ach_withdrawal", amount=6_000, bank_link_id="BL-1",
                   screen_basis={**basis, "link_verified_on": "2026-10-16"})
    assert any("verified on 2026-10-16" in x for x in money.fraud_signals(new_link))
    assert not money.fraud_signals({**new_link, "amount": 4_000})


def test_a_release_after_the_cutoff_or_on_a_closed_day_goes_out_the_next_business_day():
    from datetime import time
    req = ria("wire_out", amount=5_000, counterparty={"owner_name": "Maria Chen", "institution": "Wasatch Bank"})
    morning = run(req, ctx(clock=time(10, 0)), {"release": "released"})
    assert morning.record["submitted"] == {"n": 1, "on": "2026-10-21"}
    late = run(req, ctx(clock=time(17, 30)), {"release": "released"})
    assert late.record["submitted"]["sends_on"] == "2026-10-22" and late.summary["Expected to settle"] == "2026-10-22"
    assert late.entries[0]["on"] == date(2026, 10, 21)        # the pending entries post at release
    columbus = run(req, ctx(as_of=date(2026, 10, 12), clock=time(9, 0)), {"release": "released"})
    assert columbus.record["submitted"]["sends_on"] == "2026-10-13"      # Fedwire is closed on Columbus Day
    acats = money.expected_dates("acats_in", date(2026, 10, 9))
    assert acats["validate"] == date(2026, 10, 12)                        # NSCC is open on Columbus Day


def test_the_calendars_know_their_holidays():
    from custody.calendar import holidays, is_bday
    assert len(holidays(2026, "fed")) == 10                # 11 holidays; July 4 falls on a Saturday
    assert not is_bday(date(2026, 11, 11)) and is_bday(date(2026, 11, 11), "nscc")
    assert not is_bday(date(2026, 4, 3), "nyse") and is_bday(date(2026, 4, 3))         # Good Friday
    assert not is_bday(date(2026, 7, 3), "nyse") and is_bday(date(2026, 7, 3), "fed")
    assert is_bday(date(2027, 12, 31), "nyse")             # New Year's on a Saturday doesn't close the Friday


def test_a_flagged_counterparty_bank_goes_to_the_sanctions_team():
    req = ria("wire_out", amount=5_000, counterparty={"owner_name": "Maria Chen", "institution": "Banco Austral del Mar",
                                                       "bic": "BAMRPAPAXXX"})
    assert run(req).status == "PENDING_SANCTIONS"
    assert run(req, decisions={"sanctions": "true_match"}).status == "BLOCKED"


def test_client_self_service_needs_grant_passcode_and_verified_link():
    sd = acct("ACC-SD", group="self_directed", name="Farid Hosseini", owners=["Farid Hosseini"])
    grant = {"status": "active", "capability": "ach_out", "per_request": 10_000, "per_day": 15_000,
             "destinations": ["BL-1"], "expires_on": "2027-06-30"}
    req = {"type": "ach_withdrawal", "account_id": "ACC-SD", "amount": 2_000, "bank_link_id": "BL-1",
           "requester": {"role": "client"}, "otp_verified": True,
           "counterparty": {"owner_name": "Farid Hosseini", "institution": "Wasatch Community Bank"}}
    assert run(req, ctx(account=sd)).status == "REJECTED"                                # no grant
    res = run(req, ctx(account=sd, grant=grant))
    assert res.status == "AWAITING_SETTLEMENT"                                           # straight through
    assert run({**req, "otp_verified": False}, ctx(account=sd, grant=grant)).status == "REJECTED"
    assert run({**req, "amount": 12_000}, ctx(account=sd, grant=grant)).status == "REJECTED"


def test_an_ach_deposit_is_held_then_released_or_returned():
    req = ria("ach_deposit", amount=7_500, bank_link_id="BL-1")
    submitted = date(2026, 10, 14)
    settle = add_bdays(submitted, 1)
    ev = [{"type": "submitted", "n": 1, "on": submitted.isoformat()},
          {"type": "rail", "n": 1, "outcome": "confirmed", "on": settle.isoformat()}]
    res = run(req, ctx(as_of=settle), events=ev)
    assert res.status == "COMPLETED" and "release-1" not in [e["ref"] for e in res.entries]
    res = run(req, ctx(as_of=add_bdays(settle, 2)), events=ev)
    assert "release-1" in [e["ref"] for e in res.entries]
    ret = ev + [{"type": "rail", "n": 1, "outcome": "returned", "code": "R01", "on": add_bdays(settle, 1).isoformat()}]
    res = run(req, ctx(as_of=add_bdays(settle, 1)), events=ret)
    assert res.status == "RETURNED" and "return-1" in [e["ref"] for e in res.entries]


def test_acats_in_routes_positions_and_corrects_a_reject():
    positions = [{"security_id": STUM, "description": "Summit Total US Market ETF", "asset_type": "etf",
                  "quantity": 50},
                 {"security_id": "SYN-REIT-01", "description": "Summit REIT", "asset_type": "alternative",
                  "quantity": 1000},
                 {"security_id": "SYN-LP-02", "description": "Harbor Creek LP", "asset_type": "alternative",
                  "quantity": 10}]
    req = ria("acats_in", positions=positions, title_on_form="Maria Chen")
    res = run(req)
    assert res.status == "WAITING_PRODUCT" and res.spawn[0]["security_id"] == "SYN-LP-02"
    res = run(req, ctx(products={"SYN-LP-02": "declined"}))
    assert res.status == "AWAITING_SETTLEMENT"
    assert any(s["key"] == "loi" for s in res.spawn)                      # the REIT re-registers by LOI
    ev = [{"type": "submitted", "n": 1, "on": "2026-10-14"},
          {"type": "rail", "n": 1, "outcome": "rejected", "code": "title", "on": "2026-10-15"}]
    res = run(req, ctx(products={"SYN-LP-02": "declined"}), events=ev)
    assert res.status == "PENDING_CORRECTION" and "reject-1" in [e["ref"] for e in res.entries]
    ev += [{"type": "correction", "n": 1, "on": "2026-10-16"}]
    res = run(req, ctx(products={"SYN-LP-02": "declined"}), events=ev)
    assert res.status == "AWAITING_SETTLEMENT" and res.record["submitted"]["n"] == 2


def test_a_title_mismatch_waits_for_the_suggested_fix():
    req = ria("acats_in", positions=[{"security_id": STUM, "quantity": 10}], title_on_form="Maria L. Chen")
    assert run(req).status == "PENDING_FIX"
    assert run(req, events=[{"type": "fix", "applied": True, "by": "x"}]).status == "AWAITING_SETTLEMENT"


def test_an_overdue_transfer_is_chased():
    req = ria("acats_in", positions=[{"security_id": STUM, "quantity": 10}])
    ev = [{"type": "submitted", "n": 1, "on": "2026-10-01"}]
    assert run(req, events=ev).status == "OVERDUE"


def test_ira_distributions_withhold_ten_percent():
    ira = acct(registration_type="ira", purpose="Retirement (traditional IRA)")
    req = ria("ach_withdrawal", amount=10_000, bank_link_id="BL-1",
              counterparty={"owner_name": "Maria Chen", "institution": "Wasatch"})
    res = run(req, ctx(account=ira), decisions={"release": "released"})
    legs = res.entries[0]["legs"]
    assert any(x.account == "2700" and x.units == -units(1_000) for x in legs)


def test_the_rail_simulator_answers_on_time():
    req = ria("acats_in", positions=[{"security_id": STUM, "quantity": 10}])
    out = money.rail_response(req, date(2026, 10, 14), date(2026, 10, 21))
    assert [o["outcome"] for o in out] == ["validated", "confirmed"]
    assert money.rail_response(req, date(2026, 10, 14), date(2026, 10, 21), {"reject": "nof"})[0]["code"] == "nof"


# --- settlement and reconciliation (maps 12, 13) -------------------------------------------------
def test_unapplied_cash_matching():
    accts = [{"id": "ACC-3015", "name": "Arthur Lindgren", "owners": ["Arthur Lindgren"]},
             {"id": "ACC-3005", "name": "Gregory Holm", "owners": ["Gregory Holm"]}]
    typo = settlement.match_receipt("FBO ACC-3O15 closing proceeds", "Harbor Title & Escrow", accts)
    assert typo[0]["account"] == "ACC-3015" and typo[0]["score"] < 0.9 and settlement.auto_apply(typo) is None
    exact = settlement.match_receipt("ACC-3005 Gregory Holm deposit", "Gregory Holm", accts)
    assert settlement.auto_apply(exact)["account"] == "ACC-3005"
    assert settlement.return_due(date(2026, 10, 1), AS_OF)


def test_breaks_route_and_escalate():
    assert recon.route("dividend_reinvestment", 5_000) == "auto_fix"
    assert recon.route("ach_return_unbooked", 7_500) == "ai_suggest"
    assert recon.route("unexplained", 500) == "human_only"
    assert recon.route("dividend_reinvestment", 200_000) == "human_only"
    assert recon.escalate(bday(AS_OF, -7), AS_OF, 500)
    assert recon.escalate(AS_OF, AS_OF, 500) is None
    assert recon.compare({("1500", STUM): 100}, {("1500", STUM): 112})[0]["difference"] == 12
    assert recon.certifiable([{"status": "open", "pattern": "in_transit"}])[0]


# --- entitlements (map 05) ------------------------------------------------------------------------
def test_grants_follow_the_account_type_rules():
    sd = acct("ACC-SD", group="self_directed")
    links = {"BL-1": {"bank": "Wasatch", "last4": "2290", "status": "pending"}}
    g = entitlements.GrantContext(AS_OF, sd, "310001", links)
    req = {"capability": "ach_out", "per_request": 5_000, "per_day": 10_000, "destinations": ["BL-1"],
           "expires_on": "2027-06-30"}
    assert entitlements.process_grant(req, g).status == "PENDING_LINK_VERIFICATION"
    links["BL-1"]["status"] = "verified"
    assert entitlements.process_grant(req, g).status == "ACTIVE"
    assert entitlements.process_grant({**req, "capability": "margin"}, g).status == "DECLINED"
    pure = entitlements.GrantContext(AS_OF, acct(), "310001", links)
    assert entitlements.process_grant(req, pure).status == "DECLINED"
    revoked = entitlements.process_grant(req, g, [{"type": "revoked", "on": "2026-10-21", "by": "x", "reason": "y"}])
    assert revoked.status == "REVOKED"
    assert entitlements.monitor(30_000, 50_000, [], AS_OF)


# --- products (map 24) -----------------------------------------------------------------------------
def test_product_acceptance_needs_a_recommendation_and_a_decision():
    req = {"security_id": "SYN-LP-02", "description": "Harbor Creek Private Credit LP", "asset_type": "alternative",
           "sponsor": "Harbor Creek Capital", "audited_financials": True, "nominee_registration": True,
           "administrator": "Synthetic Fund Admin", "valuation_source": "Quarterly NAV", "capital_calls": True,
           "aip": True}
    c = products.ProductContext(AS_OF, None, FLAGGED_LIST, ["offering", "subscription", "financials"])
    assert products.process_product(req, products.ProductContext(AS_OF, None, FLAGGED_LIST, [])).status == \
        "PENDING_DOCUMENTS"
    assert products.process_product(req, c).status == "PENDING_PRODUCT_REVIEW"
    ev = [{"type": "recommendation", "recommend": "accept", "by": "x"}]
    assert products.process_product(req, c, {}, ev).status == "PENDING_PRODUCT_DECISION"
    assert products.process_product(req, c, {"product": "accepted"}, ev).status == "PENDING_SETUP"
    done = products.process_product(req, c, {"product": "accepted"}, ev + [{"type": "setup", "by": "x"}])
    assert done.status == "AVAILABLE" and done.effects[0]["type"] == "accept_security"
    flagged = products.checks({**req, "sponsor": "Sable Crest Trading FZE"}, c)
    assert any("flagged list" in b for b in flagged[1])
    assert products.valuation_checks(12.0, AS_OF, 9.62, AS_OF)


# --- lifecycle (maps 16, 17, 22, 23) ----------------------------------------------------------------
def state(**kw):
    base = dict(as_of=AS_OF, account=acct(), balances={STUM: {"settled": 100}, CASH: {"settled": 5_000}},
                prices={STUM: 268.4}, fee={"annual_rate": 0.01, "timing": "in_advance", "period_start": "2026-10-01",
                                          "period_end": "2026-12-31", "value": 31_840})
    base.update(kw)
    return lifecycle.AccountState(**base)


def test_offboarding_restricts_charges_the_final_fee_transfers_and_closes():
    req = {"account_id": "ACC-1", "trigger": "ria_notice"}
    res = balanced(lifecycle.process_offboarding(req, state()))
    assert res.status == "AWAITING_TRANSFER" and [r["code"] for r in res.restrictions] == ["OUTO"]
    fee = [e for e in res.entries if e["ref"] == "final-fee"][0]
    assert fee["kind"] == "advisory_fee_refund"
    ev = [{"type": "fee", "amount": -61.06, "how": "x", "on": "2026-10-21"},
          {"type": "tif", "receiving_firm": "Receiving firm D", "on": "2026-10-21"}]
    res = lifecycle.process_offboarding(req, state(), events=ev)
    assert res.status == "TRANSFERRING" and res.spawn[0]["type"] == "acats_out"
    done = state(balances={}, children={"acats_out": {"status": "COMPLETED", "completed_on": "2026-06-01", "id": 9}})
    res = lifecycle.process_offboarding(req, done, events=ev)
    assert res.status == "CLOSED" and res.effects[0]["type"] == "close_account" and not res.restrictions
    early = state(balances={}, children={"acats_out": {"status": "COMPLETED", "completed_on": "2026-10-20", "id": 9}})
    assert lifecycle.process_offboarding(req, early, events=ev).status == "RESIDUAL_WINDOW"
    residual = state(balances={CASH: {"settled": 12.5}},
                     children={"acats_out": {"status": "COMPLETED", "completed_on": "2026-10-20", "id": 9}})
    assert lifecycle.process_offboarding(req, residual, events=ev).status == "PENDING_RESIDUAL"


def test_offboarding_waits_for_the_owner_of_a_hold():
    res = lifecycle.process_offboarding({"account_id": "ACC-1"}, state(account=acct(codes=["LEGAL"])))
    assert res.status == "PENDING_HOLD_RESOLUTION"


def test_a_deceased_account_locks_down_and_follows_ownership():
    req = {"account_id": "ACC-1", "reported_by": "Family", "date_of_death": "2026-10-10"}
    assert lifecycle.process_deceased(req, state()).status == "AWAITING_CERTIFICATE"
    s = state(documents=["death_certificate"])
    assert lifecycle.process_deceased(req, s).status == "PENDING_DEATH_VERIFICATION"
    ev = [{"type": "death_verified", "verified": True, "by": "x", "on": "2026-10-20"}]
    res = lifecycle.process_deceased(req, s, events=ev)
    assert res.status == "AWAITING_ESTATE_DOCS" and [r["code"] for r in res.restrictions] == ["DECD"]
    joint = state(account=acct(registration_type="joint", holders=[{"name": "Paul Chen"}]),
                  documents=["death_certificate", "claim_form"])
    assert lifecycle.process_deceased(req, joint, events=ev).status == "PENDING_CLAIM_REVIEW"
    ev2 = ev + [{"type": "claim_verified", "claimant": "Paul Chen", "by": "x"},
                {"type": "distribution", "method": "new_account", "account_id": "ACC-2"}]
    res = lifecycle.process_deceased(req, joint, events=ev2)
    assert res.status == "DISTRIBUTING" and res.spawn[0]["type"] == "journal"
    notv = lifecycle.process_deceased(req, s, events=[{"type": "death_verified", "verified": False}])
    assert notv.status == "NOT_VERIFIED" and not notv.restrictions


def test_an_advisor_move_relinks_without_moving_assets():
    s = state(firms={"310003": {"name": "Bonneville Planning LLC", "status": "ACTIVE"},
                     "310001": {"name": "Granite Peak Advisors LLC", "status": "ACTIVE"}})
    req = {"account_id": "ACC-1", "new_firm_crd": "310003", "advisor": "Sam Okafor", "lpoa_signed": True,
           "rep_code": "BP-07"}
    res = balanced(lifecycle.process_advisor_move(req, s))
    assert res.status == "COMPLETED" and res.entries[0]["kind"] == "relink"
    assert res.effects[0] == {"type": "relink", "account_id": "ACC-1", "firm_crd": "310003", "rep_code": "BP-07"}
    assert lifecycle.process_advisor_move({**req, "lpoa_signed": False}, s).status == "PENDING_ITEMS"
    assert lifecycle.process_advisor_move({**req, "dispute": True}, s).status == "PENDING_LEGAL_HOLD"


def test_a_change_of_control_needs_consent():
    firm = {"id": "FIRM-310031", "name": "Timpanogos Wealth Partners LLC", "firm_crd": "310031"}
    s = state(account=firm, firms={"310001": {"name": "Granite", "status": "ACTIVE"}})
    req = {"firm_crd": "310031", "acquirer_crd": "310001", "accounts": ["ACC-1", "ACC-2"],
           "consent_deadline": "2026-11-20", "method": "affirmative"}
    assert lifecycle.process_merger(req, s).status == "PENDING_CLASSIFICATION"
    d = {"merger_classification": "change_of_control"}
    ev = [{"type": "consent", "account_id": "ACC-1", "answer": "consented"},
          {"type": "consent", "account_id": "ACC-2", "answer": "objected"}]
    res = lifecycle.process_merger(req, s, d, ev)
    assert res.status == "COMPLETED"
    assert any(e["type"] == "relink" for e in res.effects) and res.spawn[0]["kind"] == "offboarding"
    assert lifecycle.process_merger(req, s, {"merger_classification": "rebrand"}).status == "RECORDS_UPDATED"


def test_a_joining_rias_book_opens_then_transfers_in_batches():
    firm = {"id": "FIRM-310050", "name": "Wasatch Front Advisors", "firm_crd": "310050", "status": "ACTIVE"}
    app = {"account_id": "ACC-5001"}
    req = {"direction": "join", "batches": [{"n": 1, "accounts": [{"key": "a", "name": "A", "application": app,
                                                                   "positions": [{"security_id": STUM,
                                                                                  "quantity": 5}]}]}]}
    s = state(account=firm)
    assert lifecycle.process_transition(req, s).status == "PLANNING"
    ev = [{"type": "plan_confirmed", "on": "2026-10-20"}]
    res = lifecycle.process_transition(req, s, events=ev)
    assert res.status == "OPENING_ACCOUNTS" and res.spawn[0]["kind"] == "account"
    s.children = {"open:a": {"status": "OPEN", "id": 1}}
    res = lifecycle.process_transition(req, s, events=ev + [{"type": "batch_released", "n": 1}])
    assert res.status == "TRANSFERRING" and res.spawn[0]["kind"] == "transfer"
    s.children["acats:a"] = {"status": "COMPLETED", "id": 2}
    res = lifecycle.process_transition(req, s, events=ev + [{"type": "batch_released", "n": 1}])
    assert res.status == "PENDING_BASIS"
    res = lifecycle.process_transition(req, s, events=ev + [{"type": "batch_released", "n": 1},
                                                            {"type": "basis", "account": "a"}])
    assert res.status == "COMPLETED"


def test_every_release_2_rule_cites_a_source():
    assert all(r["source"] and r["statement"] for r in policy.RULES)
    assert len({r["rule_id"] for r in policy.RULES}) == len(policy.RULES)


def test_an_incoming_position_the_platform_cant_hold_arrives_as_cash():
    prv = {"security_id": "SYN-PRV-01", "description": "Proprietary Core Bond Fund", "asset_type": "mutual_fund",
           "quantity": 1000, "market_value": 10_250.0}
    req = ria("acats_in", positions=[{"security_id": STUM, "description": "Summit Total US Market ETF",
                                      "asset_type": "etf", "quantity": 50}, prv])
    res = run(req)
    assert res.status == "AWAITING_SETTLEMENT", res.status
    legs = [leg for e in res.entries for leg in e["legs"] if leg.layer == "client"]
    assert {leg.asset for leg in legs} == {STUM, CASH}                      # no shares of the proprietary fund
    assert sum(leg.units for leg in legs if leg.asset == CASH) == units(10_250)
