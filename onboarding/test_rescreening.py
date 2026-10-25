"""Ongoing screening: a client clear at opening matches after a list update, and the sanctions team decides."""
from datetime import date

from flagged_list import FlaggedEntry, Kind, Subject
from pipeline import Decisions
from rescreening import Party, Population, alert_package, find_alerts, hit_key, process_rescreen
from sample_data import FLAGGED_LIST

AS_OF = date(2026, 10, 26)
NEW = FlaggedEntry("FL-101", Kind.INDIVIDUAL, "Gregor Halvorsen", ("G. Halvorsen",), "1958-03-11",
                   program="Synthetic program D")


def population():
    holder = Subject("ACC-3005:holder:0", Kind.INDIVIDUAL, "Gregor Halvorsen", "account_holder", "1958-03-11")
    other = Subject("ACC-3006:holder:0", Kind.INDIVIDUAL, "Samuel Adeyemi", "account_holder", "1980-02-02")
    payee = Subject("ACC-3009:sloa:0", Kind.ENTITY, "Alpine Ridge School", "standing payee")
    return Population([Party("ACC-3005", "account", "Gregor Halvorsen", holder, "account holder"),
                       Party("ACC-3006", "account", "Samuel Adeyemi", other, "account holder"),
                       Party("ACC-3009", "account", "Benson Family Trust", payee, "standing payee")])


def test_a_client_clear_at_opening_matches_after_the_list_changes():
    assert find_alerts(population(), FLAGGED_LIST, AS_OF, set()) == {}
    alerts = find_alerts(population(), FLAGGED_LIST + [NEW], AS_OF, set())
    assert list(alerts) == ["ACC-3005"] and alerts["ACC-3005"][0].entry_id == "FL-101"


def test_a_cleared_match_isnt_raised_again_but_a_new_entry_is():
    hit = find_alerts(population(), FLAGGED_LIST + [NEW], AS_OF, set())["ACC-3005"][0]
    cleared = {hit_key("ACC-3005", hit)}
    assert find_alerts(population(), FLAGGED_LIST + [NEW], AS_OF, cleared) == {}
    alias = FlaggedEntry("FL-102", Kind.INDIVIDUAL, "Gregor Halvorsen", program="Synthetic program E")
    assert list(find_alerts(population(), FLAGGED_LIST + [NEW, alias], AS_OF, cleared)) == ["ACC-3005"]


def _alert(flagged):
    pop = population()
    parties = pop.targets()["ACC-3005"]
    hits = find_alerts(pop, flagged, AS_OF, set())["ACC-3005"]
    return alert_package(parties[0], hits, "list_change", len(flagged), parties)


def test_a_new_match_holds_the_account_until_the_sanctions_team_decides():
    flagged = FLAGGED_LIST + [NEW]
    res = process_rescreen(_alert(flagged), flagged, AS_OF)
    assert res.status == "PENDING_SANCTIONS" and [r["code"] for r in res.restrictions] == ["SANC"]
    cleared = process_rescreen(_alert(flagged), flagged, AS_OF,
                               Decisions(sanctions="false_positive", sanctions_reference="DET-1"))
    assert cleared.status == "CLEARED" and cleared.restrictions == []
    blocked = process_rescreen(_alert(flagged), flagged, AS_OF,
                               Decisions(sanctions="true_match", sanctions_reference="DET-2"))
    assert blocked.status == "BLOCKED" and [r["code"] for r in blocked.restrictions] == ["OFAC"]


def test_an_entry_removed_before_the_decision_closes_the_alert():
    alert = _alert(FLAGGED_LIST + [NEW])
    assert process_rescreen(alert, FLAGGED_LIST, AS_OF).status == "CLEARED"
