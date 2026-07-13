"""Application service: turns a parsed command into effects and a reply.

This is the orchestration layer.  It owns no math (delegated to
:class:`InterestCalculator`) and no persistence details (delegated to a
:class:`LoanRepository`); it wires them together and enforces the business rules
around who may see what.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from app.config import Settings, settings
from app.domain import LoanTransaction, WebhookEvent
from app.interest import InterestCalculator, LoanEntry
from app.line_models import Event
from app.parser import (
    CheckCommand,
    FormatError,
    Ignored,
    MentionRef,
    MessageParser,
    RegisterCommand,
)
from app.reports import (
    CREDITOR,
    DEBTOR,
    CheckReport,
    CounterpartyLine,
    MonthLine,
    RoleReport,
)
from app.repository import LoanRepository

_MESSAGE = "message"
_TEXT = "text"


class LoanBotService:
    def __init__(
        self,
        repository: LoanRepository,
        parser: MessageParser | None = None,
        calculator: InterestCalculator | None = None,
        config: Settings = settings,
    ) -> None:
        self._repo = repository
        self._parser = parser or MessageParser()
        self._calc = calculator or InterestCalculator(config)
        self._config = config

    # -- entry point ---------------------------------------------------------
    def handle_event(self, event: Event, today: date | None = None) -> str | None:
        """Process one webhook event; return the reply text (or None to stay silent)."""
        today = today or self._now()
        if not self._is_text_message(event):
            return None

        stored = self._record(event)
        if stored is None:  # duplicate delivery -> already handled, stay idempotent
            return None

        sender_id = event.source.userId  # guaranteed by _is_text_message
        self._repo.upsert_user(sender_id)
        command = self._parser.parse(event.message.text, self._mentions(event))

        try:
            reply = self._dispatch(command, sender_id, stored, today)
        except Exception as error:  # noqa: BLE001 - surface, don't crash the webhook
            self._repo.set_event_status(stored.webhook_event_id, "failed", str(error))
            raise
        self._repo.set_event_status(stored.webhook_event_id, "parsed")
        return reply

    def _dispatch(
        self, command, sender_id: str, event: WebhookEvent, today: date
    ) -> str | None:
        from app.formatting import format_check, format_format_error, format_register

        if isinstance(command, RegisterCommand):
            transaction = self._register(command, sender_id, event)
            return format_register(transaction)
        if isinstance(command, CheckCommand):
            return format_check(self._check(sender_id, command, today))
        if isinstance(command, FormatError):
            return format_format_error(command.reason)
        return None  # Ignored

    # -- register ------------------------------------------------------------
    def _register(
        self, command: RegisterCommand, sender_id: str, event: WebhookEvent
    ) -> LoanTransaction:
        if command.creditor_line_id == sender_id:
            raise ValueError("債權人不可以是自己")
        self._repo.upsert_user(command.creditor_line_id, command.creditor_display)

        transaction = LoanTransaction(
            debtor_line_id=sender_id,
            creditor_line_id=command.creditor_line_id,
            transaction_type=command.transaction_type,
            signed_amount=command.signed_amount,
            annual_rate=command.annual_rate,
            transaction_date=command.transaction_date,
            note=event.raw_text,
            source_event_id=str(event.id) if event.id is not None else None,
        )
        stored = self._repo.add_transaction(transaction)
        # A (back-dated) record can change any statement month it lands in or
        # after, so drop the affected fixed-month cache entries.
        self._repo.invalidate_cached_interest(
            sender_id, command.creditor_line_id, command.transaction_date
        )
        return stored

    # -- check ---------------------------------------------------------------
    def _check(self, user_id: str, command: CheckCommand, today: date) -> CheckReport:
        user = self._repo.get_user(user_id)
        single = (command.year, command.month) if command.is_single_month else None

        debtor_txns = self._repo.transactions_as_debtor(user_id)
        creditor_txns = self._repo.transactions_as_creditor(user_id)

        return CheckReport(
            line_user_id=user_id,
            display_name=user.display_name if user else None,
            as_of=today,
            debtor=self._role_report(DEBTOR, user_id, debtor_txns, today, command),
            creditor=self._role_report(CREDITOR, user_id, creditor_txns, today, command),
            single_month=single,
        )

    def _role_report(
        self,
        role: str,
        user_id: str,
        transactions: list[LoanTransaction],
        today: date,
        command: CheckCommand,
    ) -> RoleReport | None:
        if not transactions:
            return None
        by_counterparty = self._group_by_counterparty(role, transactions)

        counterparties = [
            CounterpartyLine(
                line_id=counterparty,
                display_name=self._display_name(counterparty),
                outstanding=self._calc.outstanding_as_of(entries, today),
            )
            for counterparty, entries in by_counterparty.items()
        ]
        total_outstanding = sum(
            (line.outstanding for line in counterparties), Decimal("0")
        )

        if command.is_single_month:
            month = self._aggregate_month(
                role, user_id, by_counterparty, command.year, command.month, today  # type: ignore[arg-type]
            )
            return RoleReport(role, total_outstanding, counterparties, single_month=month)

        months = self._month_series(role, user_id, by_counterparty, today)
        return RoleReport(role, total_outstanding, counterparties, months=months)

    def _month_series(
        self, role: str, user_id: str, by_counterparty: dict, today: date
    ) -> list[MonthLine]:
        earliest = min(
            entry.entry_date for entries in by_counterparty.values() for entry in entries
        )
        first_period = self._calc.period_containing(earliest)
        current_period = self._calc.period_containing(today)

        months: list[MonthLine] = []
        year, month = first_period.year, first_period.month
        while (year, month) <= (current_period.year, current_period.month):
            line = self._aggregate_month(role, user_id, by_counterparty, year, month, today)
            # Keep months that cost something, plus the still-open current month.
            if line.interest > 0 or not line.is_final:
                months.append(line)
            year, month = self._next_month(year, month)

        return months[-self._config.report_max_months :]

    def _aggregate_month(
        self,
        role: str,
        user_id: str,
        by_counterparty: dict,
        year: int,
        month: int,
        today: date,
    ) -> MonthLine:
        period = self._calc.statement_period(year, month)
        is_final = today > period.end
        total = Decimal("0")
        for counterparty, entries in by_counterparty.items():
            debtor_id, creditor_id = self._relationship(role, user_id, counterparty)
            total += self._relationship_interest(
                debtor_id, creditor_id, entries, year, month, today, is_final
            )
        return MonthLine(
            year=year,
            month=month,
            interest=total.quantize(self._config.money_quantize),
            is_final=is_final,
        )

    def _relationship_interest(
        self,
        debtor_id: str,
        creditor_id: str,
        entries: list[LoanEntry],
        year: int,
        month: int,
        today: date,
        is_final: bool,
    ) -> Decimal:
        """Interest for one debtor->creditor pair in a month, using the cache."""
        if is_final:
            cached = self._repo.get_cached_interest(debtor_id, creditor_id, year, month)
            if cached is not None:
                return cached

        result = self._calc.monthly_interest(entries, year, month, today)
        if is_final:  # a closed month is fixed forever -> safe to cache
            self._repo.save_cached_interest(
                debtor_id, creditor_id, year, month, result.closing_balance, result.interest
            )
        return result.interest

    # -- helpers -------------------------------------------------------------
    def _group_by_counterparty(
        self, role: str, transactions: list[LoanTransaction]
    ) -> dict[str, list[LoanEntry]]:
        grouped: dict[str, list[LoanEntry]] = defaultdict(list)
        for txn in transactions:
            counterparty = (
                txn.creditor_line_id if role == DEBTOR else txn.debtor_line_id
            )
            grouped[counterparty].append(
                LoanEntry(txn.transaction_date, txn.signed_amount, txn.annual_rate)
            )
        return grouped

    @staticmethod
    def _relationship(role: str, user_id: str, counterparty: str) -> tuple[str, str]:
        """Return (debtor_line_id, creditor_line_id) for a relationship."""
        if role == DEBTOR:
            return user_id, counterparty
        return counterparty, user_id

    def _display_name(self, line_user_id: str) -> str | None:
        user = self._repo.get_user(line_user_id)
        return user.display_name if user else None

    def _record(self, event: Event) -> WebhookEvent | None:
        record = WebhookEvent(
            webhook_event_id=event.webhookEventId or "",
            line_user_id=event.source.userId or "",
            event_type=event.type or "",
            line_timestamp=event.event_datetime,
            raw_payload=event.model_dump(),
            message_type=event.message.type if event.message else None,
            line_message_id=event.message.id if event.message else None,
            raw_text=event.message.text if event.message else None,
        )
        return self._repo.record_event(record)

    @staticmethod
    def _is_text_message(event: Event) -> bool:
        return (
            event.type == _MESSAGE
            and event.message is not None
            and event.message.type == _TEXT
            and event.source is not None
            and bool(event.source.userId)
            and bool(event.webhookEventId)
        )

    @staticmethod
    def _mentions(event: Event) -> list[MentionRef]:
        if event.message is None or event.message.mention is None:
            return []
        return [
            MentionRef(m.index, m.length, m.userId, m.isSelf)
            for m in event.message.mention.mentionees
        ]

    @staticmethod
    def _next_month(year: int, month: int) -> tuple[int, int]:
        return (year + 1, 1) if month == 12 else (year, month + 1)

    def _now(self) -> date:
        from datetime import datetime

        return datetime.now(tz=self._config.timezone).date()
