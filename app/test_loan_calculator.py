from __future__ import annotations

import argparse
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path

from openpyxl import Workbook

from loan_calculator import (
    LoanInterestCalculator,
    Transaction,
    calculate_reports,
    load_loan_workbook,
    main,
    month_argument,
    statement_window,
)


class LoanInterestCalculatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calculator = LoanInterestCalculator("0.03")

    def test_statement_window_crosses_year(self) -> None:
        self.assertEqual(
            statement_window(2026, 1),
            (date(2025, 12, 21), date(2026, 1, 20)),
        )

    def test_month_accepts_only_integers_from_1_to_12(self) -> None:
        self.assertEqual(month_argument("1"), 1)
        self.assertEqual(month_argument("12"), 12)
        for invalid_month in ("0", "13", "6.5", "June"):
            with self.subTest(invalid_month=invalid_month):
                with self.assertRaises(argparse.ArgumentTypeError):
                    month_argument(invalid_month)

    def test_repayment_changes_interest_on_same_day(self) -> None:
        result = self.calculator.calculate_month(
            [
                Transaction(date(2026, 5, 10), Decimal("10000")),
                Transaction(date(2026, 5, 12), Decimal("-5000")),
            ],
            2026,
            5,
        )
        # 5/10 and 5/11: 10,000; 5/12 through 5/20: 5,000 (9 days).
        expected = (
            Decimal("10000") * 2 + Decimal("5000") * 9
        ) * Decimal("0.03") / Decimal("365")
        self.assertEqual(
            result.interest_due,
            expected.quantize(Decimal("0.01")),
        )

    def test_loan_on_21st_is_in_next_payable_month(self) -> None:
        transactions = [Transaction(date(2026, 4, 21), Decimal("36500"))]
        may = self.calculator.calculate_month(transactions, 2026, 5)
        april = self.calculator.calculate_month(transactions, 2026, 4)
        self.assertEqual(may.interest_due, Decimal("90.00"))
        self.assertEqual(april.interest_due, Decimal("0.00"))

    def test_multiple_same_day_transactions_are_net_before_interest(self) -> None:
        result = self.calculator.calculate_month(
            [
                Transaction(date(2026, 5, 1), Decimal("10000")),
                Transaction(date(2026, 5, 1), Decimal("-3000")),
                Transaction(date(2026, 5, 1), Decimal("2000")),
            ],
            2026,
            5,
        )
        expected = Decimal("9000") * 20 * Decimal("0.03") / Decimal("365")
        self.assertEqual(result.interest_due, expected.quantize(Decimal("0.01")))

    def test_balance_cannot_be_negative(self) -> None:
        with self.assertRaisesRegex(ValueError, "became negative"):
            self.calculator.calculate_month(
                [Transaction(date(2026, 5, 1), Decimal("-1"))], 2026, 5
            )

    def test_round_only_after_summing_daily_interest(self) -> None:
        result = self.calculator.calculate_month(
            [Transaction(date(2026, 4, 21), Decimal("1"))], 2026, 5
        )
        self.assertEqual(result.interest_due, Decimal("0.00"))


