"""
Outbound email. Every notice the custodian sends (items requested, account opened, application declined, change
applied) is written to the outbox first, then handed to the provider.

Providers (MAIL_PROVIDER):
  outbox  the default: mail stays in the outbox, viewable in the workbench. Nothing is sent.
  m365    Microsoft 365 through Microsoft Graph: POST /users/{sender}/sendMail as an app holding Mail.Send, granted
          in Exchange Online and scoped to the sending mailbox (RBAC for Applications; see docs/DEPLOY.md).
          The app signs in to Microsoft Entra ID either with a client secret
          (MS_CLIENT_SECRET) or, on Cloud Run, with no secret at all: the service's Google identity token is
          presented as a federated credential (MS_FEDERATED=true).

In demo mode mail is always held, whatever the provider: the recipients are synthetic. MAIL_ALLOWED_DOMAINS
narrows who a live provider may send to (for example, your own domain while testing).
"""
from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .orm import Case, OutboundEmail, User, utcnow

GRAPH = "https://graph.microsoft.com/v1.0"
PORTAL_URL = os.environ.get("PORTAL_URL", "https://ria-custody-1638463558.us-central1.run.app/portal")


def provider() -> str:
    return os.environ.get("MAIL_PROVIDER", "outbox")


def connection() -> dict:
    """What the outbox page shows about the connection (never the secret)."""
    p = provider()
    return {"provider": p, "label": "Microsoft 365 (Outlook)" if p == "m365" else "Outbox only (nothing is sent)",
            "sender": os.environ.get("MAIL_SENDER") or None, "tenant_configured": bool(os.environ.get("MS_TENANT_ID")),
            "auth": ("federated credential (no secret)" if os.environ.get("MS_FEDERATED") == "true"
                     else "client secret" if os.environ.get("MS_CLIENT_SECRET") else None) if p == "m365" else None,
            "demo_hold": settings.demo_mode, "allowed_domains": _allowed() or None}


def _allowed() -> list[str]:
    return [d.strip().lower() for d in os.environ.get("MAIL_ALLOWED_DOMAINS", "").split(",") if d.strip()]


def _hold_reason(address: str) -> str | None:
    if provider() != "m365":
        return "Outbox only: no email provider is connected"
    if settings.demo_mode:
        return "Demo mode: synthetic recipients, so mail is held"
    allowed = _allowed()
    if allowed and address.split("@")[-1].lower() not in allowed:
        return f"Recipient's domain isn't in MAIL_ALLOWED_DOMAINS ({', '.join(allowed)})"
    return None


def _google_identity_token(audience: str) -> str:
    import httpx
    r = httpx.get("http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity",
                  params={"audience": audience, "format": "full"}, headers={"Metadata-Flavor": "Google"}, timeout=5)
    r.raise_for_status()
    return r.text.strip()


