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
from .orm import Account, Case, Firm, OutboundEmail, User, utcnow

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

def _recipients(db: Session, case: Case) -> list[User]:
    """Who hears about a case: the firm's RIA users, or the client for a change they asked for."""
    if case.kind == "change" and case.created_by:
        submitter = db.get(User, case.created_by)
        if submitter and submitter.role == "client":
            return [submitter]
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
                            "The account is open but restricted from trading and fee deduction until we receive:\n"
                            + "\n".join(f"- {i}" for i in items)),
        "DECLINED": ("declined", f"Application declined: {name}", "We weren't able to approve this application. "
                     "Your RIA service team will follow up with the reason."),
        "APPLIED": ("change_applied", f"Change applied: {name}", "The change you asked for has been applied."),
        "REDIRECTED": ("redirected", f"Please resubmit through the right channel: {name}",
                       "Changes to a pure RIA account come from the RIA; changes to the self-directed account come "
                       "from the client."),
    }
    if status not in notices or previous == status or case.kind == "firm":
        return
    template, subject, text = notices[status]
    for u in _recipients(db, case):
        body = f"Hello {u.display_name.split()[0]},\n\n{text}\n\nOpen it in the portal: {link}\n\nYour RIA service team"
        queue(db, to=u.email, to_name=u.display_name, subject=subject, body=body, template=template, case=case)
