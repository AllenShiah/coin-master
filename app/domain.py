"""Domain data structures shared across parsing, storage and calculation.

These are plain dataclasses with no framework or database coupling so they can
move freely between the FastAPI layer, the repositories and the interest
calculator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

# Transaction directions. Borrow increases what the debtor owes the creditor and
# is stored as a positive signed amount; repay decreases it and is stored
# negative -- matching the "借款正數 / 還款負數" rule from the spec.
BORROW = "borrow"
REPAY = "repay"


@dataclass
class User:
    """A LINE user. ``id`` is the internal database key (None until persisted)."""

    line_user_id: str
    display_name: str | None = None
    id: int | None = None


@dataclass
class WebhookEvent:
    """A raw LINE webhook event captured for idempotency and auditing."""

    webhook_event_id: str
    line_user_id: str
    event_type: str
    line_timestamp: datetime
    raw_payload: dict
    message_type: str | None = None
    line_message_id: str | None = None
    raw_text: str | None = None
    parse_status: str = "pending"
    parse_error: str | None = None
    id: int | None = None


@dataclass
class LoanTransaction:
    """A single borrow/repay between a debtor and a creditor.

    ``signed_amount`` already carries the sign (positive borrow, negative repay);
    it always expresses "how much the debtor's debt to the creditor changed".
    """

    debtor_line_id: str
    creditor_line_id: str
    transaction_type: str
    signed_amount: Decimal
    annual_rate: Decimal
    transaction_date: date
    note: str | None = None
    source_event_id: str | None = None
    id: int | None = None

    @property
    def magnitude(self) -> Decimal:
        return abs(self.signed_amount)
