"""Interest-calculation business constants.

A monthly statement runs from the 21st of the previous month to the 20th of the
payable month (e.g. June interest = 05/21 .. 06/20).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.util.constants import DAYS_PER_YEAR, MONEY_QUANTIZE


@dataclass(frozen=True)
class InterestSettings:
    days_per_year: Decimal = field(default=DAYS_PER_YEAR)
    money_quantize: Decimal = field(default=MONEY_QUANTIZE)
    statement_start_day: int = field(default=21)
    statement_end_day: int = field(default=20)


settings = InterestSettings()
