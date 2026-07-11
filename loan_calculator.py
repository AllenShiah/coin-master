from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel


MONEY_QUANTIZE = Decimal("0.01")
DAYS_PER_YEAR = Decimal("365")


@dataclass(frozen=True)
class Transaction:
    occurred_on: date
    amount: Decimal


@dataclass(frozen=True)
class MonthlyInterest:
    year: int
    month: int
    statement_start: date
    statement_end: date
    interest_due: Decimal


def statement_window(year: int, month: int) -> tuple[date, date]:
    """Return the 21st-to-20th statement period payable in year/month."""
    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    previous_year = year if month > 1 else year - 1
    previous_month = month - 1 if month > 1 else 12
    return date(previous_year, previous_month, 21), date(year, month, 20)


class LoanInterestCalculator:
    """Calculate interest using each day's closing loan balance."""

    def __init__(self, annual_rate: Decimal | str | float) -> None:
        try:
            rate = Decimal(str(annual_rate))
        except InvalidOperation as exc:
            raise ValueError("annual rate must be a number") from exc
        if not rate.is_finite() or rate < 0:
            raise ValueError("annual rate must be a finite, non-negative decimal")

        self.annual_rate = rate
        self.daily_rate = rate / DAYS_PER_YEAR

    def calculate_month(
        self,
        transactions: Iterable[Transaction],
        year: int,
        month: int,
    ) -> MonthlyInterest:
        ordered = sorted(transactions, key=lambda transaction: transaction.occurred_on)
        self._validate_balances(ordered)
        start, end = statement_window(year, month)

        balance = sum(
            (transaction.amount for transaction in ordered if transaction.occurred_on < start),
            Decimal("0"),
        )
        changes_by_day: dict[date, Decimal] = {}
        for transaction in ordered:
            if start <= transaction.occurred_on <= end:
                changes_by_day[transaction.occurred_on] = (
                    changes_by_day.get(transaction.occurred_on, Decimal("0"))
                    + transaction.amount
                )

        interest = Decimal("0")
        current_day = start
        while current_day <= end:
            # Same-day loans and repayments affect that day's interest.
            balance += changes_by_day.get(current_day, Decimal("0"))
            interest += balance * self.daily_rate
            current_day += timedelta(days=1)

        return MonthlyInterest(
            year=year,
            month=month,
            statement_start=start,
            statement_end=end,
            interest_due=interest.quantize(MONEY_QUANTIZE, rounding=ROUND_HALF_UP),
        )

    @staticmethod
    def _validate_balances(transactions: list[Transaction]) -> None:
        balance = Decimal("0")
        changes_by_day: dict[date, Decimal] = {}
        for transaction in transactions:
            changes_by_day[transaction.occurred_on] = (
                changes_by_day.get(transaction.occurred_on, Decimal("0"))
                + transaction.amount
            )
        for occurred_on in sorted(changes_by_day):
            balance += changes_by_day[occurred_on]
            if balance < 0:
                raise ValueError(
                    f"loan balance became negative on {occurred_on.isoformat()}"
                )


def load_loan_workbook(path: str | Path) -> dict[str, list[Transaction]]:
    workbook_path = Path(path)
    if not workbook_path.is_file():
        raise ValueError(f"Excel file does not exist: {workbook_path}")

    workbook = load_workbook(workbook_path, data_only=True, read_only=True)
    users: dict[str, list[Transaction]] = {}
    try:
        for worksheet in workbook.worksheets:
            headers = _header_columns(worksheet)
            missing = {"date", "amount"} - headers.keys()
            if missing:
                names = ", ".join(sorted(missing))
                raise ValueError(f"sheet '{worksheet.title}' is missing columns: {names}")

            transactions: list[Transaction] = []
            for row_number, row in enumerate(
                worksheet.iter_rows(min_row=2, values_only=True), start=2
            ):
                raw_date = row[headers["date"]] if headers["date"] < len(row) else None
                raw_amount = row[headers["amount"]] if headers["amount"] < len(row) else None
                if raw_date is None and raw_amount is None:
                    continue
                if raw_date is None or raw_amount is None:
                    raise ValueError(
                        f"sheet '{worksheet.title}', row {row_number}: "
                        "Date and Amount are both required"
                    )
                transactions.append(
                    Transaction(
                        occurred_on=_parse_excel_date(
                            raw_date, workbook.epoch, worksheet.title, row_number
                        ),
                        amount=_parse_excel_amount(
                            raw_amount, worksheet.title, row_number
                        ),
                    )
                )
            users[worksheet.title] = transactions
    finally:
        workbook.close()
    return users


