"""Central configuration.

All tunable values and environment-backed secrets live here so that the rest of
the code never reaches for ``os.environ`` directly nor hard-codes a "magic"
constant.  Import :data:`settings` (a process-wide singleton) wherever config is
needed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from zoneinfo import ZoneInfo

try:  # Loading a local .env is a developer convenience; in Docker the values
    # are injected via ``env_file`` so python-dotenv is optional at runtime.
    from dotenv import load_dotenv

    load_dotenv()
except ModuleNotFoundError:  # pragma: no cover - only hit when dep is absent
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    """Immutable application settings.

    The upper block is environment driven (secrets / deployment specific); the
    lower block holds business constants that would otherwise be magic numbers
    scattered through the code.
    """

    # --- environment driven -------------------------------------------------
    line_channel_secret: str
    line_channel_access_token: str
    supabase_url: str
    supabase_key: str
    timezone_name: str

    # --- business constants (no magic numbers elsewhere) --------------------
    days_per_year: Decimal = field(default=Decimal("365"))
    money_quantize: Decimal = field(default=Decimal("0.01"))
    # A monthly statement runs from the 21st of the previous month to the 20th
    # of the payable month (e.g. June interest = 05/21 .. 06/20).
    statement_start_day: int = field(default=21)
    statement_end_day: int = field(default=20)
    # Most recent statement months to include in a ``check`` reply.
    report_max_months: int = field(default=24)

    # LINE Messaging API endpoints.
    line_reply_endpoint: str = field(default="https://api.line.me/v2/bot/message/reply")
    line_push_endpoint: str = field(default="https://api.line.me/v2/bot/message/push")
    line_profile_endpoint: str = field(default="https://api.line.me/v2/bot/profile")

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def supabase_enabled(self) -> bool:
        """True when a real Supabase target is configured.

        When false the app falls back to an in-memory repository so the FastAPI
        surface can be exercised without a database.
        """
        return bool(self.supabase_url and self.supabase_key)

    @property
    def line_reply_enabled(self) -> bool:
        return bool(self.line_channel_access_token)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            line_channel_secret=_env("LINE_CHANNEL_SECRET"),
            line_channel_access_token=_env("LINE_CHANNEL_ACCESS_TOKEN"),
            supabase_url=_env("SUPABASE_URL").rstrip("/"),
            supabase_key=_env("SUPABASE_KEY"),
            timezone_name=_env("APP_TIMEZONE", "Asia/Taipei"),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


settings = get_settings()
