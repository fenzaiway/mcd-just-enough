"""把方案渲染成终端报表或 Markdown 账单。

中英混排时按东亚字符宽度对齐，避免表格在终端里错位。
"""

from __future__ import annotations

import unicodedata

from .planner import Plan


def _width(text: str) -> int:
    total = 0
    for char in text:
        total += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return total


def _pad(text: str, width: int, align: str = "left") -> str:
    gap = max(width - _width(text), 0)
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def _table(headers: list[str], rows: list[list[str]], aligns: list[str] | None = None) -> list[str]:
    aligns = aligns or ["left"] * len(headers)
    widths = [_width(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], _width(cell))
    line = "┌" + "┬".join("─" * (w + 2) for w in widths) + "┐"
    out = [line]
    out.append("│ " + " │ ".join(_pad(h, w) for h, w in zip(headers, widths)) + " │")
    out.append("├" + "┼".join("─" * (w + 2) for w in widths) + "┤")
    for row in rows:
        out.append(
            "│ "
            + " │ ".join(_pad(cell, w, a) for cell, w, a in zip(row, widths, aligns))
            + " │"
        )
    out.append("└" + "┴".join("─" * (w + 2) for w in widths) + "┘")
    return out


def _bar(plan: Plan, width: int = 46) -> str:
    top = max(plan.target.hi * 1.35, plan.kcal * 1.1)
    if top <= 0:
        return ""
    filled = int(round(min(plan.kcal / top, 1.0) * width))
    lo_pos = int(round(min(plan.target.lo / top, 1.0) * width))
    hi_pos = int(round(min(plan.target.hi / top, 1.0) * width))
    cells = ["·"] * width
    for index in range(lo_pos, hi_pos):
        cells[index] = "─"
    for index in range(min(filled, width)):
        cells[index] = "█"
    return "".join(cells)


def render_plan(plan: Plan, *, title: str = "麦门刚好 · 点餐方案") -> str:
    lines: list[str] = []
    lines.append(f"🍟 {title}")
    lines.append("=" * 56)
    lines.append(
        f"就餐 {plan.headcount} 人 | 目标 {plan.target.describe()} | 方案 {plan.kcal:.0f} kcal | 判定 {plan.verdict}"
    )
    lines.append(f"[{_bar(plan)}]  ▏区间 {plan.target.lo:.0f}–{plan.target.hi:.0f}")
    lines.append("")
    lines.append("分组")
    for group in plan.groups:
        lines.append(
            f"  · {group.group.label}: {group.kcal:.0f} kcal（目标 {group.target.describe()}，"
            f"人均 {group.per_person_kcal:.0f} kcal）"
        )
    lines.append("")
    rows = []
    for line in plan.lines:
        rows.append(
            [
                line.item.name,
                str(line.qty),
                f"{line.item.price:.2f}",
                f"{line.subtotal:.2f}",
                f"{line.kcal:.0f}",
            ]
        )
    lines.extend(_table(["餐品", "数量", "单价", "小计", "kcal"], rows, ["left", "right", "right", "right", "right"]))
    lines.append("")
    lines.append(f"菜品小计 {plan.quote.subtotal:.2f} 元 | 优惠 -{plan.quote.discount:.2f} 元", )
    if plan.quote.delivery_fee:
        lines.append(f"配送费 {plan.quote.delivery_fee:.2f} 元")
    lines.append(
        f"实付 {plan.quote.payable:.2f} 元 | 人均 {plan.per_person_price:.2f} 元 | 价格来源 {plan.quote.source}"
    )
    lines.append("")
    lines.append("说明")
    for note in plan.notes:
        lines.append(f"  - {note}")
    return "\n".join(lines)


def render_markdown(plan: Plan, *, title: str = "麦门刚好 · 点餐方案") -> str:
    lines = [f"### {title}", ""]
    lines.append(
        f"**{plan.headcount} 人** ｜ 目标 **{plan.target.describe()}** ｜ 方案 **{plan.kcal:.0f} kcal** ｜ 判定 **{plan.verdict}**"
    )
    lines.append("")
    lines.append("| 餐品 | 数量 | 单价 | 小计 | kcal |")
    lines.append("|---|---:|---:|---:|---:|")
    for line in plan.lines:
        lines.append(
            f"| {line.item.name} | {line.qty} | {line.item.price:.2f} | {line.subtotal:.2f} | {line.kcal:.0f} |"
        )
    lines.append("")
    lines.append(
        f"实付 **{plan.quote.payable:.2f} 元**（人均 {plan.per_person_price:.2f} 元，优惠 {plan.quote.discount:.2f} 元）"
    )
    lines.append("")
    for note in plan.notes:
        lines.append(f"- {note}")
    return "\n".join(lines)
