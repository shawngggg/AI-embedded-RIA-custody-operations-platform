"""
Check a downloaded audit export (Audit trail → Export, the hash-chained JSON) on its own:

    python tools/verify_audit_export.py audit-log.json

Recomputes every event's hash from the one before (app/audit_chain.py) and reports the first event that doesn't fit,
and whether the heads recorded by earlier exports still match.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audit_chain import FORMAT, verify  # noqa: E402


def main(path: str) -> int:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format") != FORMAT:
        print(f"Not a {FORMAT} export")
        return 2
    bad = verify(data["events"])
    if bad is not None:
        print(f"Broken at event {bad}: it, or something before it, changed after the export")
        return 1
    stale = [c for c in data.get("checkpoints", []) if not c["ok"]]
    head = data["head"]
    print(f"{len(data['events'])} events chain to {head['hash'][:16]}... at event {head['id']}")
    if stale:
        print(f"{len(stale)} earlier export heads no longer match: the log changed between exports")
        return 1
    print(f"{len(data.get('checkpoints', []))} earlier export heads still match")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]) if len(sys.argv) == 2 else 2)
