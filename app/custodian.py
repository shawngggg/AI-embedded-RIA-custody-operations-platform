"""
Who the custodian is, in one place: the name, address, and phone numbers on every client notice and statement.
All synthetic. A client statement names the custodian and how to reach it, so a client can report an error to the
firm that holds the assets (FINRA Rule 2231; Advisers Act Rule 206(4)-2 relies on these statements reaching the client).
"""

NAME = "Custody Operations (synthetic custodian)"
ADDRESS = "100 Example Plaza, Salt Lake City, UT 84111"
CLIENT_PHONE = "800-555-0142"
CLIENT_HOURS = "business days, 7 a.m. to 7 p.m. Eastern"
RIA_SERVICE_EMAIL = "riaservice@custodian.example"
RIA_SERVICE_HOURS = "Business days, 7 a.m. to 7 p.m. Eastern"

CONTACT_LINE = f"Questions? Call us at {CLIENT_PHONE}, {CLIENT_HOURS}."
SIGNATURE = f"{NAME}\n{ADDRESS}\n{CLIENT_PHONE}"
