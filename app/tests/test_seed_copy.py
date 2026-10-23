"""The bulk-copy seed (app/seed.py, seed_database): on Postgres a deploy and a demo reset build the seed in a local
SQLite file and copy it across, so the copy must carry every row and leave the ids ready for new rows."""
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, func, select

from app.db import Base
from app.seed import build_local_seed, copy_database


def test_the_copy_carries_every_row_and_new_rows_get_new_ids():
    folder = Path(tempfile.mkdtemp(prefix="seed-copy-"))
    source, _ = build_local_seed(str(folder / "source.db"))
    target = create_engine(f"sqlite:///{folder / 'target.db'}")
    copy_database(source, target)
    with source.connect() as s, target.connect() as t:
        for table in Base.metadata.sorted_tables:
            assert s.scalar(select(func.count()).select_from(table)) == \
                t.scalar(select(func.count()).select_from(table)), table.name
        legs = Base.metadata.tables["ledger_legs"]
        assert s.scalar(select(func.sum(legs.c.units))) == t.scalar(select(func.sum(legs.c.units)))
        cases = Base.metadata.tables["cases"]
        first = select(cases.c.application, cases.c.created_at).order_by(cases.c.id).limit(1)
        assert s.execute(first).one() == t.execute(first).one()
    events = Base.metadata.tables["audit_events"]
    with target.begin() as t:
        top = t.scalar(select(func.max(events.c.id)))
        new = t.execute(events.insert().values(actor_name="system", actor_role="system", action="test",
                                               target_type="x", target_id="1", detail={})).inserted_primary_key[0]
        assert new == top + 1
    source.dispose()
    target.dispose()
