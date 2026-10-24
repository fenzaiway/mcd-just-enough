"""菜单、营养与券的领域模型，以及两种数据来源：真实 MCP / 离线 fixture。"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .mcp_client import McpClient, McpError

# --------------------------------------------------------------------- 模型

CATEGORY_ALIAS = {
    "main": "main",
    "burger": "main",
    "主食": "main",
    "汉堡": "main",
    "side": "side",
    "snack": "side",
    "小食": "side",
    "配菜": "side",
    "drink": "drink",
    "beverage": "drink",
    "饮料": "drink",
}


@dataclass(frozen=True)
class MenuItem:
    code: str
    name: str
    category: str  # main / side / drink
    price: float
    kcal: float | None = None
    protein: float | None = None
    fat: float | None = None
    carb: float | None = None
    sodium: float | None = None
    tags: tuple[str, ...] = ()
    shared: bool = False  # 是否适合多人共享（薯条、鸡块等）

    @property
    def has_nutrition(self) -> bool:
        # 0 kcal（如零度可乐）也是有效营养数据，不能用真值判断
        return self.kcal is not None

    def conflicts(self, avoid: Iterable[str]) -> bool:
        if not avoid:
            return False
        haystack = f"{self.name}{''.join(self.tags)}"
        return any(word and word in haystack for word in avoid)


@dataclass(frozen=True)
class Coupon:
    coupon_id: str
    name: str
    discount: float = 0.0
    threshold: float = 0.0
    kind: str = "unknown"

    def applies(self, subtotal: float) -> bool:
        return subtotal >= self.threshold


@dataclass
class Quote:
    subtotal: float
    discount: float
    delivery_fee: float
    payable: float
    source: str = "estimate"  # estimate / mcp
    coupon_name: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------- 工具函数

_KV = re.compile(r"[（(].*?[)）]")


def normalize_name(name: str) -> str:
    text = _KV.sub("", name or "")
    return re.sub(r"[\s·\-—_]+", "", text).lower()


def _pick(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, "", []):
            return data[key]
    return default


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    matched = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(matched.group()) if matched else None


def classify(name: str, raw_category: str | None, tags: Sequence[str]) -> str:
    """把任意菜单结构归一到 main / side / drink 三类。"""
    if raw_category:
        key = str(raw_category).strip().lower()
        if key in CATEGORY_ALIAS:
            return CATEGORY_ALIAS[key]
        for token, mapped in CATEGORY_ALIAS.items():
            if token in key:
                return mapped
    text = f"{name}{''.join(tags)}"
    if re.search(r"可乐|雪碧|芬达|咖啡|拿铁|美式|茶|橙汁|牛奶|豆浆|水|饮|奶茶|圣代|新地|麦旋风", text):
        return "drink"
    if re.search(r"薯条|薯饼|玉米|沙拉|鸡块|鸡翅|鸡腿|派|麦乐鸡|洋葱圈|苹果|摇摇", text):
        return "side"
    return "main"


SHARE_KEYWORDS = ("薯条", "鸡块", "鸡翅", "鸡腿", "派", "玉米", "洋葱圈", "沙拉")


# --------------------------------------------------------------------- 数据源协议


class CatalogProvider:
    """统一的数据来源接口，便于把真实接口替换成离线数据。"""

    name = "base"

    def stores(self, city: str | None = None, address: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    def menu(self, store_code: str | None = None) -> list[MenuItem]:
        raise NotImplementedError

    def coupons(self, store_code: str | None = None) -> list[Coupon]:
        return []

    def price(self, items: Sequence[MenuItem] | Sequence[tuple[MenuItem, int]], coupon: Coupon | None = None) -> Quote:
        lines = _as_lines(items)
        subtotal = sum(item.price * qty for item, qty in lines)
        discount = coupon.discount if coupon and coupon.applies(subtotal) else 0.0
        return Quote(
            subtotal=round(subtotal, 2),
            discount=round(discount, 2),
            delivery_fee=0.0,
            payable=round(max(subtotal - discount, 0.0), 2),
            source="estimate",
            coupon_name=coupon.name if coupon and coupon.applies(subtotal) else None,
        )


# --------------------------------------------------------------------- fixture 数据源


class FixtureProvider(CatalogProvider):
    """离线数据源，用于演示与自动化测试。

    数据为**示例数据**，不代表任何时点的真实菜单与定价，仅用于让没有 Token 的
    评审者也能一键跑通完整流程。
    """

    name = "fixture"

    def __init__(self, directory: str | Path | None = None) -> None:
        self.dir = Path(directory or Path(__file__).resolve().parent.parent / "fixtures")
        self._menu_cache: list[MenuItem] | None = None

    def stores(self, city: str | None = None, address: str | None = None) -> list[dict[str, Any]]:
        path = self.dir / "stores.json"
        if not path.exists():
            return [{"code": "DEMO01", "name": "示例门店（离线数据）", "city": city or "示例市"}]
        return json.loads(path.read_text("utf-8"))

    def _load(self) -> list[MenuItem]:
        if self._menu_cache is not None:
            return self._menu_cache
        path = self.dir / "menu.json"
        payload = json.loads(path.read_text("utf-8"))
        items: list[MenuItem] = []
        for raw in payload:
            name = str(raw["name"])
            tags = tuple(raw.get("tags") or ())
            items.append(
                MenuItem(
                    code=str(raw["code"]),
                    name=name,
                    category=raw.get("category") or classify(name, None, tags),
                    price=float(raw["price"]),
                    kcal=raw.get("kcal"),
                    protein=raw.get("protein"),
                    fat=raw.get("fat"),
                    carb=raw.get("carb"),
                    sodium=raw.get("sodium"),
                    tags=tags,
                    shared=bool(raw.get("shared"))
                    or any(word in name for word in SHARE_KEYWORDS),
                )
            )
        self._menu_cache = items
        return items

    def menu(self, store_code: str | None = None) -> list[MenuItem]:
        return list(self._load())

    def coupons(self, store_code: str | None = None) -> list[Coupon]:
        path = self.dir / "coupons.json"
        if not path.exists():
            return []
        return [
            Coupon(
                coupon_id=str(raw["coupon_id"]),
                name=str(raw["name"]),
                discount=float(raw.get("discount") or 0),
                threshold=float(raw.get("threshold") or 0),
                kind=str(raw.get("kind") or "unknown"),
            )
            for raw in json.loads(path.read_text("utf-8"))
        ]


# --------------------------------------------------------------------- 真实 MCP 数据源


class McpCatalogProvider(CatalogProvider):
    """基于麦当劳 MCP 的真实数据源。

    参数名不靠猜：先 ``tools/list`` 拿 ``inputSchema``，再按语义别名映射到
    实际字段（见 ``_build_args``）。字段名缺失时会给出明确错误，而不是静默用
    错参数。
    """

    name = "mcp"

    # 语义字段 -> 候选参数名
    ALIASES: dict[str, tuple[str, ...]] = {
        "city": ("city", "cityName", "cityCode"),
        "address": ("address", "addressName", "keyword"),
        "store_code": ("storeCode", "storeId", "store_code", "code"),
        "items": ("products", "items", "productList", "goodsList", "mealList"),
        "coupon": ("couponId", "coupon_id", "couponCode"),
        "delivery_type": ("deliveryType", "diningType", "orderType", "takeWay"),
    }

    def __init__(self, client: McpClient) -> None:
        self.client = client
        self._tools: dict[str, dict[str, Any]] = {}

    # -- schema 相关

    def _tool(self, name: str) -> dict[str, Any]:
        if not self._tools:
            self._tools = {tool.get("name", ""): tool for tool in self.client.list_tools()}
        tool = self._tools.get(name)
        if tool is None:
            raise McpError(f"服务端未提供工具 {name}，可用工具：{', '.join(sorted(self._tools))}")
        return tool

    def _build_args(self, tool_name: str, values: dict[str, Any]) -> dict[str, Any]:
        schema = self._tool(tool_name).get("inputSchema") or {}
        props: dict[str, Any] = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        args: dict[str, Any] = {}
        for semantic, value in values.items():
            if value is None:
                continue
            for alias in self.ALIASES.get(semantic, (semantic,)):
                if alias in props:
                    args[alias] = value
                    break
        missing = required - set(args)
        if missing:
            raise McpError(
                f"工具 {tool_name} 的必填参数 {sorted(missing)} 无法从语义映射得到；"
                f"请运行 `justenough doctor --schema {tool_name}` 查看真实字段。"
            )
        return args

    def _call(self, tool_name: str, **values: Any) -> Any:
        return self.client.call_tool(tool_name, self._build_args(tool_name, values))

    # -- 接口实现

    def stores(self, city: str | None = None, address: str | None = None) -> list[dict[str, Any]]:
        data = self._call("query-nearby-stores", city=city or "上海", address=address)
        rows = _first_list(data)
        result = []
        for row in rows:
            result.append(
                {
                    "code": str(_pick(row, "storeCode", "storeId", "code", default="")),
                    "name": str(_pick(row, "storeName", "name", "title", default="门店")),
                    "city": str(_pick(row, "city", "cityName", default=city or "")),
                    "address": str(_pick(row, "address", "storeAddress", default="")),
                    "raw": row,
                }
            )
        return result

    def menu(self, store_code: str | None = None) -> list[MenuItem]:
        data = self._call("query-meals", store_code=store_code)
        rows = _first_list(data)
        nutrition = self._nutrition_index()
        items: list[MenuItem] = []
        for row in rows:
            name = str(_pick(row, "name", "productName", "mealName", "title", default="")).strip()
            if not name:
                continue
            tags = tuple(_as_str_list(_pick(row, "tags", "labels", "tagList", default=[])))
            category = classify(name, _pick(row, "categoryName", "category", "type"), tags)
            nutri = _match_nutrition(name, nutrition)
            items.append(
                MenuItem(
                    code=str(_pick(row, "code", "productCode", "mealCode", "skuCode", "id", default=name)),
                    name=name,
                    category=category,
                    price=float(_to_float(_pick(row, "price", "salePrice", "currentPrice", default=0)) or 0),
                    kcal=_to_float(nutri.get("kcal")),
                    protein=_to_float(nutri.get("protein")),
                    fat=_to_float(nutri.get("fat")),
                    carb=_to_float(nutri.get("carb")),
                    sodium=_to_float(nutri.get("sodium")),
                    tags=tags,
                    shared=any(word in name for word in SHARE_KEYWORDS),
                )
            )
        return [item for item in items if item.price > 0]

    def _nutrition_index(self) -> dict[str, dict[str, Any]]:
        try:
            data = self.client.call_tool("list-nutrition-foods", {})
        except McpError:
            return {}
        rows = _first_list(data)
        index: dict[str, dict[str, Any]] = {}
        for row in rows:
            name = str(_pick(row, "name", "productName", "foodName", default=""))
            if not name:
                continue
            index[normalize_name(name)] = {
                "kcal": _pick(row, "energy", "calorie", "kcal", "calories", "heat"),
                "protein": _pick(row, "protein"),
                "fat": _pick(row, "fat"),
                "carb": _pick(row, "carbohydrate", "carb"),
                "sodium": _pick(row, "sodium", "na"),
            }
        return index

    def coupons(self, store_code: str | None = None) -> list[Coupon]:
        for tool in ("query-store-coupons", "available-coupons", "query-my-coupons"):
            if self._tools and tool not in self._tools:
                continue
            try:
                data = self._call(tool, store_code=store_code)
            except McpError:
                continue
            rows = _first_list(data)
            coupons: list[Coupon] = []
            for row in rows:
                coupons.append(
                    Coupon(
                        coupon_id=str(_pick(row, "couponId", "id", "code", default="")),
                        name=str(_pick(row, "couponName", "name", "title", default="优惠券")),
                        discount=float(
                            _to_float(_pick(row, "discount", "discountAmount", "amount", default=0)) or 0
                        ),
                        threshold=float(_to_float(_pick(row, "threshold", "minAmount", default=0)) or 0),
                        kind="store",
                    )
                )
            if coupons:
                return coupons
        return []

    def price(self, items: Sequence[MenuItem] | Sequence[tuple[MenuItem, int]], coupon: Coupon | None = None) -> Quote:
        lines = _as_lines(items)
        products = []
        for item, qty in lines:
            products.append(
                {
                    "productCode": item.code,
                    "code": item.code,
                    "quantity": qty,
                    "count": qty,
                }
            )
        try:
            data = self._call("calculate-price", items=products, coupon=coupon.coupon_id if coupon else None)
        except McpError:
            return super().price(items, coupon)
        rows = data if isinstance(data, list) else [data]
        payload = rows[0] if rows else {}
        # 兼容 {"data": {...}} 这类再包一层的情况
        inner = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(inner, dict):
            payload = inner
        subtotal = float(
            _to_float(_pick(payload, "totalAmount", "originalAmount", "subtotal", default=0)) or 0
        )
        discount = float(_to_float(_pick(payload, "discountAmount", "discount", default=0)) or 0)
        delivery = float(_to_float(_pick(payload, "deliveryFee", "deliveryPrice", default=0)) or 0)
        payable = float(
            _to_float(_pick(payload, "payableAmount", "payAmount", "totalPrice", default=0))
            or max(subtotal - discount + delivery, 0)
        )
        return Quote(
            subtotal=round(subtotal, 2),
            discount=round(discount, 2),
            delivery_fee=round(delivery, 2),
            payable=round(payable, 2),
            source="mcp",
            coupon_name=coupon.name if coupon and discount > 0 else None,
            raw=payload if isinstance(payload, dict) else {},
        )


# --------------------------------------------------------------------- 解析辅助


def _as_lines(
    items: Sequence[MenuItem] | Sequence[tuple[MenuItem, int]],
) -> list[tuple[MenuItem, int]]:
    """统一成 (餐品, 数量) 列表；兼容直接传菜单项的写法。"""
    lines: list[tuple[MenuItem, int]] = []
    for entry in items:
        if isinstance(entry, MenuItem):
            lines.append((entry, 1))
        elif isinstance(entry, tuple):
            item, qty = entry
            lines.append((item, max(1, int(qty))))
        elif hasattr(entry, "item") and hasattr(entry, "qty"):  # planner.Line，鸭子类型避免循环依赖
            lines.append((entry.item, max(1, int(entry.qty))))
        else:
            raise TypeError(f"无法识别的餐品行：{entry!r}")
    return lines


def _first_list(data: Any) -> list[dict[str, Any]]:
    """从各种包装层级里掏出第一个「字典数组」。"""
    if data is None:
        return []
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("data", "result", "list", "items", "records", "products", "meals", "stores", "coupons"):
            value = data.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
            if isinstance(value, dict):
                nested = _first_list(value)
                if nested:
                    return nested
        for value in data.values():
            if isinstance(value, (list, dict)):
                nested = _first_list(value)
                if nested:
                    return nested
    return []


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part for part in re.split(r"[,，|/]", value) if part]
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            if isinstance(item, dict):
                out.append(str(_pick(item, "name", "tagName", default="")))
            else:
                out.append(str(item))
        return [item for item in out if item]
    return []


def _match_nutrition(name: str, index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    key = normalize_name(name)
    if not key:
        return {}
    if key in index:
        return index[key]
    best_score = 0.0
    best: dict[str, Any] = {}
    for candidate, payload in index.items():
        score = difflib.SequenceMatcher(None, key, candidate).ratio()
        if score > best_score:
            best_score, best = score, payload
    return best if best_score >= 0.72 else {}
