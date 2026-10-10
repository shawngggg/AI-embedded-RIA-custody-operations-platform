# Onboarding module

RIA firm onboarding and client account opening for the RIA custody operations platform, built along process maps [01 (RIA firm onboarding)](../docs/diagrams/01-ria-firm-onboarding.png) and [02 (client account opening)](../docs/diagrams/02-client-account-opening.png). All data is synthetic.

## Design

- **AI proposes; rules and people decide; the ledger records.** The AI step reads documents. A deterministic gate decides what it may contribute, the rules decide outcomes, and people make the decisions the maps give them: compliance sign-off, the sanctions determination, and EDD approval.
- **Rules are data.** Every rule has an id, a version, an effective date, an optional expiry date, and a cited source. The engine applies the versions in force on the evaluation date, so a past decision can be reproduced.
- **One complete request.** Firm and account rules collect every finding, so the RIA gets all missing items in a single request.
- **First line escalates; the second line decides.** A flagged-list match places a sanctions hold that only the sanctions team can release.
- **Defensive by default.** A rule that names an unknown field or operator is skipped with a warning; it never crashes a run.

## Files

| File | Milestone | Contents |
|---|---|---|
| `models.py` | 1 | Account groups, paths, the RIA firm and account records, reliance eligibility, the path decision, and the account-opening gate (written by Shawn Ghodrati) |
| `rules.py` | 2 | The generic rules engine: dispositions, findings, effective dating, operators, library checks |
| `applications.py` | 2 | Application records for firms, people, accounts, and positions |
| `firm_rules.py` | 2 | Registration and licensing, KYB, and CIP rules for RIA firms; the reliance determination |
| `account_rules.py` | 2 | Account rules: in good order, minimum CIP information, Path B CDD, and beneficial ownership before and after FinCEN's Feb. 13, 2026 relief |
| `restrictions.py` | 3 | Restriction code catalog, owner-only removal, authorization references, audit trail |
| `flagged_list.py` | 4 | Name normalization and matching, identifiers, sanctioned jurisdictions by date, the 50 Percent Rule, escalation and determinations |
| `risk.py` | 5 | Firm and account risk factors, FATF lists by date, tiers, EDD requirements, review dates |
| `transferability.py` | 6 | Security master and per-position transfer routes; full, partial, or held release |
| `extraction.py` | 7 | Claude structured-output extraction and the controls gate (grounding check) |
| `pipeline.py` | All | Firm onboarding and account opening in map order, with a step trace and audit record |
| `sample_data.py` | All | Synthetic firms, accounts, flagged list, and an application package |
| `demo.py` | All | Command-line demo and the browser console's entry point |
| `test_*.py` | All | 177 tests |

## Run it

From the repo root:

```
pip install -r requirements.txt
python -m pytest onboarding -v
python onboarding/demo.py
```

Live AI extraction needs an `ANTHROPIC_API_KEY`. The model defaults to `claude-haiku-5-5`; set `RIA_EXTRACTION_MODEL` to change it.

From inside `onboarding/`:

```python
from datetime import date
from pipeline import onboard_firm_from_package
result = onboard_firm_from_package(open("package.txt").read(), date.today())
print(result.status, result.requested_items)
```

## Browser console

`docs/console/` serves copies of these modules to the browser. After changing a module, run `python tools/sync_console.py`; `test_console_sync.py` fails if the copies drift.

## Authorship

Shawn Ghodrati wrote Milestone 1. Milestones 2 to 8 were written by Claude (Anthropic's AI model) to the requirements in Shawn's process maps. The rules and citations are a reference design, not legal advice.
