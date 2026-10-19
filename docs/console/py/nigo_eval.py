"""
The NIGO detection test set (MVP gate metric: NIGO detection recall; PRD guardrail 5, pre-release testing).

Synthetic RIA firm application packages, each with known defects seeded in. AI reads a package through the
controls gate (extraction.py) and the firm rules then flag what isn't in good order. The test compares what the
pipeline flags with what was seeded:

  recall      share of seeded defects the pipeline flags (target: 90% or higher)
  false NIGO  items flagged missing that are in the package (the AI missed something that's there)

Both are reported, because an AI that read nothing would flag everything and score 100% recall.

The set covers every document defect the firm rules can raise, traps that test whether the AI is fooled (a section
listed in the contents but not attached, a certificate "to follow", an agreement "to be signed at account opening",
a blank signature block), packages with several defects, and clean packages. Each package carries an answer key: the
extraction a careful reader would return. The engine tests run every answer key through the gate and the rules, so
the test set itself is checked before any live model sees it.

Identity verification (KYB-005) is left out: a person verifies identity, never the model, so it is flagged on every
new firm.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from firm_rules import evaluate_firm

AS_OF = date(2026, 10, 13)

# The firm rules that raise a document NIGO (operations requests an item); KYB-005 is a person's job, not the model's
NIGO_RULES = {
    "REG-003": "SEC file number missing",
    "REG-004": "CRD number missing",
    "KYB-001": "Formation documents missing",
    "KYB-002": "Ownership chart missing",
    "KYB-003": "Beneficial ownership certification missing",
    "KYB-004": "No control person identified",
    "KYB-006": "Custodial services agreement not signed",
    "CIP-F01": "EIN missing",
    "CIP-F02": "Principal place of business missing",
    "CIP-F03": "Principal place of business is a P.O. box",
}
TARGET_RECALL = 0.90

ENTITY = {"LLC": ("limited liability company", "CERTIFICATE OF FORMATION (SYNTHETIC)", "filed with the {state} "
                  "Division of Corporations on"),
          "corporation": ("corporation", "ARTICLES OF INCORPORATION (SYNTHETIC)", "incorporated in {state} on"),
          "partnership": ("limited partnership", "CERTIFICATE OF LIMITED PARTNERSHIP (SYNTHETIC)",
                          "formed in {state} on")}


@dataclass(frozen=True)
class Owner:
    name: str
    title: str
    pct: int
    control: bool = False


@dataclass(frozen=True)
class Package:
    """One synthetic firm package. Switches leave a section out or turn it into a trap; `seeded` is the answer."""
    key: str
    about: str
    name: str
    crd: str | None
    sec_file: str | None
    address: str | None
    ein: str | None
    owners: tuple[Owner, ...]
    entity: str = "LLC"
    state: str = "Utah"
    formed: str = "June 3, 2014"
    raum: int = 640_000_000
    formation: str = "attached"          # attached | missing | to_follow
    chart: str = "attached"              # attached | missing | listed_not_attached
    bo_cert: str = "attached"            # attached | missing | to_follow
    agreement: str = "signed"            # signed | blank_signature | to_be_signed
    control_named: bool = True
    seeded: frozenset[str] = field(default_factory=frozenset)


def _control(p: Package) -> Owner | None:
    return next((o for o in p.owners if o.control), None) if p.control_named else None


def package_text(p: Package) -> str:
    """The package as a firm would send it: Form ADV excerpt, formation, EIN notice, chart, certification, agreement."""
    noun, heading, filed = ENTITY[p.entity]
    parts = []
    if p.chart == "listed_not_attached":
        parts.append("PACKAGE CONTENTS: Form ADV excerpt; formation document; IRS EIN notice; ownership chart "
                     "(attached); beneficial ownership certification; custodial services agreement signature page.")
    parts.append("\n".join([
        "FORM ADV PART 1A (EXCERPT, SYNTHETIC)",
        f"Item 1.A Full legal name: {p.name}",
        f"Item 1.D SEC file number: {p.sec_file or ''}".rstrip(),
        f"Item 1.E CRD number: {p.crd or ''}".rstrip(),
        f"Item 1.F Principal office and place of business: {p.address or ''}".rstrip(),
        "Item 2.A The adviser is registered with the U.S. Securities and Exchange Commission.",
        "Item 3.B Fiscal year end: December",
        f"Item 5.F Regulatory assets under management: ${p.raum:,}",
        "Item 11 Disciplinary information: No",
        "Annual updating amendment filed: March 18, 2026",
    ]))
    if p.formation == "attached":
        parts.append(f"{heading}\n{p.name}, a {p.state} {noun}, {filed.format(state=p.state)} {p.formed}.")
    elif p.formation == "to_follow":
        parts.append(f"FORMATION DOCUMENT\nThe firm is refiling its {heading.split(' (')[0].lower()} with the state "
                     "and will send a copy when the state returns it.")
    if p.ein:
        parts.append(f"IRS EIN ASSIGNMENT NOTICE (SYNTHETIC)\nThe IRS assigned employer identification number {p.ein} "
                     f"to {p.name}.")
    if p.chart == "attached":
        parts.append("OWNERSHIP CHART (SYNTHETIC)\n" + "\n".join(f"{o.name}, {o.title}: {o.pct}%" for o in p.owners))
    control = _control(p)
    if p.bo_cert == "attached":
        lines = ["CERTIFICATION REGARDING BENEFICIAL OWNERS (SYNTHETIC)",
                 f"{p.name} certifies that each individual who owns 25% or more of its equity is listed here: "
                 + "; ".join(f"{o.name} ({o.pct}%)" for o in p.owners if o.pct >= 25) + "."]
        lines.append(f"Control person: {control.name}, {control.title}, has significant responsibility to manage the "
                     "firm." if control else "Control person: not designated.")
        parts.append("\n".join(lines))
    elif p.bo_cert == "to_follow":
        parts.append("BENEFICIAL OWNERSHIP\nThe beneficial ownership certification will follow under separate cover.")
    signer = control or p.owners[0]
    if p.agreement == "signed":
        parts.append(f"CUSTODIAL SERVICES AGREEMENT, SIGNATURE PAGE (SYNTHETIC)\nSigned for {p.name} by {signer.name}, "
                     f"{signer.title}, on September 28, 2026.")
    elif p.agreement == "blank_signature":
        parts.append(f"CUSTODIAL SERVICES AGREEMENT, SIGNATURE PAGE (SYNTHETIC)\nFor {p.name}: Signature ________ "
                     "Name ________ Title ________ Date ________")
    else:
        parts.append("CUSTODIAL SERVICES AGREEMENT (SYNTHETIC)\nThe firm has reviewed the agreement and will sign it "
                     "at account opening.")
    return "\n\n".join(parts) + "\n"


def answer_key(p: Package) -> dict:
    """The extraction a careful reader returns, in the model's output schema."""
    noun, heading, filed = ENTITY[p.entity]
    f = [("legal_name", p.name, f"Item 1.A Full legal name: {p.name}"),
         ("registration", "SEC", "The adviser is registered with the U.S. Securities and Exchange Commission."),
         ("fiscal_year_end_month", "12", "Item 3.B Fiscal year end: December"),
         ("raum", str(p.raum), f"Item 5.F Regulatory assets under management: ${p.raum:,}"),
         ("disciplinary_disclosures", "false", "Item 11 Disciplinary information: No"),
         ("adv_last_annual_amendment", "2026-03-18", "Annual updating amendment filed: March 18, 2026")]
    if p.sec_file:
        f.append(("sec_file_number", p.sec_file, f"Item 1.D SEC file number: {p.sec_file}"))
    if p.crd:
        f.append(("crd_number", p.crd, f"Item 1.E CRD number: {p.crd}"))
    if p.address:
        f.append(("principal_address", p.address, f"Item 1.F Principal office and place of business: {p.address}"))
    if p.formation == "attached":
        f += [("formation_documents", "true", heading), ("entity_type", p.entity, f"a {p.state} {noun}")]
    if p.ein:
        f.append(("ein", p.ein, f"employer identification number {p.ein}"))
    if p.chart == "attached":
        f.append(("ownership_chart", "true", "OWNERSHIP CHART (SYNTHETIC)"))
    if p.bo_cert == "attached":
        f.append(("beneficial_ownership_certification", "true", "CERTIFICATION REGARDING BENEFICIAL OWNERS (SYNTHETIC)"))
    signer = _control(p) or p.owners[0]
    if p.agreement == "signed":
        f.append(("custodial_agreement_signed", "true", f"Signed for {p.name} by {signer.name}"))
    people = []
    control = _control(p)
    for o in p.owners:
        quote = f"{o.name}, {o.title}: {o.pct}%" if p.chart == "attached" else f"{o.name} ({o.pct}%)"
        if p.chart != "attached" and p.bo_cert != "attached":
            continue                       # nowhere in the package names the owners
        people.append({"name": o.name, "role": "control_person" if o is control else "owner",
                       "ownership_pct": str(o.pct), "quote": quote})
    return {"fields": [{"name": n, "value": v, "quote": q} for n, v, q in f], "people": people, "notes": ""}


