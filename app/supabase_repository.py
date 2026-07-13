"""Supabase (PostgREST) implementation of :class:`LoanRepository`.

Talks to the auto-generated REST API that Supabase exposes at
``{SUPABASE_URL}/rest/v1``.  All timestamps/dates are passed as ISO strings and
numeric money values as strings to preserve Decimal precision end to end.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx

from app.config import Settings, settings
from app.domain import LoanTransaction, User, WebhookEvent

_TIMEOUT_SECONDS = 10.0


class SupabaseLoanRepository:
    """PostgREST-backed store. Interface-compatible with LoanRepository."""

    def __init__(self, config: Settings = settings) -> None:
        self._rest = f"{config.supabase_url}/rest/v1"
        self._client = httpx.Client(
            base_url=self._rest,
            timeout=_TIMEOUT_SECONDS,
            headers={
                "apikey": config.supabase_key,
                "Authorization": f"Bearer {config.supabase_key}",
                "Content-Type": "application/json",
            },
        )

    # -- low level helpers ---------------------------------------------------
    def _get(self, table: str, params: dict) -> list[dict]:
        response = self._client.get(f"/{table}", params=params)
        response.raise_for_status()
        return response.json()

    def _insert(self, table: str, row: dict, *, prefer: str, params: dict | None = None) -> list[dict]:
        response = self._client.post(
            f"/{table}",
            params=params or {},
            json=row,
            headers={"Prefer": prefer},
        )
        response.raise_for_status()
        return response.json() if response.content else []

    def _delete(self, table: str, params: dict) -> None:
        response = self._client.delete(f"/{table}", params=params)
        response.raise_for_status()

    # -- users ---------------------------------------------------------------
    def upsert_user(self, line_user_id: str, display_name: str | None = None) -> User:
        row: dict = {"line_user_id": line_user_id}
        if display_name:
            row["display_name"] = display_name
        rows = self._insert(
            "users",
            row,
            prefer="resolution=merge-duplicates,return=representation",
            params={"on_conflict": "line_user_id"},
        )
        if not rows:  # merge that changed nothing returns empty -> read it back
            return self.get_user(line_user_id)  # type: ignore[return-value]
        return self._to_user(rows[0])

    def get_user(self, line_user_id: str) -> User | None:
        rows = self._get(
            "users",
            {"line_user_id": f"eq.{line_user_id}", "select": "*", "limit": "1"},
        )
        return self._to_user(rows[0]) if rows else None

    def _user_id(self, line_user_id: str) -> int:
        user = self.upsert_user(line_user_id)
        assert user.id is not None
        return user.id

    # -- events --------------------------------------------------------------
    def record_event(self, event: WebhookEvent) -> WebhookEvent | None:
        rows = self._insert(
            "line_webhook_events",
            {
                "webhook_event_id": event.webhook_event_id,
                "line_message_id": event.line_message_id,
                "line_user_id": event.line_user_id,
                "event_type": event.event_type,
                "message_type": event.message_type,
                "raw_text": event.raw_text,
                "raw_payload": event.raw_payload,
                "line_timestamp": event.line_timestamp.isoformat(),
                "parse_status": event.parse_status,
            },
            prefer="resolution=ignore-duplicates,return=representation",
            params={"on_conflict": "webhook_event_id"},
        )
        if not rows:  # conflict was ignored -> duplicate delivery
            return None
        event.id = rows[0]["id"]
        return event

    def set_event_status(
        self, webhook_event_id: str, status: str, error: str | None = None
    ) -> None:
        response = self._client.patch(
            "/line_webhook_events",
            params={"webhook_event_id": f"eq.{webhook_event_id}"},
            json={"parse_status": status, "parse_error": error},
        )
        response.raise_for_status()

    # -- transactions --------------------------------------------------------
    def add_transaction(self, transaction: LoanTransaction) -> LoanTransaction:
        rows = self._insert(
            "loan_transactions",
            {
                "debtor_user_id": self._user_id(transaction.debtor_line_id),
                "creditor_user_id": self._user_id(transaction.creditor_line_id),
                "source_event_id": transaction.source_event_id,
                "transaction_type": transaction.transaction_type,
                "amount": str(transaction.signed_amount),
                "annual_rate": str(transaction.annual_rate),
                "transaction_date": transaction.transaction_date.isoformat(),
                "note": transaction.note,
            },
            prefer="return=representation",
        )
        if rows:
            transaction.id = rows[0]["id"]
        return transaction

    def transactions_as_debtor(self, debtor_line_id: str) -> list[LoanTransaction]:
        return self._query_transactions("debtor_user_id", debtor_line_id)

    def transactions_as_creditor(self, creditor_line_id: str) -> list[LoanTransaction]:
        return self._query_transactions("creditor_user_id", creditor_line_id)

    def _query_transactions(self, column: str, line_user_id: str) -> list[LoanTransaction]:
        user = self.get_user(line_user_id)
        if user is None:
            return []
        rows = self._get(
            "loan_transactions",
            {
                column: f"eq.{user.id}",
                "select": "*,debtor:debtor_user_id(line_user_id),creditor:creditor_user_id(line_user_id)",
            },
        )
        return [self._to_transaction(row) for row in rows]

    # -- interest cache ------------------------------------------------------
    def get_cached_interest(
        self, debtor_line_id: str, creditor_line_id: str, year: int, month: int
    ) -> Decimal | None:
        rows = self._get(
            "monthly_interest",
            {
                "debtor_user_id": f"eq.{self._user_id(debtor_line_id)}",
                "creditor_user_id": f"eq.{self._user_id(creditor_line_id)}",
                "year_month": f"eq.{date(year, month, 1).isoformat()}",
                "select": "interest_amount",
                "limit": "1",
            },
        )
        return Decimal(str(rows[0]["interest_amount"])) if rows else None

    def save_cached_interest(
        self,
        debtor_line_id: str,
        creditor_line_id: str,
        year: int,
        month: int,
        closing_balance: Decimal,
        interest: Decimal,
    ) -> None:
        self._insert(
            "monthly_interest",
            {
                "debtor_user_id": self._user_id(debtor_line_id),
                "creditor_user_id": self._user_id(creditor_line_id),
                "year_month": date(year, month, 1).isoformat(),
                "closing_balance": str(closing_balance),
                "interest_amount": str(interest),
            },
            prefer="resolution=merge-duplicates,return=minimal",
            params={"on_conflict": "debtor_user_id,creditor_user_id,year_month"},
        )

    def invalidate_cached_interest(
        self, debtor_line_id: str, creditor_line_id: str, on_or_after: date
    ) -> None:
        first_of_month = date(on_or_after.year, on_or_after.month, 1)
        self._delete(
            "monthly_interest",
            {
                "debtor_user_id": f"eq.{self._user_id(debtor_line_id)}",
                "creditor_user_id": f"eq.{self._user_id(creditor_line_id)}",
                "year_month": f"gte.{first_of_month.isoformat()}",
            },
        )

    # -- row mappers ---------------------------------------------------------
    @staticmethod
    def _to_user(row: dict) -> User:
        return User(
            line_user_id=row["line_user_id"],
            display_name=row.get("display_name"),
            id=row.get("id"),
        )

    @staticmethod
    def _to_transaction(row: dict) -> LoanTransaction:
        debtor = (row.get("debtor") or {}).get("line_user_id", "")
        creditor = (row.get("creditor") or {}).get("line_user_id", "")
        return LoanTransaction(
            debtor_line_id=debtor,
            creditor_line_id=creditor,
            transaction_type=row["transaction_type"],
            signed_amount=Decimal(str(row["amount"])),
            annual_rate=Decimal(str(row["annual_rate"])),
            transaction_date=date.fromisoformat(row["transaction_date"]),
            note=row.get("note"),
            source_event_id=row.get("source_event_id"),
            id=row.get("id"),
        )
