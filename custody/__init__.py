"""
The release 2 engine: the three-layer ledger, money movement and transfers, cash settlement, position reconciliation,
client entitlements, product acceptance, and the account lifecycle (offboarding, deceased accounts, RIA transitions,
advisor moves and mergers). Pure Python, no database: the app (app/) stores what it decides and posts what it asks for.

Each workflow follows its process map (bpmn/05, 08, 12, 13, 16, 17, 22, 23, 24) and reuses release 1's shared services:
the flagged-list check, the transferability review, and restriction codes (onboarding/).
"""
import sys
from pathlib import Path

# The onboarding engine uses flat imports (from models import ...), so its folder goes on the path once, here.
ENGINE_DIR = Path(__file__).resolve().parent.parent / "onboarding"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
