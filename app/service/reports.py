"""Structured results returned by a ``check`` query.

Kept free of any rendering so the service can build them and the formatter can
turn them into LINE text independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

# The two sides a user can be on for a given relationship.
DEBTOR = "debtor"
CREDITOR = "creditor"


@dataclass
class MonthLine:
    year: int
    month: int
    interest: Decimal
    is_final: bool  # False -> still accruing (current statement month)


@dataclass
class CounterpartyLine:
    line_id: str
    display_name: str | None
    outstanding: Decimal


@dataclass
class RoleReport:
    role: str  # DEBTOR or CREDITOR
    total_outstanding: Decimal
    counterparties: list[CounterpartyLine] = field(default_factory=list)
    months: list[MonthLine] = field(default_factory=list)  # all-months view
    single_month: MonthLine | None = None  # single-month view


@dataclass
class CheckReport:
    line_user_id: str
    display_name: str | None
    as_of: date
    debtor: RoleReport | None = None  # user owes others
    creditor: RoleReport | None = None  # others owe user
    single_month: tuple[int, int] | None = None  # (year, month) scope, if any

    @property
    def has_activity(self) -> bool:
        return self.debtor is not None or self.creditor is not None
