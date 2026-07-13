"""Parse LINE text messages into structured commands.

Two commands are understood:

* register -- a borrow/repay record, e.g.::

      債權人: @creditor 日期: 2026/07/12 借款: 100,000 年利率: 2.92%

* check -- a balance/interest query, optionally scoped to a month::

      check              -> everything
      check 2026/06      -> just that statement month

Anything that looks like a command but does not fit the grammar yields a
:class:`FormatError`; unrelated chatter yields :class:`Ignored`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from app.domain import BORROW, REPAY

# A percentage in the message ("2.92%") is divided by this to get a fraction.
_PERCENT = Decimal("100")

_DATE_RE = re.compile(r"日期\s*[:：]\s*(\d{4})[/-](\d{1,2})[/-](\d{1,2})")
_BORROW_RE = re.compile(r"借款\s*[:：]\s*([\d,]+(?:\.\d+)?)")
_REPAY_RE = re.compile(r"還款\s*[:：]\s*([\d,]+(?:\.\d+)?)")
_RATE_RE = re.compile(r"年利率\s*[:：]\s*(\d+(?:\.\d+)?)\s*%")
_CHECK_RE = re.compile(r"^\s*(?:check|查詢)\s*(?:(\d{4})[/-](\d{1,2}))?\s*$", re.IGNORECASE)

# Keywords that mark a message as *intended* as a command (so we help on error
# instead of silently ignoring genuine typos).
_COMMAND_HINTS = ("債權人", "借款", "還款")

FORMAT_EXAMPLE = (
    "債權人: @對方 日期: 2026/07/12 借款: 100,000 年利率: 2.92%"
)


@dataclass(frozen=True)
class MentionRef:
    index: int
    length: int
    user_id: str | None
    is_self: bool = False


@dataclass(frozen=True)
class RegisterCommand:
    creditor_line_id: str
    creditor_display: str | None
    transaction_type: str
    signed_amount: Decimal
    annual_rate: Decimal
    transaction_date: date


@dataclass(frozen=True)
class CheckCommand:
    year: int | None
    month: int | None

    @property
    def is_single_month(self) -> bool:
        return self.year is not None and self.month is not None


@dataclass(frozen=True)
class FormatError:
    reason: str


@dataclass(frozen=True)
class Ignored:
    pass


Command = RegisterCommand | CheckCommand | FormatError | Ignored


class MessageParser:
    """Stateless converter from raw text (+ mentions) to a :data:`Command`."""

    def parse(self, text: str, mentions: list[MentionRef]) -> Command:
        if text is None:
            return Ignored()

        check = self._try_check(text)
        if check is not None:
            return check

        if self._looks_like_register(text):
            return self._parse_register(text, mentions)

        return Ignored()

    # -- check ---------------------------------------------------------------
    def _try_check(self, text: str) -> CheckCommand | FormatError | None:
        match = _CHECK_RE.match(text)
        if match is None:
            return None
        year_raw, month_raw = match.group(1), match.group(2)
        if year_raw is None:
            return CheckCommand(year=None, month=None)
        month = int(month_raw)
        if not 1 <= month <= 12:
            return FormatError("查詢月份必須介於 1 到 12。範例：check 2026/06")
        return CheckCommand(year=int(year_raw), month=month)

    # -- register ------------------------------------------------------------
    def _looks_like_register(self, text: str) -> bool:
        return any(hint in text for hint in _COMMAND_HINTS)

    def _parse_register(self, text: str, mentions: list[MentionRef]) -> Command:
        transaction_type, signed_amount = self._parse_direction(text)
        if transaction_type is None:
            return FormatError(f"缺少借款或還款金額。格式：{FORMAT_EXAMPLE}")

        transaction_date = self._parse_date(text)
        if transaction_date is None:
            return FormatError(f"缺少或無法辨識日期。格式：{FORMAT_EXAMPLE}")

        annual_rate = self._parse_rate(text)
        if annual_rate is None:
            return FormatError(f"缺少或無法辨識年利率。格式：{FORMAT_EXAMPLE}")

        creditor = self._pick_creditor(mentions)
        if creditor is None:
            return FormatError(
                "請 @ 標註您的債權人（對方需已加入本機器人）。"
                f"格式：{FORMAT_EXAMPLE}"
            )
        creditor_ref, creditor_display = creditor

        return RegisterCommand(
            creditor_line_id=creditor_ref.user_id,  # type: ignore[arg-type]
            creditor_display=self._mention_text(text, creditor_ref) or creditor_display,
            transaction_type=transaction_type,
            signed_amount=signed_amount,
            annual_rate=annual_rate,
            transaction_date=transaction_date,
        )

    def _parse_direction(
        self, text: str
    ) -> tuple[str | None, Decimal]:
        borrow = _BORROW_RE.search(text)
        repay = _REPAY_RE.search(text)
        if borrow and repay:
            return None, Decimal("0")  # ambiguous -> treated as missing
        if borrow:
            amount = self._to_decimal(borrow.group(1))
            return (BORROW, amount) if amount and amount > 0 else (None, Decimal("0"))
        if repay:
            amount = self._to_decimal(repay.group(1))
            return (REPAY, -amount) if amount and amount > 0 else (None, Decimal("0"))
        return None, Decimal("0")

    def _parse_date(self, text: str) -> date | None:
        match = _DATE_RE.search(text)
        if match is None:
            return None
        year, month, day = (int(group) for group in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None

    def _parse_rate(self, text: str) -> Decimal | None:
        match = _RATE_RE.search(text)
        if match is None:
            return None
        value = self._to_decimal(match.group(1))
        if value is None or value < 0:
            return None
        return value / _PERCENT

    def _pick_creditor(
        self, mentions: list[MentionRef]
    ) -> tuple[MentionRef, str | None] | None:
        for mention in mentions:
            if not mention.is_self and mention.user_id:
                return mention, None
        return None

    @staticmethod
    def _mention_text(text: str, mention: MentionRef) -> str | None:
        segment = text[mention.index : mention.index + mention.length]
        segment = segment.lstrip("@").strip()
        return segment or None

    @staticmethod
    def _to_decimal(raw: str) -> Decimal | None:
        try:
            return Decimal(raw.replace(",", ""))
        except (InvalidOperation, AttributeError):
            return None
