"""Application-service settings (report shape and timezone)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from zoneinfo import ZoneInfo

from app.util.constants import MONEY_QUANTIZE
from app.util.env import env


@dataclass(frozen=True)
class ServiceSettings:
    timezone_name: str
    money_quantize: Decimal = field(default=MONEY_QUANTIZE)
    # Most recent statement months to include in a ``check`` reply.
    report_max_months: int = field(default=24)

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @classmethod
    def from_env(cls) -> "ServiceSettings":
        return cls(timezone_name=env("APP_TIMEZONE", "Asia/Taipei"))


@lru_cache(maxsize=1)
def get_settings() -> ServiceSettings:
    return ServiceSettings.from_env()


settings = get_settings()
