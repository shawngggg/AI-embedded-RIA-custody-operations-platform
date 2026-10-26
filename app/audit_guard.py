"""
The audit log is insert-only in the database, not just in the code: on Postgres a trigger refuses every UPDATE, DELETE,
and TRUNCATE on audit_events, whoever runs it. The migration installs it, and so does every database copy (a deploy's
reseed or the demo reset rebuilds the tables, then reinstalls it).

The one way past it is dropping the table, which only the demo reset does (the platform administrator, in demo mode,
and the reset is logged). With DEMO_MODE=false there is no reset. Blocking DROP TABLE too would take a Postgres event
trigger, which needs superuser rights that Neon doesn't grant.

SQLite (local development and the default test run) has no trigger; the Postgres test run checks it.
"""
from sqlalchemy import text

FUNCTION = """
CREATE OR REPLACE FUNCTION audit_events_insert_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_events is insert-only: % refused', TG_OP USING ERRCODE = 'insufficient_privilege';
END $$
"""
TRIGGERS = [
    "DROP TRIGGER IF EXISTS audit_events_no_change ON audit_events",
    "CREATE TRIGGER audit_events_no_change BEFORE UPDATE OR DELETE ON audit_events "
    "FOR EACH ROW EXECUTE FUNCTION audit_events_insert_only()",
    "DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events",
    "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events "
    "FOR EACH STATEMENT EXECUTE FUNCTION audit_events_insert_only()",
]


def statements() -> list[str]:
    return [FUNCTION] + TRIGGERS


def install(conn) -> bool:
    """Install the guard on a Postgres connection; a no-op elsewhere. Returns whether it was installed."""
    if conn.dialect.name != "postgresql":
        return False
    for sql in statements():
        conn.execute(text(sql))
    return True
