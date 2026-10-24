"""命令行入口：``justenough doctor`` / ``plan`` / ``demo``。"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from .catalog import CatalogProvider, FixtureProvider, McpCatalogProvider
from .mcp_client import McpClient, McpError
from .planner import build_plan
from .portion import APPETITE_FACTOR, KIND_MID, DinerGroup
from .render import render_markdown, render_plan


def _parse_group(spec: str) -> DinerGroup:
    """解析 ``4`` / ``4:adult`` / ``4:adult:big`` / ``4:child:normal:香菜,辣``。"""
    parts = spec.split(":")
    if not parts or not parts[0].strip():
        raise argparse.ArgumentTypeError(f"无法解析分组：{spec}")
    try:
        count = int(parts[0])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"人数必须是整数：{parts[0]}") from exc
    kind = parts[1] if len(parts) > 1 and parts[1] else "adult"
    appetite = parts[2] if len(parts) > 2 and parts[2] else "normal"
    avoid = tuple(x for x in (parts[3].split(",") if len(parts) > 3 else []) if x)
    try:
        return DinerGroup(count=count, kind=kind, appetite=appetite, avoid=avoid)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _provider(args: argparse.Namespace) -> CatalogProvider:
    if args.demo:
        return FixtureProvider(args.fixture_dir)
    client = McpClient(url=args.mcp_url, verbose=args.verbose)
    return McpCatalogProvider(client)


def _cmd_doctor(args: argparse.Namespace) -> int:
    if args.demo:
        provider = FixtureProvider(args.fixture_dir)
        menu = provider.menu()
        print("模式：fixture（离线示例数据，不调用任何网络接口）")
        print(f"餐品数：{len(menu)}；含营养数据：{sum(1 for item in menu if item.has_nutrition)}")
        print(f"券数：{len(provider.coupons())}")
        return 0

    client = McpClient(url=args.mcp_url, verbose=args.verbose)
    info = client.initialize()
    print("✅ 握手成功")
    server = (info or {}).get("serverInfo") or {}
    if server:
        print(f"服务端：{server.get('name', '?')} {server.get('version', '')}")
    protocol = (info or {}).get("protocolVersion")
    if protocol:
        print(f"协议版本：{protocol}")

    tools = client.list_tools()
    print(f"工具数：{len(tools)}")
    for tool in tools:
        print(f"  - {tool.get('name')}: {str(tool.get('description', ''))[:70]}")

    if args.schema:
        target = next((tool for tool in tools if tool.get("name") == args.schema), None)
        if target is None:
            print(f"\n未找到工具 {args.schema}", file=sys.stderr)
            return 2
        print("\ninputSchema:")
        print(json.dumps(target.get("inputSchema"), ensure_ascii=False, indent=2))

    if args.check_menu:
        provider = McpCatalogProvider(client)
        menu = provider.menu()
        with_nutrition = [item for item in menu if item.has_nutrition]
        print(f"\n菜单：{len(menu)} 项，其中 {len(with_nutrition)} 项匹配到营养数据")
        for item in menu[:5]:
            print(f"  - {item.name} | {item.category} | {item.price:.2f} 元 | {item.kcal or '?'} kcal")
        coupons = provider.coupons()
        print(f"券：{len(coupons)} 张")
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    groups = list(args.group) if args.group else [
        DinerGroup(count=args.people, kind=args.kind, appetite=args.appetite, avoid=tuple(args.avoid or ()))
    ]
    provider = _provider(args)
    plan = build_plan(
        groups,
        provider,
        store_code=args.store,
        budget=args.budget,
        top_per_group=args.top_per_group,
        price_top=args.price_top,
    )
    if args.json:
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    elif args.markdown:
        print(render_markdown(plan))
    else:
        print(render_plan(plan))
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    args.demo = True
    args.group = []
    args.people = 4
    args.kind = "adult"
    args.appetite = "normal"
    args.avoid = []
    args.budget = 120.0
    args.store = None
    args.json = False
    args.markdown = False
    args.top_per_group = 6
    args.price_top = 6
    print("# 离线演示：全部数据来自 fixtures/，不代表真实门店菜单与价格\n")
    return _cmd_plan(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="justenough",
        description="麦门刚好：按人数与食量算「刚好够」的麦当劳订单，少点一点，少剩一点。",
    )
    parser.add_argument("--mcp-url", default=None, help="MCP 服务地址，默认 https://mcp.mcd.cn")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印重试等过程信息")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="检查 Token、握手、工具清单与字段结构")
    doctor.add_argument("--demo", action="store_true", help="检查离线数据")
    doctor.add_argument("--fixture-dir", default=None)
    doctor.add_argument("--schema", default=None, help="打印某个工具的 inputSchema")
    doctor.add_argument("--check-menu", action="store_true", help="顺便拉一次菜单与券")
    doctor.set_defaults(func=_cmd_doctor)

    plan = sub.add_parser("plan", help="生成点餐方案")
    plan.add_argument("--people", type=int, default=4, help="人数（未使用 --group 时生效）")
    plan.add_argument("--kind", choices=sorted(KIND_MID), default="adult")
    plan.add_argument("--appetite", choices=sorted(APPETITE_FACTOR), default="normal")
    plan.add_argument("--avoid", nargs="*", default=[], help="忌口关键词，如 辣 香菜 牛肉")
    plan.add_argument(
        "--group",
        action="append",
        type=_parse_group,
        default=[],
        metavar="N[:KIND[:APPETITE[:AVOID]]]",
        help="精确分组，可重复，如 --group 3:adult:big --group 1:child:light:辣",
    )
    plan.add_argument("--budget", type=float, default=None, help="总预算上限（元）")
    plan.add_argument("--store", default=None, help="门店编码")
    plan.add_argument("--demo", action="store_true", help="使用离线示例数据")
    plan.add_argument("--fixture-dir", default=None)
    plan.add_argument("--json", action="store_true", help="输出 JSON")
    plan.add_argument("--markdown", action="store_true", help="输出 Markdown")
    plan.add_argument("--top-per-group", type=int, default=6, help="每组保留的候选数")
    plan.add_argument("--price-top", type=int, default=6, help="送去真实核价的方案数（控制接口调用量）")
    plan.set_defaults(func=_cmd_plan)

    demo = sub.add_parser("demo", help="用离线数据跑一次完整演示")
    demo.add_argument("--fixture-dir", default=None)
    demo.set_defaults(func=_cmd_demo)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except McpError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
