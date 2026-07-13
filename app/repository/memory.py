"""Thread-safe, process-local implementation of :class:`LoanRepository`."""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal

from app.repository.base import LoanRepository
from app.util.domain import LoanTransaction, User, WebhookEvent


class InMemoryLoanRepository(LoanRepository):
    """Thread-safe, process-local store. Data is lost on restart."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._users: dict[str, User] = {}
        self._events: dict[str, WebhookEvent] = {}
        self._transactions: list[LoanTransaction] = []
        self._cache: dict[tuple[str, str, int, int], tuple[Decimal, Decimal]] = {}
        self._seq = 0

    def _next_id(self) -> int:
        self._seq += 1
        return self._seq

    def upsert_user(self, line_user_id: str, display_name: str | None = None) -> User:
        with self._lock:
            user = self._users.get(line_user_id)
            if user is None:
                user = User(
                    line_user_id=line_user_id,
                    display_name=display_name,
                    id=self._next_id(),
                )
                self._users[line_user_id] = user
            elif display_name and user.display_name != display_name:
                user.display_name = display_name
            return user

    def get_user(self, line_user_id: str) -> User | None:
        with self._lock:
            return self._users.get(line_user_id)

    def record_event(self, event: WebhookEvent) -> WebhookEvent | None:
        with self._lock:
            if event.webhook_event_id in self._events:
                return None
            event.id = self._next_id()
            self._events[event.webhook_event_id] = event
            return event

    def set_event_status(
        self, webhook_event_id: str, status: str, error: str | None = None
    ) -> None:
        with self._lock:
            event = self._events.get(webhook_event_id)
            if event is not None:
                event.parse_status = status
                event.parse_error = error

    def add_transaction(self, transaction: LoanTransaction) -> LoanTransaction:
        with self._lock:
            transaction.id = self._next_id()
            self._transactions.append(transaction)
            return transaction

    def transactions_as_debtor(self, debtor_line_id: str) -> list[LoanTransaction]:
        with self._lock:
            return [t for t in self._transactions if t.debtor_line_id == debtor_line_id]

    def transactions_as_creditor(self, creditor_line_id: str) -> list[LoanTransaction]:
        with self._lock:
            return [
                t for t in self._transactions if t.creditor_line_id == creditor_line_id
            ]

    def get_cached_interest(
        self, debtor_line_id: str, creditor_line_id: str, year: int, month: int
    ) -> Decimal | None:
        with self._lock:
            entry = self._cache.get((debtor_line_id, creditor_line_id, year, month))
            return entry[1] if entry else None

    def save_cached_interest(
        self,
        debtor_line_id: str,
        creditor_line_id: str,
        year: int,
        month: int,
        closing_balance: Decimal,
        interest: Decimal,
    ) -> None:
        with self._lock:
            self._cache[(debtor_line_id, creditor_line_id, year, month)] = (
                closing_balance,
                interest,
            )

    def invalidate_cached_interest(
        self, debtor_line_id: str, creditor_line_id: str, on_or_after: date
    ) -> None:
        with self._lock:
            stale = [
                key
                for key in self._cache
                if key[0] == debtor_line_id
                and key[1] == creditor_line_id
                and date(key[2], key[3], 1) >= date(on_or_after.year, on_or_after.month, 1)
            ]
            for key in stale:
                del self._cache[key]
