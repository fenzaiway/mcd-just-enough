import unittest

from justenough.catalog import CatalogProvider, Coupon, MenuItem, Quote
from justenough.planner import build_plan
from justenough.portion import DinerGroup


def item(code, name, category, price, kcal, tags=(), shared=False):
    return MenuItem(
        code=code,
        name=name,
        category=category,
        price=price,
        kcal=kcal,
        tags=tuple(tags),
        shared=shared,
    )


class StubProvider(CatalogProvider):
    """合成菜单，用于验证求解逻辑而不是验证真实菜单。"""

    def __init__(self, menu, coupons=()):
        self._menu = menu
        self._coupons = list(coupons)
        self.price_calls = 0

    def menu(self, store_code=None):
        return list(self._menu)

    def coupons(self, store_code=None):
        return list(self._coupons)

    def price(self, items, coupon=None):
        self.price_calls += 1
        return super().price(items, coupon)


MENU = [
    item("B1", "大汉堡", "main", 26.0, 550),
    item("B2", "小汉堡", "main", 16.0, 380),
    item("B3", "辣汉堡", "main", 24.0, 500, tags=("辣",)),
    item("S1", "大薯条", "side", 15.0, 470, shared=True),
    item("S2", "沙拉", "side", 11.0, 60, shared=True),
    item("D1", "可乐", "drink", 9.0, 150),
    item("D2", "美式", "drink", 12.0, 5),
]


class PlannerTest(unittest.TestCase):
    def test_plan_within_target_range(self):
        provider = StubProvider(MENU)
        plan = build_plan([DinerGroup(count=2)], provider)
        self.assertGreaterEqual(plan.kcal, plan.target.lo * 0.9)
        self.assertLessEqual(plan.kcal, plan.target.hi * 1.1)
        self.assertIn(plan.verdict, {"刚好", "偏少", "偏多"})

    def test_shared_side_ordered_once_per_two_people(self):
        provider = StubProvider(MENU)
        plan = build_plan([DinerGroup(count=4)], provider)
        side_lines = [line for line in plan.lines if line.item.category == "side"]
        for line in side_lines:
            self.assertLessEqual(line.qty, 2)  # 4 人共享最多 2 份

    def test_avoid_filter_is_respected(self):
        provider = StubProvider(MENU)
        plan = build_plan([DinerGroup(count=3, avoid=("辣",))], provider)
        self.assertFalse(any("辣" in line.item.name for line in plan.lines))

    def test_budget_is_respected_when_feasible(self):
        provider = StubProvider(MENU, coupons=[Coupon("C1", "满 30 减 5", discount=5, threshold=30)])
        plan = build_plan([DinerGroup(count=2)], provider, budget=80)
        self.assertLessEqual(plan.payable, 80 + 1e-9)

    def test_infeasible_budget_falls_back_with_note(self):
        provider = StubProvider(MENU)
        plan = build_plan([DinerGroup(count=4)], provider, budget=10)
        self.assertGreater(plan.payable, 10)
        self.assertTrue(any("预算" in note for note in plan.notes))

    def test_baseline_is_more_expensive_than_shared_plan(self):
        provider = StubProvider(MENU)
        plan = build_plan([DinerGroup(count=4)], provider)
        self.assertIsNotNone(plan.baseline_quote)
        self.assertGreaterEqual(plan.baseline_quote.payable, plan.payable)

    def test_price_calls_are_bounded(self):
        provider = StubProvider(MENU)
        build_plan([DinerGroup(count=4)], provider, price_top=3)
        # 1 次方案核价 + 1 次对照组合计 ≤ 4，避免打爆 600 次/分钟限流
        self.assertLessEqual(provider.price_calls, 4)

    def test_coupon_is_selected_by_discount(self):
        coupons = [
            Coupon("C30", "满 30 减 5", discount=5, threshold=30),
            Coupon("C60", "满 60 减 15", discount=15, threshold=60),
        ]
        provider = StubProvider(MENU, coupons=coupons)
        plan = build_plan([DinerGroup(count=4)], provider)
        self.assertIsNotNone(plan.coupon)
        if plan.quote.subtotal >= 60:
            self.assertEqual(plan.coupon.coupon_id, "C60")

    def test_menu_without_nutrition_raises(self):
        provider = StubProvider([MenuItem("X", "无营养", "main", 10.0, None)])
        with self.assertRaises(ValueError):
            build_plan([DinerGroup(count=1)], provider)

    def test_empty_groups_raises(self):
        with self.assertRaises(ValueError):
            build_plan([], StubProvider(MENU))

    def test_to_dict_is_json_serialisable(self):
        import json

        provider = StubProvider(MENU)
        plan = build_plan([DinerGroup(count=2)], provider)
        payload = json.dumps(plan.to_dict(), ensure_ascii=False)
        self.assertIn("payable", payload)

    def test_provider_accepts_lines_and_items(self):
        line = Quote(10, 0, 0, 10)
        provider = StubProvider(MENU)
        quote = provider.price([(MENU[0], 2), MENU[1]])
        self.assertEqual(quote.subtotal, 26.0 * 2 + 16.0)
        self.assertEqual(line.payable, 10)


if __name__ == "__main__":
    unittest.main()