class WorkbookAndReportTests(unittest.TestCase):
    def make_workbook(self, path: Path) -> None:
        workbook = Workbook()
        first = workbook.active
        first.title = "Alice"
        first.append(["Date", "Amount"])
        first.append([date(2026, 5, 10), 10000])
        first.append([date(2026, 5, 12), -5000])
        second = workbook.create_sheet("Bob")
        second.append(["Date", "Amount"])
        workbook.save(path)

    def test_loads_each_sheet_as_a_user_and_preserves_empty_user(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "loan.xlsx"
            self.make_workbook(path)
            users = load_loan_workbook(path)
        self.assertEqual(set(users), {"Alice", "Bob"})
        self.assertEqual(users["Alice"][1].amount, Decimal("-5000"))
        self.assertEqual(users["Bob"], [])

    def test_default_report_contains_all_months_and_users(self) -> None:
        users = {
            "Alice": [Transaction(date(2026, 5, 10), Decimal("10000"))],
            "Bob": [],
        }
        reports = calculate_reports(users, "0.03")
        self.assertEqual(len(reports["Alice"]), 12)
        self.assertEqual(len(reports["Bob"]), 12)
        self.assertEqual(reports["Bob"][4].interest_due, Decimal("0.00"))

    def test_month_and_user_filters(self) -> None:
        users = {
            "Alice": [Transaction(date(2026, 5, 10), Decimal("10000"))],
            "Bob": [],
        }
        reports = calculate_reports(users, "0.03", month=5, user="Alice")
        self.assertEqual(list(reports), ["Alice"])
        self.assertEqual([(item.year, item.month) for item in reports["Alice"]], [(2026, 5)])

    def test_unknown_user_is_rejected(self) -> None:
        users = {"Alice": [Transaction(date(2026, 5, 10), Decimal("1"))]}
        with self.assertRaisesRegex(ValueError, "does not exist"):
            calculate_reports(users, "0.03", user="Nobody")

    def test_cli_reads_excel_and_prints_filtered_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "loan.xlsx"
            self.make_workbook(path)
            output = StringIO()
            with redirect_stdout(output):
                return_code = main(
                    ["--input", str(path), "--rate", "0.03", "--month", "5", "--user", "Alice"]
                )
        self.assertEqual(return_code, 0)
        self.assertIn("使用者: Alice", output.getvalue())
        self.assertIn("2026年05月應付利息: 5.34", output.getvalue())
        self.assertNotIn("Bob", output.getvalue())


class DebtorSheetTests(unittest.TestCase):
    """A sheet with a Debtor column is split into one user per Debtor."""

    def make_workbook(self, path: Path) -> None:
        workbook = Workbook()
        pooled = workbook.active
        pooled.title = "CK2"
        pooled.append(["Date", "Amount", "Debtor"])
        pooled.append([date(2026, 5, 10), 10000, "Olivia"])
        pooled.append([date(2026, 5, 11), 20000, "Allen"])
        pooled.append([date(2026, 5, 12), -5000, "Olivia"])
        legacy = workbook.create_sheet("Bob(CK3)")
        legacy.append(["Date", "Amount"])
        legacy.append([date(2026, 5, 10), 1000])
        workbook.save(path)

    def load(self, sheet: str | None = None) -> dict[str, list[Transaction]]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "loan.xlsx"
            self.make_workbook(path)
            return load_loan_workbook(path, sheet)

    def test_debtor_column_splits_sheet_into_users(self) -> None:
        users = self.load("CK2")
        self.assertEqual(set(users), {"Olivia", "Allen"})
        self.assertEqual([t.amount for t in users["Olivia"]], [Decimal("10000"), Decimal("-5000")])
        self.assertEqual([t.amount for t in users["Allen"]], [Decimal("20000")])

    def test_sheet_without_debtor_column_is_one_user_named_after_sheet(self) -> None:
        self.assertEqual(set(self.load()), {"Olivia", "Allen", "Bob(CK3)"})

    def test_unknown_sheet_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "sheet does not exist"):
            self.load("Nope")

    def test_blank_debtor_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "loan.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["Date", "Amount", "Debtor"])
            sheet.append([date(2026, 5, 10), 10000, None])
            workbook.save(path)
            with self.assertRaisesRegex(ValueError, "Debtor is required"):
                load_loan_workbook(path)

    def test_each_debtor_is_calculated_independently(self) -> None:
        reports = calculate_reports(self.load("CK2"), "0.03", month=5)
        olivia = reports["Olivia"][0]
        allen = reports["Allen"][0]
        # Olivia: 5/10, 5/11 at 10,000; 5/12-5/20 (9 days) at 5,000.
        expected_olivia = (Decimal("10000") * 2 + Decimal("5000") * 9) * Decimal("0.03") / Decimal("365")
        # Allen: 5/11-5/20 (10 days) at 20,000.
        expected_allen = Decimal("20000") * 10 * Decimal("0.03") / Decimal("365")
        self.assertEqual(olivia.interest_due, expected_olivia.quantize(Decimal("0.01")))
        self.assertEqual(allen.interest_due, expected_allen.quantize(Decimal("0.01")))
        self.assertEqual(olivia.outstanding_balance, Decimal("5000.00"))
        self.assertEqual(allen.outstanding_balance, Decimal("20000.00"))


class OutstandingBalanceTests(unittest.TestCase):
    def test_outstanding_balance_is_balance_at_end_of_statement_window(self) -> None:
        calculator = LoanInterestCalculator("0.03")
        transactions = [
            Transaction(date(2026, 4, 10), Decimal("10000")),
            Transaction(date(2026, 5, 20), Decimal("-4000")),
            Transaction(date(2026, 5, 21), Decimal("500")),
        ]
        may = calculator.calculate_month(transactions, 2026, 5)
        june = calculator.calculate_month(transactions, 2026, 6)
        self.assertEqual(may.outstanding_balance, Decimal("6000.00"))
        self.assertEqual(june.outstanding_balance, Decimal("6500.00"))

    def test_negative_balance_allowed_accrues_no_interest_and_is_reported(self) -> None:
        calculator = LoanInterestCalculator("0.03", allow_negative_balance=True)
        transactions = [
            Transaction(date(2026, 5, 10), Decimal("-1000")),
            Transaction(date(2026, 5, 15), Decimal("3000")),
        ]
        may = calculator.calculate_month(transactions, 2026, 5)
        # 5/10-5/14 balance -1,000 -> 0 interest; 5/15-5/20 (6 days) at 2,000.
        expected = Decimal("2000") * 6 * Decimal("0.03") / Decimal("365")
        self.assertEqual(may.interest_due, expected.quantize(Decimal("0.01")))
        self.assertEqual(may.outstanding_balance, Decimal("2000.00"))

    def test_report_keeps_negative_balance_instead_of_raising(self) -> None:
        users = {"Olivia": [Transaction(date(2026, 5, 10), Decimal("-100"))]}
        reports = calculate_reports(users, "0.03", month=5)
        self.assertEqual(reports["Olivia"][0].interest_due, Decimal("0.00"))
        self.assertEqual(reports["Olivia"][0].outstanding_balance, Decimal("-100.00"))


if __name__ == "__main__":
    unittest.main()
