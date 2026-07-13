"""LINE Messaging API credentials and endpoints."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from app.util.env import env


@dataclass(frozen=True)
class LineSettings:
    channel_secret: str
    channel_access_token: str
    reply_endpoint: str = field(default="https://api.line.me/v2/bot/message/reply")
    push_endpoint: str = field(default="https://api.line.me/v2/bot/message/push")
    profile_endpoint: str = field(default="https://api.line.me/v2/bot/profile")

    @property
    def reply_enabled(self) -> bool:
        return bool(self.channel_access_token)

    @classmethod
    def from_env(cls) -> "LineSettings":
        return cls(
            channel_secret=env("LINE_CHANNEL_SECRET"),
            channel_access_token=env("LINE_CHANNEL_ACCESS_TOKEN"),
        )


@lru_cache(maxsize=1)
def get_settings() -> LineSettings:
    return LineSettings.from_env()


settings = get_settings()
