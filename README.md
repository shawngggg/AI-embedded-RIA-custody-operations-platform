# RIA Custody Operations Platform: Process Architecture

**Live viewer:** https://shawngggg.github.io/AI-embedded-RIA-custody-operations-platform/

A BPMN 2.0 process architecture for an AI-embedded RIA custody operations platform: 25 detailed processes covering the full account lifecycle, from RIA firm onboarding and product acceptance through trading, settlement, billing, reporting, retirement servicing, transitions, offboarding, escheatment, and regulatory filings.

**Phase one**, mapping every process, is complete. **Phase two**, building the platform module by module, has started with onboarding.

![Platform overview](docs/diagrams/00-platform-overview.png)

## Integration architecture

Every request enters through the platform APIs, every outbound instruction or filing passes the rules engine and a person's approval before the integration layer sends it, and AI reaches models only through its own gateway. The integration layer has one adapter per external system: clearing and depositories (NSCC, DTC, OCC, DTCC CTM), payment rails (Fedwire, FedACH, FedNow, RTP, SWIFT), regulators and tax (FinCEN BSA E-Filing, CAT, TRACE, MSRB RTRS, Electronic Blue Sheets, IRS IRIS, state unclaimed property), due diligence services, transfer agents and fund administrators, and advisor technology. The demo runs simulators that use the public message formats.

![Integration architecture](docs/diagrams/integration-architecture.png)

## Design principles

- **AI proposes; rules and people decide; the ledger records.** AI handles unstructured inputs and exceptions, such as document extraction, NIGO categorization, address matching, and break triage. Every decision passes a deterministic rules check or a human approval, and nothing AI produces posts to the ledger directly.
- **One three-layer ledger.** Client accounts roll up to the RIA master account, and RIA masters roll up to the custodian general ledger.
- **First line and second line stay separate.** Operations escalates; compliance and the sanctions team decide. Restriction codes can be removed only by the function that owns them.
- **The RIA is the channel and makes the investment decisions.** Account changes, money movement, and asset transfers come through the RIA, which owns account entitlements and client and product suitability; owner-level changes, such as beneficiaries and registration, carry the client's signature. The custodian provides the platform and holds the assets. It makes no investment judgment and decides only what its own obligations require: options and margin approval, AML and sanctions, fraud holds, legal process, and which assets it will hold.
- **Two account groups, no dual management.** A client has one or more pure RIA accounts managed by the RIA, and may have one designated self-directed account as an added benefit. The client trades and deposits there freely. An RIA grant turns on self-service outgoing money movement and transfers, and the client can always move assets out through a signed request or a receiving firm's ACATS transfer. The RIA has view-only access and decides whether to bill it, and keeping client-directed trading in its own account keeps the RIA's fiduciary scope unambiguous.
- **Validated where experienced, researched where not.** Steps I performed are validated against my experience. Steps I didn't perform are built from current US regulation and industry practice and labeled as reference design, and my own positions were checked against the rules.
- **Shared subprocesses instead of repeated steps.** Sanctions escalation and the transferability review are modeled once and called from onboarding, maintenance, transfers, and offboarding.
- **Built to connect.** The design assumes adapters for clearing (NSCC, DTC, OCC), payment rails, KYC and screening services, regulators, transfer agents, and AI model providers.

## Process inventory and review status

Each process is labeled by how far it has been validated:

| Process | Status |
|---|---|
| Platform overview | Overview |
| Integration architecture | Overview |
| RIA firm onboarding | Validated |
| Client account opening (paths A and B) | Validated |
| Product acceptance | Partially validated |
| Rolling review | Validated |
| Account maintenance | Validated |
| Client entitlement management | Proposed enhancement |
| Retirement account servicing | Partially validated |
| Sanctions escalation (shared) | Validated |
| Transferability review (shared) | Validated |
| Asset and money transfers | Partially validated |
| Trade processing | Partially validated |
| Corporate actions | Partially validated |
| Capital calls and distributions | In review |
| Cash settlement | Validated |
| Position reconciliation | Partially validated |
| Advisor billing | Validated |
| Client reporting | Validated |
| Client offboarding | Validated |
| Deceased client account | Partially validated |
| RIA firm transitions (joining or leaving with a book) | Partially validated |
| Advisor moves and RIA mergers | In review |
| Escheatment | Validated |
| Financial crimes and SAR filing | Partially validated |
| Regulatory reporting | Partially validated |
| Tax reporting | Partially validated |

- **Validated:** corrected against my operating experience.
- **Partially validated:** key steps validated; the rest is reference design built from regulation and industry practice.
- **In review:** reference design built from regulation and industry practice, awaiting validation.
- **Proposed enhancement:** a platform capability beyond standard practice.

Every process is now mapped: 11 are validated, 11 partially validated, 2 in review, and 1 is a proposed enhancement.

## Onboarding module (in progress)

`onboarding/models.py` holds the account data model and the first two decisions:

- **Due-diligence path.** A pure RIA account takes Path A, where the custodian relies on the RIA for customer identification, only when the RIA is SEC-registered, has a reliance contract, and has certified within the last year. Every other account, including the self-directed account, takes Path B, where the custodian runs its own checks.
- **Account opening.** An inactive RIA can't open accounts, and a client can add one self-directed account only alongside an open RIA-managed account.

To run the tests from the repo root: `pip install pytest`, then `python -m pytest onboarding -v`.

## Repository contents

| Path | Contents |
|---|---|
| `docs/index.html` | Interactive viewer (served by GitHub Pages) |
| `docs/diagrams/` | PNG image of every process |
| `bpmn/` | BPMN 2.0 XML files, editable in Camunda Modeler, bpmn.io, or Signavio |
| `onboarding/` | Onboarding module code and its tests |

## Roadmap

1. Map every process (done) and validate the two maps still in review.
2. Build the onboarding module in Python, extending my [onboarding execution engine](https://github.com/shawngggg/onboarding-execution-engine) (policy-as-code KYC/AML rules). Milestone 1 of 8, the data model and due-diligence path decision, is done.
3. Add the remaining modules, then a demo console.

## Notes

- **Reference architecture.** Process content reflects my operating experience in RIA custody and brokerage operations, current US regulation, and industry practice. It does not depict any specific firm's internal procedures, systems, or data. All data and parties are synthetic.
- **How it was made.** The process content comes from my own review and corrections. The BPMN diagrams were produced with AI assistance (Claude). In the onboarding module, I wrote Milestone 1 (`onboarding/models.py`); its specification and tests were written with AI assistance.
- **Third-party software.** The viewer embeds [bpmn-js](https://bpmn.io), licensed under the bpmn.io license; see `docs/BPMN-JS-LICENSE.txt`. Its watermark must remain visible.

## Author

**Shawn Ghodrati**, operations excellence, process improvement, and AI automation in banking, payments, and investment operations.
[LinkedIn](https://www.linkedin.com/in/shawngh) · [GitHub](https://github.com/shawngggg)