def graph_token(http=None) -> str:
    """An app-only Microsoft Graph token (client credentials), with a client secret or a federated credential."""
    import httpx
    tenant, client_id = os.environ["MS_TENANT_ID"], os.environ["MS_CLIENT_ID"]
    form = {"client_id": client_id, "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"}
    if os.environ.get("MS_FEDERATED") == "true":
        form["client_assertion_type"] = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        form["client_assertion"] = _google_identity_token("api://AzureADTokenExchange")
    else:
        form["client_secret"] = os.environ["MS_CLIENT_SECRET"]
    r = (http or httpx).post(f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token", data=form, timeout=10)
    r.raise_for_status()
    return r.json()["access_token"]


def send_m365(email: OutboundEmail, http=None) -> None:
    import httpx
    sender = os.environ["MAIL_SENDER"]
    message = {"message": {"subject": email.subject, "body": {"contentType": "Text", "content": email.body},
                           "toRecipients": [{"emailAddress": {"address": email.to_address,
                                                              **({"name": email.to_name} if email.to_name else {})}}]},
               "saveToSentItems": True}
    r = (http or httpx).post(f"{GRAPH}/users/{sender}/sendMail", json=message,
                             headers={"Authorization": f"Bearer {graph_token(http)}"}, timeout=15)
    if r.status_code != 202:
        raise RuntimeError(f"Graph sendMail returned {r.status_code}: {r.text[:300]}")


def queue(db: Session, *, to: str, subject: str, body: str, template: str, to_name: str | None = None,
          case: Case | None = None) -> OutboundEmail:
    """Write a notice to the outbox and, when a provider is connected and allowed, send it."""
    e = OutboundEmail(to_address=to, to_name=to_name, subject=subject, body=body, template=template,
                      case_id=case.id if case else None, provider=provider())
    db.add(e)
    why = _hold_reason(to)
    if why:
        e.status, e.error = "held", why
    else:
        try:
            send_m365(e)
            e.status, e.sent_at = "sent", utcnow()
        except Exception as exc:  # noqa: BLE001 - a failed send stays in the outbox with its reason
            e.status, e.error = "failed", str(exc)[:500]
    db.flush()
    return e


# --- Notices on case transitions ---------------------------------------------------------

# Release 2 notices: who needs to act or know, never why a review is happening (no sanctions, risk, or fraud wording)
R2_NOTICES = {
    "transfer": {
        "COMPLETED": ("transfer_completed", "Transfer complete", "The transfer is complete."),
        "RETURNED": ("transfer_returned", "Returned by the bank", "The bank returned this transfer:\n{reason}"),
        "REJECTED": ("transfer_declined", "Request not processed", "We weren't able to process this request. Your "
                     "RIA service team will follow up."),
        "BLOCKED": ("transfer_declined", "Request not processed", "We weren't able to process this request. Your "
                    "RIA service team will follow up."),
        "PENDING_CALLBACK": ("transfer_callback", "We'll call to confirm", "Before we release this request we'll call "
                             "the client on the phone number we already have on file."),
    },
    "entitlement": {
        "ACTIVE": ("capability_active", "Capability active", "The capability is active. Every request needs a "
                   "one-time passcode."),
        "PENDING_LINK_VERIFICATION": ("bank_link_verify", "Verify your bank link", "We sent two small deposits to "
                                      "the bank account. Enter their amounts in the portal to verify it."),
        "DECLINED": ("capability_declined", "Capability not granted", "We couldn't record this grant:\n{reason}"),
        "REVOKED": ("capability_revoked", "Capability revoked", "The capability was revoked."),
    },
    "product": {
        "AVAILABLE": ("product_available", "Product available", "The product is available on the platform."),
        "DECLINED": ("product_declined", "Product not accepted", "We can't hold this product. The client can keep "
                     "it at the current firm, or you can use another custodian for it."),
        "PENDING_DOCUMENTS": ("product_documents", "Documents needed", "We need:\n{reason}"),
    },
    "offboarding": {
        "AWAITING_TRANSFER": ("offboarding_started", "Account closing", "The account now takes only outgoing "
                              "transfers. The client can pick another RIA on the platform, or have the receiving firm "
                              "start an ACATS transfer."),
        "CLOSED": ("account_closed", "Account closed", "The account is closed. Final-year tax forms follow."),
    },
    "deceased": {
        "AWAITING_CERTIFICATE": ("death_notice", "Account on hold", "We received notice of the account owner's "
                                 "death and placed a hold. Your authority on the account has ended. Please ask the "
                                 "family for a certified death certificate."),
        "AWAITING_ESTATE_DOCS": ("estate_documents", "Estate documents needed", "We need:\n{reason}"),
        "SETTLED": ("estate_settled", "Estate settled", "The account has been distributed and closed."),
    },
    "advisor_move": {
        "COMPLETED": ("advisor_move_done", "Account moved", "The account is now with the new RIA. No assets moved."),
        "PENDING_ITEMS": ("advisor_move_items", "Items needed", "We need:\n{reason}"),
    },
}


def _recipients(db: Session, case: Case) -> list[User]:
    """Who hears about a case: the firm's RIA users, or the client for a change they asked for."""
    if case.kind in ("change", "transfer", "entitlement") and case.created_by:
        submitter = db.get(User, case.created_by)
        if submitter and submitter.role == "client":
            return [submitter]
    if case.kind == "entitlement" and case.account_id:
        clients = [u for u in db.scalars(select(User).where(User.client_account_id == case.account_id,
                                                            User.active.is_(True))).all() if u.email]
        firm = [u for u in db.scalars(select(User).where(User.firm_id == case.firm_id, User.active.is_(True),
                                                        User.role.in_(("ria_user", "ria_admin")))).all() if u.email]
        return clients + firm
    if not case.firm_id:
        return []
    return [u for u in db.scalars(select(User).where(User.firm_id == case.firm_id, User.active.is_(True),
                                                     User.role.in_(("ria_user", "ria_admin")))).all() if u.email]


def notify_transition(db: Session, case: Case, previous: str | None, status: str) -> None:
    """Email the people outside the custodian who need to act on, or know about, a case's new status."""
    result = case.result or {}
    items = result.get("requested_items") or []
    link = f"{PORTAL_URL}/cases/{case.id}"
    name = case.title.split(": ", 1)[-1]
    notices = {
        "PENDING_ITEMS": ("items_requested", f"Items needed: {name}",
                          "We need the following before we can continue:\n" + "\n".join(f"- {i}" for i in items)),
        "RETURNED": ("returned", f"Returned for correction: {name}",
                     "We couldn't process this request as submitted:\n" + "\n".join(f"- {i}" for i in items)),
        "OPEN": ("account_opened", f"Account open: {name}", "The account is open."),
        "OPEN_RESTRICTED": ("account_opened_restricted", f"Account open, restricted: {name}",
                            "The account is open, but some activity is restricted until we receive:\n"
                            + "\n".join(f"- {i}" for i in items)),
        "DECLINED": ("declined", f"Application declined: {name}", "We weren't able to approve this application. "
                     "Your RIA service team will follow up with the reason."),
        "APPLIED": ("change_applied", f"Change applied: {name}", "The change you asked for has been applied."),
        "REDIRECTED": ("redirected", f"Please resubmit through the right channel: {name}",
                       "Changes to a pure RIA account come from the RIA; changes to the self-directed account come "
                       "from the client."),
    }
    if case.kind in R2_NOTICES:
        if previous == status or status not in R2_NOTICES[case.kind]:
            return
        template, subject, text = R2_NOTICES[case.kind][status]
        subject = f"{subject}: {name}"
        if "{reason}" in text:
            text = text.replace("{reason}", "\n".join(f"- {i}" for i in items) or "-")
    elif status not in notices or previous == status or case.kind == "firm":
        return
    else:
        template, subject, text = notices[status]
    for u in _recipients(db, case):
        body = f"Hello {u.display_name.split()[0]},\n\n{text}\n\nOpen it in the portal: {link}\n\nYour RIA service team"
        queue(db, to=u.email, to_name=u.display_name, subject=subject, body=body, template=template, case=case)
    if case.kind == "transfer" and status == "COMPLETED" and previous != status and (case.application or {}).get("sloa_id"):
        sloa_transfer_notice(db, case)


# --- Client notices: to each account holder's email on file -----------------------------------------------

def client_notice(db: Session, account, *, subject: str, text: str, template: str, case: Case | None = None) -> int:
    """Write a notice to every account holder with an email on file. Returns how many went to the outbox."""
    from .servicing import holder_emails
    sent = 0
    for email, name in holder_emails(account):
        body = f"Hello {name.split()[0]},\n\n{text}\n\nQuestions? Call the number on your statement.\n\nThe custodian"
        queue(db, to=email, to_name=name, subject=subject, body=body, template=template, case=case)
        sent += 1
    return sent


def _sloa_line(s: dict) -> str:
    where = ", ".join(x for x in (s.get("institution"), f"account ending {s['account_last4']}" if s.get("account_last4")
                                  else None, s.get("payee_address")) if x)
    return f"{s['id']}: payments to {s.get('payee')} ({where}), {s.get('schedule') or 'from time to time'}"


def sloa_initial_notice(db: Session, account, s: dict, case: Case | None = None) -> int:
    """SEC SLOA relief, condition 7: confirm a new standing instruction to the client in writing."""
    return client_notice(db, account, template="sloa_initial", case=case,
                         subject=f"Your standing letter of authorization is on file: {account.display_name}",
                         text="We've set up the standing instruction you signed:\n" + _sloa_line(s) + "\n\n"
                              "Your advisor can send payments to this payee only, with these details, and can't change "
                              "them. You can change or end it at any time.")


def sloa_annual_notice(db: Session, account, s: dict) -> int:
    """Condition 7: reconfirm each standing instruction every year."""
    return client_notice(db, account, template="sloa_annual",
                         subject=f"Annual reminder: your standing letter of authorization, {account.display_name}",
                         text="This instruction you signed is still in place:\n" + _sloa_line(s) + "\n\n"
                              "If you no longer want it, tell us or your advisor and we'll end it.")


def sloa_transfer_notice(db: Session, case: Case) -> int:
    """Condition 3: a transfer-of-funds notice to the client promptly after each transfer under an SLOA."""
    from .orm import Account
    req = case.application or {}
    account = db.get(Account, case.account_id)
    cp = req.get("counterparty") or {}
    return client_notice(db, account, template="sloa_transfer", case=case,
                         subject=f"A payment left your account: {account.display_name}",
                         text=f"Your advisor sent ${float(req.get('amount') or 0):,.2f} to {cp.get('owner_name')} "
                              f"({cp.get('institution')}) under your standing instruction {req.get('sloa_id')}, "
                              f"on {settings.today():%B %-d, %Y}.")
