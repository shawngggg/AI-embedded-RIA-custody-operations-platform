"""
Copy the onboarding modules into docs/console/py/ so GitHub Pages can serve
them to the browser console. Run after changing any module:

    python tools/sync_console.py

onboarding/test_console_sync.py fails if the copies drift from the source.
"""
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "onboarding"
DEST = ROOT / "docs" / "console" / "py"


def console_files() -> list[str]:
    """The module list the console page loads (PY_FILES in docs/console/index.html)."""
    html = (ROOT / "docs" / "console" / "index.html").read_text(encoding="utf-8")
    block = re.search(r"const PY_FILES = \[(.*?)\];", html, re.S).group(1)
    return re.findall(r'"([\w]+\.py)"', block)


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    for old in DEST.glob("*.py"):
        old.unlink()
    for name in console_files():
        shutil.copy2(SRC / name, DEST / name)
    print(f"Copied {len(console_files())} modules to {DEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