def _o(name, title, pct, control=False):
    return Owner(name, title, pct, control)


TWO = (_o("Nora Lindqvist", "Managing Member and Chief Executive Officer", 65, True), _o("Tomas Reyes", "Member", 35))
THREE = (_o("Priya Desai", "Managing Member", 40, True), _o("Ethan Cole", "Member", 30), _o("Mara Quinn", "Member", 30))
CORP = (_o("Daniel Okoro", "President and Chief Executive Officer", 55, True), _o("Lena Fischer", "Director", 45))
LP = (_o("Sofia Marin", "Managing Partner", 60, True), _o("Owen Park", "Limited Partner", 40))


def _firm(key, about, name, crd, seeded=(), owners=TWO, **kw) -> Package:
    n = int(crd or "0") % 1000
    defaults = dict(sec_file=f"801-12{n:04d}", address=f"{100 + n} S Main St, Salt Lake City, UT 84111",
                    ein=f"87-12{n:05d}")
    defaults.update(kw)
    return Package(key=key, about=about, name=name, crd=crd, owners=owners, seeded=frozenset(seeded), **defaults)


PACKAGES: list[Package] = [
    # Clean packages: nothing should be flagged
    _firm("clean-llc", "Clean LLC, two owners", "Wasatch Ridge Advisors LLC", "320101"),
    _firm("clean-three", "Clean LLC, three owners", "Granite Arch Capital LLC", "320102", owners=THREE),
    _firm("clean-corp", "Clean corporation", "Bear River Wealth Inc.", "320103", owners=CORP, entity="corporation"),
    _firm("clean-lp", "Clean limited partnership", "Harbor Peak Partners LP", "320104", owners=LP, entity="partnership",
          address="45 W Broadway, Salt Lake City, UT 84101"),
    # One defect each: every document NIGO the firm rules raise
    _firm("no-sec-file", "SEC file number left blank", "Dixie Basin Advisors LLC", "320105", ["REG-003"], sec_file=None),
    _firm("no-crd", "CRD number left blank", "Kolob Arch Investment Counsel LLC", None, ["REG-004"],
          sec_file="801-120106", address="106 S Main St, Salt Lake City, UT 84111", ein="87-1200106"),
    _firm("no-formation", "No formation document", "Cedar Breaks Capital LLC", "320107", ["KYB-001"],
          formation="missing"),
    _firm("no-chart", "No ownership chart", "Echo Canyon Wealth LLC", "320108", ["KYB-002"], chart="missing"),
    _firm("no-bo-cert", "No beneficial ownership certification", "Fremont Peak Advisors LLC", "320109", ["KYB-003"],
          bo_cert="missing"),
    _firm("no-control", "Control person not designated", "Goblin Valley Partners LLC", "320110", ["KYB-004"],
          owners=(_o("Ana Ruiz", "Member", 50), _o("Ben Ito", "Member", 50)), control_named=False),
    _firm("blank-signature", "Agreement signature block left blank", "Henry Mountains Capital LLC", "320111",
          ["KYB-006"], agreement="blank_signature"),
    _firm("no-ein", "No EIN anywhere in the package", "Indian Peaks Advisors LLC", "320112", ["CIP-F01"], ein=None),
    _firm("no-address", "Principal office left blank", "Jordanelle Wealth Partners LLC", "320113", ["CIP-F02"],
          address=None),
    _firm("po-box", "Principal office is a P.O. box", "Lake Powell Asset Management LLC", "320114", ["CIP-F03"],
          address="P.O. Box 4471, Park City, UT 84060"),
    # Traps: the item is mentioned but not there
    _firm("formation-to-follow", "Formation document promised, not sent", "Manti Ridge Capital LLC", "320115",
          ["KYB-001"], formation="to_follow"),
    _firm("chart-listed", "Ownership chart listed in the contents, not attached", "Nebo Summit Advisors LLC", "320116",
          ["KYB-002"], chart="listed_not_attached"),
    _firm("bo-to-follow", "Certification to follow under separate cover", "Ogden Valley Wealth LLC", "320117",
          ["KYB-003"], bo_cert="to_follow"),
    _firm("to-be-signed", "Agreement to be signed at account opening", "Provo Canyon Capital LLC", "320118", ["KYB-006"],
          agreement="to_be_signed"),
    # Several defects at once
    _firm("three-gaps", "No certification, no EIN, agreement unsigned", "Quail Creek Advisors LLC", "320119",
          ["KYB-003", "CIP-F01", "KYB-006"], bo_cert="missing", ein=None, agreement="blank_signature"),
    _firm("two-gaps", "P.O. box and no SEC file number", "Raft River Investment Partners LLC", "320120",
          ["CIP-F03", "REG-003"], address="P.O. Box 912, Logan, UT 84321", sec_file=None),
]


