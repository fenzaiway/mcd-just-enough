"""组合求解：在「够吃」和「不浪费」之间找一组可复核的订单。

两阶段设计（这点很关键）：

1. **营养阶段**：只用本地菜单 + 营养数据枚举组合，按能量区间打分排序，
   完全不碰网络，几千种组合也是毫秒级；
2. **核价阶段**：只把排名前 ``price_top`` 个方案交给 ``calculate-price``
   做真实核价（含券、配送费），避免无意义地打接口触发 600 次/分钟限流。

最终方案会输出完整账单与「为什么不是别的方案」的对比，便于人工复核。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .catalog import CatalogProvider, Coupon, MenuItem, Quote
from .portion import DinerGroup, Target, target_for, total_target, waste_penalty


@dataclass(frozen=True)
class Line:
    item: MenuItem
    qty: int = 1

    @property
    def kcal(self) -> float:
        return (self.item.kcal or 0.0) * self.qty

    @property
    def subtotal(self) -> float:
        return self.item.price * self.qty


@dataclass
class GroupPlan:
    group: DinerGroup
    lines: list[Line]
    kcal: float
    target: Target

    @property
    def per_person_kcal(self) -> float:
        return self.kcal / self.group.count


@dataclass
class Plan:
    groups: list[GroupPlan]
    lines: list[Line]
    kcal: float
    target: Target
    quote: Quote
    coupon: Coupon | None = None
    notes: list[str] = field(default_factory=list)
    baseline_quote: Quote | None = None
    baseline_kcal: float = 0.0
    nutrition_coverage: float = 1.0

    @property
    def headcount(self) -> int:
        return sum(group.group.count for group in self.groups)

    @property
    def payable(self) -> float:
        return self.quote.payable

    @property
    def per_person_price(self) -> float:
        return self.payable / max(self.headcount, 1)

    @property
    def surplus_kcal(self) -> float:
        return max(0.0, self.kcal - self.target.hi)

    @property
    def deficit_kcal(self) -> float:
        return max(0.0, self.target.lo - self.kcal)

    @property
    def verdict(self) -> str:
        # 落在区间外一点点（<2%）不值得报警，否则「差 5 kcal 判偏少」很反直觉
        slack = 0.02 * max(self.target.mid, 1.0)
        if self.deficit_kcal > slack:
            return "偏少"
        if self.surplus_kcal > slack:
            return "偏多"
        return "刚好"

    def to_dict(self) -> dict:
        return {
            "headcount": self.headcount,
            "target_kcal": {"lo": self.target.lo, "mid": self.target.mid, "hi": self.target.hi},
            "planned_kcal": round(self.kcal, 1),
            "per_person_kcal": round(self.kcal / max(self.headcount, 1), 1),
            "verdict": self.verdict,
            "nutrition_coverage": round(self.nutrition_coverage, 2),
            "groups": [
                {
                    "label": group.group.label,
                    "people": group.group.count,
                    "target_kcal": group.target.describe(),
                    "planned_kcal": round(group.kcal, 1),
                    "items": [
                        {"code": line.item.code, "name": line.item.name, "qty": line.qty, "kcal": line.kcal}
                        for line in group.lines
                    ],
                }
                for group in self.groups
            ],
            "items": [
                {
                    "code": line.item.code,
                    "name": line.item.name,
                    "qty": line.qty,
                    "unit_price": line.item.price,
                    "subtotal": round(line.subtotal, 2),
                    "kcal": line.kcal,
                }
                for line in self.lines
            ],
            "price": {
                "source": self.quote.source,
                "subtotal": self.quote.subtotal,
                "discount": self.quote.discount,
                "delivery_fee": self.quote.delivery_fee,
                "payable": self.quote.payable,
                "per_person": round(self.per_person_price, 2),
                "coupon": self.quote.coupon_name,
            },
            "baseline": None
            if self.baseline_quote is None
            else {
                "payable": self.baseline_quote.payable,
                "kcal": round(self.baseline_kcal, 1),
                "saving": round(self.baseline_quote.payable - self.quote.payable, 2),
            },
            "notes": self.notes,
        }


# --------------------------------------------------------------------- 内部


def _side_options(group: DinerGroup, sides: Sequence[MenuItem]) -> list[tuple[MenuItem | None, int]]:
    """配菜可以共享：两人分一份薯条是常态，这里把它变成可选项。"""
    options: list[tuple[MenuItem | None, int]] = [(None, 0)]
    for side in sides:
        qty = (group.count + 1) // 2 if side.shared and group.count > 1 else group.count
        options.append((side, qty))
    return options


def _drink_options(group: DinerGroup, drinks: Sequence[MenuItem]) -> list[tuple[MenuItem | None, int]]:
    return [(None, 0)] + [(drink, group.count) for drink in drinks]


def _group_candidates(
    group: DinerGroup, mains: Sequence[MenuItem], sides: Sequence[MenuItem], drinks: Sequence[MenuItem]
) -> list[tuple[float, float, list[Line], float]]:
    target = target_for(group)
    candidates: list[tuple[float, float, list[Line], float]] = []
    for main in mains:
        if main.conflicts(group.avoid):
            continue
        main_line = Line(main, group.count)
        for side, side_qty in _side_options(group, sides):
            if side is not None and side.conflicts(group.avoid):
                continue
            for drink, drink_qty in _drink_options(group, drinks):
                if drink is not None and drink.conflicts(group.avoid):
                    continue
                lines = [main_line]
                if side is not None and side_qty:
                    lines.append(Line(side, side_qty))
                if drink is not None and drink_qty:
                    lines.append(Line(drink, drink_qty))
                kcal = sum(line.kcal for line in lines)
                cost = sum(line.subtotal for line in lines)
                penalty = waste_penalty(kcal, target)
                # 价格只作为打破平局的软约束：每多花 20 元/人 加 0.1 分
                penalty += 0.1 * (cost / max(group.count, 1)) / 20.0
                candidates.append((penalty, cost, lines, kcal))
    candidates.sort(key=lambda row: (row[0], row[1]))
    if not candidates:
        raise ValueError(
            f"{group.label} 在忌口条件下没有任何可组合的餐品，请放松忌口条件或补充菜单数据。"
        )
    return candidates


def _merge_lines(groups: Iterable[GroupPlan]) -> list[Line]:
    merged: dict[str, Line] = {}
    for group in groups:
        for line in group.lines:
            existing = merged.get(line.item.code)
            if existing:
                merged[line.item.code] = Line(existing.item, existing.qty + line.qty)
            else:
                merged[line.item.code] = line
    return list(merged.values())


def _pick_coupon(coupons: Sequence[Coupon], subtotal: float) -> Coupon | None:
    usable = [c for c in coupons if c.applies(subtotal) and c.discount > 0]
    return max(usable, key=lambda c: c.discount) if usable else None


def _baseline(
    plan_groups: list[GroupPlan], provider: CatalogProvider, coupons: Sequence[Coupon]
) -> tuple[Quote, float]:
    """对照组：人手一份主食 + 配菜 + 饮料（不共享、不合并）。"""
    lines: list[Line] = []
    kcal = 0.0
    for group in plan_groups:
        main = next((line for line in group.lines if line.item.category == "main"), None)
        if main is None:
            continue
        lines.append(Line(main.item, group.group.count))
        kcal += main.item.kcal * group.group.count
        side = next((line for line in group.lines if line.item.category == "side"), None)
        if side is not None:
            lines.append(Line(side.item, group.group.count))
            kcal += side.item.kcal * group.group.count
        drink = next((line for line in group.lines if line.item.category == "drink"), None)
        if drink is not None:
            lines.append(Line(drink.item, group.group.count))
            kcal += drink.item.kcal * group.group.count
    merged: dict[str, Line] = {}
    for line in lines:
        merged[line.item.code] = (
            Line(line.item, merged[line.item.code].qty + line.qty) if line.item.code in merged else line
        )
    line_list = list(merged.values())
    subtotal = sum(line.subtotal for line in line_list)
    coupon = _pick_coupon(coupons, subtotal)
    return provider.price(line_list, coupon), kcal


# --------------------------------------------------------------------- 对外入口


def build_plan(
    groups: Sequence[DinerGroup],
    provider: CatalogProvider,
    *,
    store_code: str | None = None,
    budget: float | None = None,
    top_per_group: int = 6,
    price_top: int = 6,
) -> Plan:
    """求解一份「刚好够」的订单。"""
    if not groups:
        raise ValueError("至少需要一组就餐者")

    menu = [item for item in provider.menu(store_code) if item.has_nutrition]
    if not menu:
        raise ValueError("菜单里没有带营养数据的餐品，无法做份量建模。请检查数据源或营养接口。")

    mains = [item for item in menu if item.category == "main"]
    sides = [item for item in menu if item.category == "side"]
    drinks = [item for item in menu if item.category == "drink"]
    if not mains:
        raise ValueError("菜单里没有识别出主食/汉堡类餐品。")

    per_group = [_group_candidates(group, mains, sides, drinks)[:top_per_group] for group in groups]
    target_all = total_target(list(groups))
    coupons = provider.coupons(store_code)

    combos: list[tuple[float, float, list[GroupPlan]]] = []
    for combo in itertools.product(*per_group):
        group_plans = [
            GroupPlan(group=group, lines=row[2], kcal=row[3], target=target_for(group))
            for group, row in zip(groups, combo)
        ]
        kcal = sum(plan.kcal for plan in group_plans)
        penalty = waste_penalty(kcal, target_all)
        est = sum(line.subtotal for plan in group_plans for line in plan.lines)
        combos.append((penalty, est, group_plans))
    combos.sort(key=lambda row: (row[0], row[1]))

    best: Plan | None = None
    for penalty, est, group_plans in combos[:price_top]:
        lines = _merge_lines(group_plans)
        subtotal = sum(line.subtotal for line in lines)
        coupon = _pick_coupon(coupons, subtotal)
        quote = provider.price(lines, coupon)
        if budget is not None and quote.payable > budget + 1e-9:
            continue
        plan = Plan(
            groups=group_plans,
            lines=lines,
            kcal=sum(plan.kcal for plan in group_plans),
            target=target_all,
            quote=quote,
            coupon=coupon,
        )
        score = penalty + 0.1 * plan.per_person_price / 20.0
        if best is None or score < _score(best):
            plan.notes = _build_notes(plan, budget)
            best = plan

    if best is None:
        # 预算过紧：退化成最便宜的可组合方案，并明确告知
        cheapest = min(combos[:price_top], key=lambda row: row[1])
        lines = _merge_lines(cheapest[2])
        subtotal = sum(line.subtotal for line in lines)
        coupon = _pick_coupon(coupons, subtotal)
        plan = Plan(
            groups=cheapest[2],
            lines=lines,
            kcal=sum(p.kcal for p in cheapest[2]),
            target=target_all,
            quote=provider.price(lines, coupon),
            coupon=coupon,
        )
        plan.notes = [
            f"预算 {budget:.0f} 元无法覆盖任何方案，已给出当前最便宜的组合。",
            "建议降低份量档位、去掉饮料，或提高预算。",
        ]
        best = plan

    best.baseline_quote, best.baseline_kcal = _baseline(best.groups, provider, coupons)
    best.nutrition_coverage = sum(1 for line in best.lines if line.item.has_nutrition) / max(len(best.lines), 1)
    if best.baseline_quote is not None:
        saved = best.baseline_quote.payable - best.payable
        kcal_saved = best.baseline_kcal - best.kcal
        side = next(
            (line.item for line in best.lines if line.item.category == "side" and (line.item.kcal or 0) > 0),
            None,
        )
        extra = ""
        if side is not None and kcal_saved > 0:
            extra = f"，约等于 {kcal_saved / side.kcal:.1f} 份{side.name}的热量"
        best.notes.append(
            f"对比「人手一份套餐」（{best.baseline_quote.payable:.2f} 元 / {best.baseline_kcal:.0f} kcal）："
            f"省 {saved:.2f} 元、少 {kcal_saved:.0f} kcal{extra}。"
        )
    return best


def _score(plan: Plan) -> float:
    penalty = waste_penalty(plan.kcal, plan.target)
    return penalty + 0.1 * plan.per_person_price / 20.0


def _build_notes(plan: Plan, budget: float | None) -> list[str]:
    notes: list[str] = []
    notes.append(
        f"目标区间 {plan.target.describe()}，方案 {plan.kcal:.0f} kcal（每人 {plan.kcal / max(plan.headcount, 1):.0f} kcal），判定「{plan.verdict}」。"
    )
    shared = [line for line in plan.lines if line.item.shared and line.qty < plan.headcount]
    if shared:
        detail = "、".join(f"{line.item.name}×{line.qty}" for line in shared)
        notes.append(f"配菜按共享份量下单（{detail}），而不是人手一份。")
    if plan.quote.discount > 0:
        notes.append(f"已套用优惠：{plan.quote.coupon_name or '门店券'}，立减 {plan.quote.discount:.2f} 元。")
    else:
        notes.append("当前没有可用券，价格按菜单原价核算。")
    if plan.quote.source == "estimate":
        notes.append("注意：本方案价格由本地菜单估算（未调用 calculate-price），下单前请以门店实价为准。")
    if budget is not None:
        gap = budget - plan.quote.payable
        notes.append(
            f"预算 {budget:.2f} 元，实付 {plan.quote.payable:.2f} 元，{'剩余' if gap >= 0 else '超出'} {abs(gap):.2f} 元。"
        )
    return notes
