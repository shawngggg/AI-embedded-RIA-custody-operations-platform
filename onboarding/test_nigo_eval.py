"""The NIGO detection test set is checked before any model sees it: a careful reader's answers must produce exactly
the seeded defects, and every answer must pass the controls gate."""
import pytest

from extraction import gate
from nigo_eval import NIGO_RULES, PACKAGES, answer_key, package_text, run


@pytest.mark.parametrize("p", PACKAGES, ids=[p.key for p in PACKAGES])
def test_the_answer_key_finds_exactly_the_seeded_defects(p):
    text = package_text(p)
    result = gate(answer_key(p), text, "end_turn", "answer key")
    assert result.rejected == [], result.rejected            # every answer is grounded in the package
    from nigo_eval import flagged
    assert flagged(result.application) == set(p.seeded)


def test_the_set_covers_every_document_nigo_with_clean_packages_and_traps():
    seeded = [r for p in PACKAGES for r in p.seeded]
    assert set(seeded) == set(NIGO_RULES)                     # every document defect the rules can raise
    assert sum(1 for p in PACKAGES if not p.seeded) >= 4      # clean packages, to count false NIGOs
    assert sum(1 for p in PACKAGES if len(p.seeded) > 1) >= 2
    traps = [p for p in PACKAGES if p.formation == "to_follow" or p.chart == "listed_not_attached"
             or p.bo_cert == "to_follow" or p.agreement == "to_be_signed"]
    assert len(traps) == 4
    assert len({p.key for p in PACKAGES}) == len(PACKAGES) and len({p.name for p in PACKAGES}) == len(PACKAGES)


def test_scoring_counts_misses_and_false_nigos():
    # A reader that returns nothing flags nearly everything (a P.O. box reads as a missing address instead), and puts
    # a false NIGO on every clean package: why false NIGOs are reported beside recall
    blind = run(lambda text: gate({"fields": [], "people": [], "notes": ""}, text, "end_turn", "blind"))
    assert blind["missed"] == 2 and blind["clean_packages_flagged"] == 4 and blind["false_nigo"] > 20
    perfect = run(lambda text: gate(answer_key(next(p for p in PACKAGES if package_text(p) == text)), text, "end_turn",
                                    "answer key"))
    assert perfect["recall"] == 1.0 and perfect["false_nigo"] == 0 and perfect["met"]
    assert perfect["seeded"] == 19 and perfect["packages"] == 20


def test_a_miss_shows_the_models_evidence():
    # The live run's one miss (Oct. 20): the model took the contents line for the ownership chart itself
    p = next(p for p in PACKAGES if p.key == "chart-listed")
    fooled = answer_key(p)
    fooled["fields"].append({"name": "ownership_chart", "value": "true", "quote": "ownership chart (attached)"})
    out = run(lambda text: gate(fooled, text, "end_turn", "fooled"), [p])
    row = out["rows"][0]
    assert row["missed"] == ["KYB-002"] and out["recall"] == 0.0
    assert row["evidence"] == {"KYB-002": [{"field": "ownership_chart", "value": "true",
                                            "quote": "ownership chart (attached)"}]}


def test_the_prompt_says_a_mention_is_not_the_document():
    from extraction import SYSTEM
    assert "table of contents" in SYSTEM and "never from a mention of it" in SYSTEM


def test_a_failed_model_call_counts_as_missed():
    def broken(text):
        raise RuntimeError("timeout")
    out = run(broken, PACKAGES[:6])
    assert out["caught"] == 0 and out["missed"] == out["seeded"] and all(r["error"] for r in out["rows"])
