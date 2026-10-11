"""
MVP web app for the RIA custody operations platform: the operations workbench
and the RIA and client portal, on top of the onboarding engine in onboarding/.
"""
import sys
from pathlib import Path

# The onboarding engine uses flat imports (from models import ...), so its
# folder goes on the path once, here.
ENGINE_DIR = Path(__file__).resolve().parent.parent / "onboarding"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
