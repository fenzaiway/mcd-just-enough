import unittest

from justenough.catalog import (
    Coupon,
    FixtureProvider,
    McpCatalogProvider,
    MenuItem,
    _as_lines,
    _first_list,
    _match_nutrition,
    classify,
    normalize_name,
)
from justenough.mcp_client import McpError


class FakeClient:
    """只实现 provider 需要的方法，工具 schema 用官方文档里的字段名。"""

    def __init__(self):
        self.calls = []
        self.tools = [
            {
                "name": "query-meals",
                "inputSchema": {
                    "type": "object",
                    "properties": {"storeCode": {"type": "string"}},
                    "required": ["storeCode"],
                },
            },
            {
                "name": "calculate-price",
                "inputSchema": {
                    "type": "object",
                    "properties": {"products": {"type": "array"}, "couponId": {"type": "string"}},
                    "required": ["products"],
                },
            },
            {"name": "list-nutrition-foods", "inputSchema": {"type": "object", "properties": {}}},
        ]

    def list_tools(self, refresh=False):
        return self.tools

    def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments))
        if name == "query-meals":
            return {
                "data": [
                    {"productCode": "M1", "productName": "麦辣鸡腿堡", "price": 25.5, "categoryName": "汉堡"},
                    {"productCode": "D1", "productName": "可口可乐", "price": 9.5, "categoryName": "饮料"},
                    {"productCode": "M9", "productName": "无价餐品", "price": 0},
                ]
            }
        if name == "list-nutrition-foods":
            return {"data": [{"productName": "麦辣鸡腿堡", "energy": "480 千卡", "protein": 22}]}
        if name == "calculate-price":
            return {
                "data": {
                    "totalAmount": 35.0,
                    "discountAmount": 5.0,
                    "deliveryFee": 0,
                    "payableAmount": 30.0,
                }
            }
        return {}


class ClassifyTest(unittest.TestCase):
    def test_drink_keywords(self):
        self.assertEqual(classify("零度可乐", None, ()), "drink")
        self.assertEqual(classify("拿铁", None, ()), "drink")

    def test_side_keywords(self):
        self.assertEqual(classify("中薯条", None, ()), "side")
        self.assertEqual(classify("麦乐鸡 5 块", None, ()), "side")

    def test_main_default(self):
        self.assertEqual(classify("巨无霸", None, ()), "main")

    def test_raw_category_wins(self):
        self.assertEqual(classify("神秘新品", "饮料", ()), "drink")

    def test_normalize_name_strips_brackets(self):
        self.assertEqual(normalize_name("麦乐鸡（5块）"), "麦乐鸡")


class StructureTest(unittest.TestCase):
    def test_first_list_nested(self):
        payload = {"data": {"products": [{"a": 1}]}}
        self.assertEqual(_first_list(payload), [{"a": 1}])

    def test_first_list_flat(self):
        self.assertEqual(_first_list([{"a": 1}, "x"]), [{"a": 1}])

    def test_as_lines_accepts_items_tuples_and_line_objects(self):
        item = MenuItem("A", "甲", "main", 10.0, 100)

        class LineLike:
            def __init__(self):
                self.item = item
                self.qty = 3

        self.assertEqual(_as_lines([item]), [(item, 1)])
        self.assertEqual(_as_lines([(item, 2)]), [(item, 2)])
        self.assertEqual(_as_lines([LineLike()]), [(item, 3)])

    def test_nutrition_fuzzy_match(self):
        index = {"麦辣鸡腿堡": {"kcal": 480}}
        self.assertEqual(_match_nutrition("麦辣鸡腿堡（辣）", index)["kcal"], 480)
        self.assertEqual(_match_nutrition("完全不同的东西", index), {})

    def test_coupon_threshold(self):
        coupon = Coupon("C1", "满 60 减 12", discount=12, threshold=60)
        self.assertFalse(coupon.applies(59.9))
        self.assertTrue(coupon.applies(60))


class McpProviderTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.provider = McpCatalogProvider(self.client)

    def test_build_args_maps_semantic_names_from_schema(self):
        args = self.provider._build_args("query-meals", {"store_code": "S1"})
        self.assertEqual(args, {"storeCode": "S1"})

    def test_build_args_reports_missing_required(self):
        with self.assertRaises(McpError) as ctx:
            self.provider._build_args("query-meals", {})
        self.assertIn("storeCode", str(ctx.exception))

    def test_menu_normalises_and_drops_zero_price(self):
        menu = self.provider.menu(store_code="S1")
        self.assertEqual(len(menu), 2)
        names = {item.name for item in menu}
        self.assertIn("麦辣鸡腿堡", names)
        burger = next(item for item in menu if item.name == "麦辣鸡腿堡")
        self.assertEqual(burger.category, "main")
        self.assertEqual(burger.kcal, 480)

    def test_price_uses_calculate_price_result(self):
        quote = self.provider.price([(MenuItem("M1", "麦辣鸡腿堡", "main", 25.5, 480), 1)])
        self.assertEqual(quote.source, "mcp")
        self.assertEqual(quote.payable, 30.0)
        self.assertEqual(quote.subtotal, 35.0)

    def test_price_passes_quantity(self):
        item = MenuItem("M1", "麦辣鸡腿堡", "main", 25.5, 480)
        self.provider.price([(item, 2)])
        name, args = next(call for call in self.client.calls if call[0] == "calculate-price")
        self.assertEqual(args["products"][0]["quantity"], 2)


class FixtureProviderTest(unittest.TestCase):
    def test_fixture_menu_has_nutrition(self):
        provider = FixtureProvider()
        menu = provider.menu()
        self.assertGreater(len(menu), 10)
        self.assertTrue(all(item.has_nutrition for item in menu))
        self.assertTrue(any(item.shared for item in menu))

    def test_fixture_coupons(self):
        coupons = FixtureProvider().coupons()
        self.assertTrue(coupons)
        self.assertTrue(all(coupon.threshold >= 0 for coupon in coupons))

    def test_estimate_quote_applies_coupon(self):
        provider = FixtureProvider()
        menu = provider.menu()
        burger = next(item for item in menu if item.price >= 26)
        coupon = Coupon("C1", "满 30 减 5", discount=5, threshold=30)
        quote = provider.price([(burger, 2)], coupon)
        self.assertEqual(quote.source, "estimate")
        self.assertEqual(quote.discount, 5)
        self.assertAlmostEqual(quote.payable, burger.price * 2 - 5, places=2)


if __name__ == "__main__":
    unittest.main()
