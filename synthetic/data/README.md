# Synthetic data

Everything here is invented: firms, people, tax IDs (900-00-xxxx, a range no one is issued), accounts,
securities, prices, banks, and amounts. It's what the demo seeds and shows, written out by
`python -m synthetic.export` on business day 2026-10-25.

Three sources, and they agree with each other:

- **Onboarding and CIP** come from the seeded cases (`synthetic/cases.py` and `app/seed.py`).
- **The books** come from the release 2 ledger (`app/seed_r2.py`): each open account's opening balance or
  incoming ACATS, then every transfer and money movement case the seed runs through the workflows. Every
  posting has legs at three layers, and in each one, asset by asset: the client legs equal the RIA master
  legs, the master legs equal minus the client control accounts (GL 2100 cash, 2500 securities), and the
  general ledger legs sum to zero. All 105 postings here pass all three. Positions, balances, and
  `ledger.csv` are what those postings add up to.
- **Trading and billing** are the release 3 previews (`synthetic/book.py`), built on the ledger's positions:
  an account with a DOCS code or an outgoing transfer trades nothing, and fees bill on ledger values.

| File | Rows | What it is |
| --- | ---: | --- |
| `cases.csv` | 110 | Every case the seed creates (onboarding, accounts, changes, transfers, money movement): status and queue |
| `people.csv` | 66 | CIP: each person on a firm or account application, with what was screened and verified |
| `restrictions.csv` | 12 | Active restriction codes and why they were placed |
| `accounts.csv` | 31 | The open accounts: RIA, registration, restriction codes, and the ledger's cash and value |
| `securities.csv` | 28 | The security master: synthetic CUSIP-format IDs with valid check digits, prices, where held, and whether the platform accepts each one |
| `positions.csv` | 256 | Settled positions by account, from the ledger |
| `balances.csv` | 313 | Every client account's balance by asset and bucket (settled, pending, restricted) |
| `masters.csv` | 3 | Each RIA master account: cash by bucket and securities value; it equals the sum of its clients |
| `ledger.csv` | 13 | The general ledger: cash and securities value by chart account |
| `ledger_checks.csv` | 3 | The three-layer checks: clients equal their RIA master; masters equal the GL; the GL balances |
| `ledger_entries.csv` | 105 | Every posting, with the case or receipt behind it |
| `ledger_legs.csv` | 2423 | Each posting's legs at the client, RIA master, and general ledger layers (they sum to zero) |
| `transfers.csv` | 46 | Every transfer and money movement case: wires, ACH, journals, IRA distributions, ACATS, LOI |
| `transfer_positions.csv` | 145 | Each ACATS or LOI case's positions and the route the transferability rules gave them |
| `bank_links.csv` | 31 | Verified bank accounts in the client's name (money moves only to or from these) |
| `receipts.csv` | 4 | Money that arrived at the settlement bank with no request behind it, and what happened to it |
| `settlement_flows.csv` | 2 | Today's cash at the settlement bank, netted by channel |
| `recon_breaks.csv` | 5 | Books against the street (DTC, transfer agents, sponsors, the bank): each break, its cause, and how it was resolved |
| `orders.csv` | 20 | The trading blotter for yesterday and today: block orders, fund orders, self-directed orders |
| `allocations.csv` | 72 | Each order's allocations to accounts at one average price |
| `fails.csv` | 3 | Open settlement fails, with Reg SHO close-out dates where they apply |
| `billing_runs.csv` | 4 | Fee runs by RIA: schedule, period, status, totals |
| `fees.csv` | 24 | Fees by account in each run, with exceptions |
| `book.json` |  | The release 3 book in one file, as the preview API serves it to operations |

## Assumptions

- Every firm, client, security, price, bank, and amount is synthetic.
- Business days skip weekends and the fixed-date market holidays (New Year's Day, Juneteenth, Independence Day, Christmas), without weekend observance or the floating holidays.
- ACATS timing follows FINRA Rule 11870: the delivering firm validates within 1 business day, then the transfer completes within 3 business days.
- Equities, ETFs, and bonds settle T+1 through NSCC and DTC; mutual funds settle T+1 through Fund/SERV.
- A sponsor re-registers non-traded REIT shares within about 40 business days of the LOI, and operations follows up after 20 (assumptions for the demo; sponsors vary).
- Fee schedules, the custody fee (0.04% a year, $10 a quarter minimum), and the $100,000 callback threshold are illustrative, not any custodian's terms.