def flagged(app) -> set[str]:
    """The document NIGOs the firm rules raise for an application."""
    return {d.rule_id for d in evaluate_firm(app, AS_OF).decisions if d.rule_id in NIGO_RULES}


def score(rows: list[dict]) -> dict:
    seeded = sum(len(r["seeded"]) for r in rows)
    caught = sum(len(r["caught"]) for r in rows)
    false_nigo = sum(len(r["false_nigo"]) for r in rows)
    recall = caught / seeded if seeded else None
    return {"packages": len(rows), "seeded": seeded, "caught": caught, "missed": seeded - caught,
            "recall": round(recall, 4) if recall is not None else None, "false_nigo": false_nigo,
            "clean_packages_flagged": sum(1 for r in rows if not r["seeded"] and r["false_nigo"]),
            "target": TARGET_RECALL, "met": recall is not None and recall >= TARGET_RECALL}


def run(extract, packages: list[Package] | None = None, workers: int = 1) -> dict:
    """Run each package through `extract(text) -> ExtractionResult` and the firm rules, and score the result.
    `workers` > 1 reads packages in parallel (live model calls)."""
    packages = packages or PACKAGES

    def one(p: Package) -> dict:
        text = package_text(p)
        try:
            result = extract(text)
        except Exception as exc:  # noqa: BLE001 - a failed call counts as a package the AI didn't read
            return {"key": p.key, "about": p.about, "seeded": sorted(p.seeded), "flagged": [], "caught": [],
                    "missed": sorted(p.seeded), "false_nigo": [], "error": str(exc)[:300]}
        got = flagged(result.application)
        return {"key": p.key, "about": p.about, "seeded": sorted(p.seeded), "flagged": sorted(got),
                "caught": sorted(p.seeded & got), "missed": sorted(p.seeded - got),
                "false_nigo": sorted(got - p.seeded), "rejected": len(result.rejected), "error": None}

    if workers > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(one, packages))
    else:
        rows = [one(p) for p in packages]
    return {**score(rows), "rows": rows, "rules": NIGO_RULES}
