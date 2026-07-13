"""Render domain results into the plain-text replies LINE will show.

All user-facing copy lives here (Traditional Chinese) so wording changes never
touch business logic.
"""

from __future__ import annotations

from decimal import Decimal

from app.service.reports import CREDITOR, DEBTOR, CheckReport, MonthLine, RoleReport
from app.util.domain import BORROW, LoanTransaction
from app.util.parser import FORMAT_EXAMPLE


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _percent(rate: Decimal) -> str:
    text = format((rate * Decimal("100")).normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text}%"


def _month_label(year: int, month: int) -> str:
    return f"{year}年{month:02d}月"


def _name(display_name: str | None, line_id: str) -> str:
    return display_name or line_id


# -- register ---------------------------------------------------------------
def format_register(transaction: LoanTransaction) -> str:
    action = "借款" if transaction.transaction_type == BORROW else "還款"
    creditor = _name(None, transaction.creditor_line_id)
    return (
        "✅ 已記錄\n"
        f"您向 {creditor} {action} {_money(transaction.magnitude)} 元\n"
        f"日期：{transaction.transaction_date:%Y/%m/%d}"
        f"｜年利率：{_percent(transaction.annual_rate)}"
    )


# -- format error -----------------------------------------------------------
def format_format_error(reason: str) -> str:
    return (
        "⚠️ 訊息格式不符\n"
        f"{reason}\n"
        "正確格式範例：\n"
        f"{FORMAT_EXAMPLE}\n"
        "查詢請輸入：check（或 check 2026/06）"
    )


# -- check ------------------------------------------------------------------
def format_check(report: CheckReport) -> str:
    if not report.has_activity:
        return "您目前沒有任何借貸紀錄。"

    lines = [f"📊 查詢結果（截至 {report.as_of:%Y/%m/%d}）"]
    if report.single_month is not None:
        year, month = report.single_month
        lines.append(f"查詢月份：{_month_label(year, month)}")

    if report.debtor is not None:
        lines.append("")
        lines.extend(_render_role(report.debtor))
    if report.creditor is not None:
        lines.append("")
        lines.extend(_render_role(report.creditor))
    return "\n".join(lines)


def _render_role(role: RoleReport) -> list[str]:
    if role.role == DEBTOR:
        header = "—— 您的借款（您是借入人）——"
        outstanding_label = "目前欠款總額"
        interest_label = "應付利息"
    else:
        header = "—— 他人向您借款（您是債權人）——"
        outstanding_label = "目前應收總額"
        interest_label = "應收利息"

    lines = [header, f"{outstanding_label}：{_money(role.total_outstanding)} 元"]
    lines.extend(_render_counterparties(role))
    lines.extend(_render_interest(role, interest_label))
    return lines


def _render_counterparties(role: RoleReport) -> list[str]:
    if len(role.counterparties) <= 1:
        return []
    verb = "欠" if role.role == DEBTOR else "應收"
    lines = ["明細："]
    for party in role.counterparties:
        name = _name(party.display_name, party.line_id)
        lines.append(f"・{verb} {name}：{_money(party.outstanding)} 元")
    return lines


def _render_interest(role: RoleReport, interest_label: str) -> list[str]:
    if role.single_month is not None:
        return [f"{interest_label}：{_money(role.single_month.interest)} 元"]

    if not role.months:
        return [f"各期{interest_label}：無"]

    lines = [f"各期{interest_label}："]
    for month in role.months:
        lines.append(_render_month(month))
    return lines


def _render_month(month: MonthLine) -> str:
    base = f"・{_month_label(month.year, month.month)}：{_money(month.interest)} 元"
    if not month.is_final:
        base += "（本期累計中，尚未結算）"
    return base
