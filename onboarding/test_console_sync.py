"""The browser console must load every module, and its copies must match the source."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONSOLE = HERE.parent / "docs" / "console"


def console_files():
    import re
    html = (CONSOLE / "index.html").read_text(encoding="utf-8")
    block = re.search(r"const PY_FILES = \[(.*?)\];", html, re.S).group(1)
    return re.findall(r'"([\w]+\.py)"', block)


def test_console_loads_every_module():
    modules = sorted(p.name for p in HERE.glob("*.py") if not p.name.startswith(("test_", "conftest")))
    assert sorted(console_files()) == modules


def test_console_copies_match_the_source():
    stale = [name for name in console_files()
             if (CONSOLE / "py" / name).read_bytes() != (HERE / name).read_bytes()]
    assert stale == [], f"Run python tools/sync_console.py; stale copies: {stale}"
