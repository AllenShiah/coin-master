"""Supabase (PostgREST) connection settings."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.util.env import env


@dataclass(frozen=True)
class SupabaseSettings:
    url: str
    key: str

    @property
    def enabled(self) -> bool:
        """True when a real Supabase target is configured.

        When false the app falls back to an in-memory repository so the FastAPI
        surface can be exercised without a database.
        """
        return bool(self.url and self.key)

    @classmethod
    def from_env(cls) -> "SupabaseSettings":
        return cls(
            url=env("SUPABASE_URL").rstrip("/"),
            key=env("SUPABASE_KEY"),
        )


@lru_cache(maxsize=1)
def get_settings() -> SupabaseSettings:
    return SupabaseSettings.from_env()


settings = get_settings()
