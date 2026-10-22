"""
Synthetic data for the demo (Shawn, Oct. 21, 2026: "as real an experience as possible"). Every firm, person, account,
security, price, and number here is invented.

- cases.py: more onboarding, CIP, and account-change cases, seeded into the app across every queue.
- book.py: one consistent book built from the demo's open accounts: positions, trades and fails, money movement,
  asset transfers, a billing run, three-layer settlement, and reconciliation breaks. The release 2 and 3 pages show it
  read-only.
- export.py: writes the book and the cases to CSV and JSON (data/synthetic).
"""
import sys
from pathlib import Path

# Like the app, these modules use the onboarding engine's flat imports (from models import ...).
ENGINE_DIR = Path(__file__).resolve().parent.parent / "onboarding"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
