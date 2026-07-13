"""Interest calculation.

Rules implemented (from the spec):

* Daily interest = (annual_rate / 365) * outstanding balance **at the close of
  that day**.  A borrow and repay on the same day net out before interest is
  taken, so money that is borrowed and returned same-day costs nothing.
* A statement month runs from the 21st of the previous month to the 20th of the
  payable month (June's bill covers 05/21 .. 06/20, due 06/21).
* Balances carrying different annual rates must be summed **per rate** and only
  then added together -- never blended into one balance.
* Rounding to cents happens once, after every daily amount has been summed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from app.config import Settings, settings


@dataclass(frozen=True)
class LoanEntry:
    """A dated, signed, rated movement fed into the calculator."""

    entry_date: date
    signed_amount: Decimal
    annual_rate: Decimal


@dataclass(frozen=True)
class StatementPeriod:
    year: int
    month: int
    start: date
    end: date

    @property
    def label(self) -> str:
        return f"{self.year}/{self.month:02d}"


@dataclass(frozen=True)
class MonthlyInterest:
    period: StatementPeriod
    closing_balance: Decimal
    interest: Decimal
    is_final: bool  # False while the statement window is still open (partial)


class InterestCalculator:
    """Pure interest math driven entirely by :class:`Settings`."""

    def __init__(self, config: Settings = settings) -> None:
        self._days_per_year = config.days_per_year
        self._quantize = config.money_quantize
        self._start_day = config.statement_start_day
        self._end_day = config.statement_end_day

    # -- period helpers ------------------------------------------------------
    def statement_period(self, year: int, month: int) -> StatementPeriod:
        """The 21st-to-20th window whose interest is payable in ``year/month``."""
        if not 1 <= month <= 12:
            raise ValueError("month must be between 1 and 12")
        prev_year = year if month > 1 else year - 1
        prev_month = month - 1 if month > 1 else 12
        return StatementPeriod(
            year=year,
            month=month,
            start=date(prev_year, prev_month, self._start_day),
            end=date(year, month, self._end_day),
        )

    def period_containing(self, day: date) -> StatementPeriod:
        """The statement period that ``day`` falls inside."""
        if day.day >= self._start_day:
            # On/after the 21st we are already accruing next month's bill.
            year = day.year + 1 if day.month == 12 else day.year
            month = 1 if day.month == 12 else day.month + 1
            return self.statement_period(year, month)
        return self.statement_period(day.year, day.month)

    # -- balances ------------------------------------------------------------
    def outstanding_as_of(self, entries: Iterable[LoanEntry], as_of: date) -> Decimal:
        """Principal still owed on ``as_of`` (rate independent)."""
        total = sum(
            (e.signed_amount for e in entries if e.entry_date <= as_of),
            Decimal("0"),
        )
        return self._round(total)

    # -- interest ------------------------------------------------------------
    def monthly_interest(
        self, entries: Iterable[LoanEntry], year: int, month: int, today: date
    ) -> MonthlyInterest:
        """Interest for one statement month.

        If the window is still open (``today`` <= its 20th) only the days up to
        ``today`` are accrued and ``is_final`` is False -- such a figure must not
        be cached because it can still change.
        """
        entries = list(entries)
        period = self.statement_period(year, month)
        accrual_end = min(period.end, today)
        interest = self._accrue(entries, period.start, accrual_end)
        return MonthlyInterest(
            period=period,
            closing_balance=self.outstanding_as_of(entries, period.end),
            interest=self._round(interest),
            is_final=today > period.end,
        )

    def _accrue(
        self, entries: list[LoanEntry], start: date, end: date
    ) -> Decimal:
        """Sum daily interest across ``[start, end]``, grouping by rate."""
        if end < start:
            return Decimal("0")
        total = Decimal("0")
        for rate, group in self._group_by_rate(entries).items():
            total += self._accrue_single_rate(group, rate, start, end)
        return total

    def _accrue_single_rate(
        self, entries: list[LoanEntry], rate: Decimal, start: date, end: date
    ) -> Decimal:
        daily_rate = rate / self._days_per_year
        balance = sum(
            (e.signed_amount for e in entries if e.entry_date < start),
            Decimal("0"),
        )
        changes = self._changes_by_day(entries, start, end)

        interest = Decimal("0")
        current = start
        while current <= end:
            balance += changes.get(current, Decimal("0"))
            # A debtor never earns interest on an over-repaid (negative) balance.
            if balance > 0:
                interest += balance * daily_rate
            current += timedelta(days=1)
        return interest

    @staticmethod
    def _group_by_rate(entries: list[LoanEntry]) -> dict[Decimal, list[LoanEntry]]:
        grouped: dict[Decimal, list[LoanEntry]] = defaultdict(list)
        for entry in entries:
            grouped[entry.annual_rate].append(entry)
        return grouped

    @staticmethod
    def _changes_by_day(
        entries: list[LoanEntry], start: date, end: date
    ) -> dict[date, Decimal]:
        changes: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
        for entry in entries:
            if start <= entry.entry_date <= end:
                changes[entry.entry_date] += entry.signed_amount
        return changes

    def _round(self, value: Decimal) -> Decimal:
        return value.quantize(self._quantize, rounding=ROUND_HALF_UP)
