"""Settings from the environment. Defaults run a local demo with SQLite."""
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get(
        "DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'mvp.db'}"))
    secret_key: str = field(default_factory=lambda: os.environ.get("SECRET_KEY", "dev-only-secret-change-me-before-deploying-0001"))
    demo_mode: bool = field(default_factory=lambda: _bool("DEMO_MODE", True))
    seed_on_start: bool = field(default_factory=lambda: _bool("SEED_ON_START", True))
    anthropic_api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    ai_daily_cap: int = field(default_factory=lambda: int(os.environ.get("AI_DAILY_CAP", "50")))
    # Workload identity federation (preferred on Cloud Run): no key; see onboarding/extraction.py
    federation_rule_id: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_FEDERATION_RULE_ID", ""))
    anthropic_org_id: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_ORGANIZATION_ID", ""))
    as_of: str = field(default_factory=lambda: os.environ.get("AS_OF", ""))   # fixed evaluation date (tests)
    clock: str = field(default_factory=lambda: os.environ.get("CLOCK", ""))   # fixed Eastern time, HH:MM (tests, seed)
    session_hours: int = field(default_factory=lambda: int(os.environ.get("SESSION_HOURS", "8")))
    cookie_secure: bool = field(default_factory=lambda: _bool("COOKIE_SECURE", False))
    static_dir: Path = field(default_factory=lambda: Path(os.environ.get("STATIC_DIR", str(ROOT / "web" / "dist"))))

    @property
    def ai_live(self) -> bool:
        return bool(self.anthropic_api_key or (self.federation_rule_id and self.anthropic_org_id))

    @property
    def ai_auth(self) -> str:
        if self.federation_rule_id and self.anthropic_org_id:
            return "workload identity federation"
        return "API key" if self.anthropic_api_key else "none"

    def today(self) -> date:
        return date.fromisoformat(self.as_of) if self.as_of else date.today()

    def clock_et(self) -> time:
        """The time of day in New York, for the rail cutoffs (custody MM-013)."""
        if self.clock:
            return time.fromisoformat(self.clock)
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).time().replace(microsecond=0)


settings = Settings()
