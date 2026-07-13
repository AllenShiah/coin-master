"""Persistence abstraction.

The service depends only on :class:`LoanRepository`.  Two implementations exist:

* :class:`~app.repository.memory.InMemoryLoanRepository` -- zero-dependency,
  used when Supabase is not configured so the FastAPI surface can be exercised
  with plain ``curl``.
* :class:`~app.repository.supabase.SupabaseLoanRepository` -- the production
  PostgREST-backed store.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from decimal import Decimal

from app.util.domain import LoanTransaction, User, WebhookEvent


class LoanRepository(ABC):
    """CRUD operations the bot needs, independent of the storage engine."""

    @abstractmethod
    def upsert_user(self, line_user_id: str, display_name: str | None = None) -> User: ...

    @abstractmethod
    def get_user(self, line_user_id: str) -> User | None: ...

    @abstractmethod
    def record_event(self, event: WebhookEvent) -> WebhookEvent | None:
        """Persist an event; return None if its id was already seen (duplicate)."""

    @abstractmethod
    def set_event_status(
        self, webhook_event_id: str, status: str, error: str | None = None
    ) -> None: ...

    @abstractmethod
    def add_transaction(self, transaction: LoanTransaction) -> LoanTransaction: ...

    @abstractmethod
    def transactions_as_debtor(self, debtor_line_id: str) -> list[LoanTransaction]: ...

    @abstractmethod
    def transactions_as_creditor(self, creditor_line_id: str) -> list[LoanTransaction]: ...

    # -- fixed-month interest cache -----------------------------------------
    @abstractmethod
    def get_cached_interest(
        self, debtor_line_id: str, creditor_line_id: str, year: int, month: int
    ) -> Decimal | None: ...

    @abstractmethod
    def save_cached_interest(
        self,
        debtor_line_id: str,
        creditor_line_id: str,
        year: int,
        month: int,
        closing_balance: Decimal,
        interest: Decimal,
    ) -> None: ...

    @abstractmethod
    def invalidate_cached_interest(
        self, debtor_line_id: str, creditor_line_id: str, on_or_after: date
    ) -> None:
        """Drop cached months whose window could contain a change on/after a date."""