def _header_columns(worksheet: object) -> dict[str, int]:
    first_row = next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
    return {
        str(value).strip().lower(): index
        for index, value in enumerate(first_row)
        if value is not None
    }


def _parse_excel_date(raw: object, epoch: datetime, sheet: str, row: int) -> date:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, (int, float, Decimal)) and not isinstance(raw, bool):
        converted = from_excel(raw, epoch)
        return converted.date() if isinstance(converted, datetime) else converted
    if isinstance(raw, str):
        text = raw.strip()
        for date_format in ("%Y/%m/%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, date_format).date()
            except ValueError:
                pass
    raise ValueError(f"sheet '{sheet}', row {row}: invalid Date value {raw!r}")


def _parse_excel_amount(raw: object, sheet: str, row: int) -> Decimal:
    if isinstance(raw, bool):
        raise ValueError(f"sheet '{sheet}', row {row}: invalid Amount value {raw!r}")
    try:
        amount = Decimal(str(raw).replace(",", "").strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(
            f"sheet '{sheet}', row {row}: invalid Amount value {raw!r}"
        ) from exc
    if not amount.is_finite():
        raise ValueError(f"sheet '{sheet}', row {row}: Amount must be finite")
    return amount


def calculate_reports(
    users: dict[str, list[Transaction]],
    annual_rate: Decimal | str | float,
    month: int | None = None,
    user: str | None = None,
) -> dict[str, list[MonthlyInterest]]:
    if month is not None and not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    if user is not None:
        if user not in users:
            raise ValueError(f"user does not exist: {user}")
        selected_users = {user: users[user]}
    else:
        selected_users = users

    all_transactions = [item for items in users.values() for item in items]
    if not all_transactions:
        raise ValueError("the workbook contains no transactions")
    years = range(
        min(item.occurred_on.year for item in all_transactions),
        max(item.occurred_on.year for item in all_transactions) + 1,
    )
    months = (month,) if month is not None else range(1, 13)
    calculator = LoanInterestCalculator(annual_rate)

    return {
        username: [
            calculator.calculate_month(transactions, year, report_month)
            for year in years
            for report_month in months
        ]
        for username, transactions in selected_users.items()
    }


def month_argument(value: str) -> int:
    try:
        month = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("month must be an integer from 1 to 12") from exc
    if not 1 <= month <= 12:
        raise argparse.ArgumentTypeError("month must be an integer from 1 to 12")
    return month


def rate_argument(value: str) -> Decimal:
    try:
        rate = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("rate must be a number") from exc
    if not rate.is_finite() or rate < 0:
        raise argparse.ArgumentTypeError("rate must be a non-negative decimal")
    return rate


def format_money(value: Decimal) -> str:
    return f"{value.quantize(MONEY_QUANTIZE, rounding=ROUND_HALF_UP):,.2f}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="依 Excel 借還款紀錄，計算每月（上月 21 日至本月 20 日）應付利息。"
    )
    parser.add_argument(
        "--input", default="loan.xlsx", help="Excel 路徑（預設：loan.xlsx）"
    )
    parser.add_argument(
        "--rate",
        required=True,
        type=rate_argument,
        help="年利率（小數格式），例如 0.03 代表 3%%",
    )
    parser.add_argument(
        "--month", type=month_argument, help="只顯示指定月份（1 至 12）"
    )
    parser.add_argument("--user", help="只顯示指定的 sheet 使用者")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        users = load_loan_workbook(args.input)
        reports = calculate_reports(users, args.rate, args.month, args.user)
    except ValueError as exc:
        parser.error(str(exc))

    for user_index, (username, monthly_results) in enumerate(reports.items()):
        if user_index:
            print()
        print(f"使用者: {username}")
        for result in monthly_results:
            print(
                f"{result.year}年{result.month:02d}月應付利息: "
                f"{format_money(result.interest_due)}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
