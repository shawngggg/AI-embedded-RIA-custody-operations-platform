# Synthetic data

Everything here is invented: firms, people, tax IDs (900-00-xxxx, a range no one is issued), accounts,
securities, prices, banks, and amounts. It's what the demo seeds and shows, written out by
`python -m synthetic.export` on business day 2026-10-22.

The onboarding and CIP files come from the seeded cases (`synthetic/cases.py` and `app/seed.py`). The rest is
the book (`synthetic/book.py`), built from the accounts those cases opened, so the files agree with each other:
a DOCS account trades nothing and can't be debited for fees, every buy fail is a reconciliation break, and the
general ledger is off by exactly the one open cash break.

| File | Rows | What it is |
| --- | ---: | --- |
| `accounts.csv` | 31 | The open accounts the book is built from: RIA, registration, model, restriction codes, cash, value |
| `securities.csv` | 22 | The synthetic security master: CUSIP-format IDs with valid check digits, prices, where held |
| `positions.csv` | 256 | Positions by account |
| `orders.csv` | 20 | The trading blotter for yesterday and today: block orders, fund orders, self-directed orders |
| `allocations.csv` | 72 | Each order's allocations to accounts at one average price |
| `fails.csv` | 3 | Open settlement fails, with Reg SHO close-out dates where they apply |
| `money_movements.csv` | 46 | ACH, wires, journals, IRA distributions, returns, holds, and an unapplied wire |
| `transfers.csv` | 16 | Incoming and outgoing ACATS transfers and LOI re-registrations |
| `transfer_positions.csv` | 139 | Each transfer's positions and the route the transferability rules gave them |
| `billing_runs.csv` | 4 | Fee runs by RIA: schedule, period, status, totals |
| `fees.csv` | 24 | Fees by account in each run, with exceptions |
| `settlement_flows.csv` | 4 | Today's settlement netted by channel |
| `ledger.csv` | 6 | General ledger balances |
| `ledger_checks.csv` | 3 | The three-layer checks: client accounts, RIA master accounts, general ledger |
| `recon_breaks.csv` | 7 | Reconciliation breaks: books against the street, with cause, owner, and next action |
| `cases.csv` | 63 | Every onboarding, account, and change case the seed creates, with its status and queue |
| `people.csv` | 66 | CIP: each person on a firm or account application, with what was screened and verified |
| `restrictions.csv` | 12 | Active restriction codes and why they were placed |
| `book.json` |  | The whole book in one file, as the preview API serves it to operations |

## Assumptions

- Every firm, client, security, price, bank, and amount is synthetic.
- Business days skip weekends and the fixed-date market holidays (New Year's Day, Juneteenth, Independence Day, Christmas), without weekend observance or the floating holidays.
- ACATS timing follows FINRA Rule 11870: the delivering firm validates within 1 business day, then the transfer completes within 3 business days.
- Equities, ETFs, and bonds settle T+1 through NSCC and DTC; mutual funds settle T+1 through Fund/SERV.
- A sponsor re-registers non-traded REIT shares within about 40 business days of the LOI, and operations follows up after 20 (assumptions for the demo; sponsors vary).
- Fee schedules, the custody fee (0.04% a year, $10 a quarter minimum), and the $100,000 callback threshold are illustrative, not any custodian's terms.
