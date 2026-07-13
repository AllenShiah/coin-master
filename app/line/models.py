"""Pydantic models for the subset of the LINE webhook payload we consume.

Only the fields we act on are declared; ``extra`` values are ignored so LINE can
evolve its schema without breaking parsing.  See
https://developers.line.biz/en/reference/messaging-api/#webhook-event-objects
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Mentionee(_Base):
    index: int = 0
    length: int = 0
    userId: str | None = None
    type: str | None = None
    isSelf: bool = False


class Mention(_Base):
    mentionees: list[Mentionee] = Field(default_factory=list)


class Source(_Base):
    type: str | None = None
    userId: str | None = None
    groupId: str | None = None
    roomId: str | None = None


class Message(_Base):
    id: str | None = None
    type: str | None = None
    text: str | None = None
    mention: Mention | None = None


class Event(_Base):
    type: str | None = None
    mode: str | None = None
    webhookEventId: str | None = None
    replyToken: str | None = None
    timestamp: int | None = None
    source: Source | None = None
    message: Message | None = None

    @property
    def event_datetime(self) -> datetime:
        """LINE timestamps are epoch milliseconds (UTC)."""
        if self.timestamp is None:
            return datetime.now(tz=timezone.utc)
        return datetime.fromtimestamp(self.timestamp / 1000, tz=timezone.utc)


class WebhookRequest(_Base):
    destination: str | None = None
    events: list[Event] = Field(default_factory=list)
