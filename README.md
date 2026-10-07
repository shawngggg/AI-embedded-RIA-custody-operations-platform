# RIA Custody Operations Platform: Process Architecture

**Live viewer:** https://shawngggg.github.io/AI-embedded-RIA-custody-operations-platform/

A BPMN 2.0 process architecture for an AI-embedded RIA custody operations platform, covering the full account lifecycle from RIA firm onboarding through offboarding, deceased-client processing, and escheatment.

This is **phase one** of a larger build: map every process correctly and completely, then build the platform module by module, starting with onboarding.

![Platform overview](docs/diagrams/00-platform-overview.png)

## Design principles

- **AI proposes; rules and people decide; the ledger records.** AI handles unstructured inputs and exceptions, such as document extraction, NIGO categorization, address matching, and break triage. Every decision passes a deterministic rules check or a human approval, and nothing AI produces posts to the ledger directly.
- **One three-layer ledger.** Client accounts roll up to the RIA master account, and RIA masters roll up to the custodian general ledger.
- **First line and second line stay separate.** Operations escalates; compliance and the sanctions team decide. Restriction codes can be removed only by the function that owns them.
- **The RIA is the channel and the decision-maker.** Account changes, money movement, and asset transfers come through the RIA, which also owns account entitlements and client and product suitability. The custodian provides the platform and holds the assets; it enforces what the RIA sets and never makes a suitability judgment.
- **Two account groups, no dual management.** A client has one or more pure RIA accounts managed by the RIA, and may have one designated self-directed account as an added benefit. The client trades and deposits there freely; outgoing money movement and transfers need an RIA grant. The RIA has view-only access and decides whether to bill it, and keeping client-directed trading in its own account keeps the RIA's fiduciary scope unambiguous.
- **Shared subprocesses instead of repeated steps.** Sanctions escalation and the transferability review are modeled once and called from onboarding, maintenance, transfers, and offboarding.
- **Built to connect.** The design assumes adapters for clearing (NSCC, DTC, OCC), payment rails, KYC and screening services, regulators, transfer agents, and AI model providers.

## Process inventory and review status

Each process is labeled by how far it has been validated:

| Process | Status |
|---|---|
| Platform overview | Overview |
| RIA firm onboarding | Validated |
| Client account opening (paths A and B) | Validated |
| Rolling review | Validated |
| Account maintenance | Validated |
| Client entitlement management | Proposed enhancement |
| Sanctions escalation (shared) | Validated |
| Transferability review (shared) | Validated |
| Asset and money transfers | Partially validated |
| Trade processing | In review |
| Corporate actions | In review |
| Capital calls and distributions | In review |
| Cash settlement | In review |
| Position reconciliation | In review |
| Advisor billing | In review |
| Client reporting | In review |
| Client offboarding | Validated |
| Deceased client account | Validated |
| Escheatment | In review |

- **Validated:** corrected against my operating experience.
- **Partially validated:** key steps validated; the rest is reference design.
- **In review:** reference design from industry practice, awaiting validation.
- **Proposed enhancement:** a platform capability beyond standard practice.

Still to be mapped: RIA and advisor transitions, financial crimes and SAR filing, product acceptance, regulatory reporting, and retirement account servicing.

## Repository contents

| Path | Contents |
|---|---|
| `docs/index.html` | Interactive viewer (served by GitHub Pages) |
| `docs/diagrams/` | PNG image of every process |
| `bpmn/` | BPMN 2.0 XML files, editable in Camunda Modeler, bpmn.io, or Signavio |

## Roadmap

1. Finish mapping and validating every process.
2. Build the onboarding module in Python, extending my [onboarding execution engine](https://github.com/shawngggg/onboarding-execution-engine) (policy-as-code KYC/AML rules).
3. Add the remaining modules, then a demo console.

## Notes

- **Reference architecture.** Process content reflects industry practice and my operating experience in RIA custody operations. It does not depict any specific firm's internal procedures, systems, or data. All data and parties are synthetic.
- **How it was made.** The process content comes from my own review and corrections. The BPMN diagrams were produced with AI assistance (Claude).
- **Third-party software.** The viewer embeds [bpmn-js](https://bpmn.io), licensed under the bpmn.io license; see `docs/BPMN-JS-LICENSE.txt`. Its watermark must remain visible.

## Author

**Shawn Ghodrati**, operations excellence, process improvement, and AI automation in banking, payments, and investment operations.
[LinkedIn](https://www.linkedin.com/in/shawngh) · [GitHub](https://github.com/shawngggg)
