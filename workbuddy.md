# WorkBuddy 协作开发记录（workbuddy.md）

本文件是「麦当劳程序员创意开发大赛」WorkBuddy 专项奖励要求的开发上下文记录，内容来自
2026-10-24 晚在 **WorkBuddy 桌面端（macOS）** 中完成的真实开发会话，未做美化。

- 使用工具：WorkBuddy 桌面端（Agent 模式），工作目录 `~/dev/mcd-just-enough`
- 参与人：项目作者（提出目标、做取舍确认）+ WorkBuddy（需求拆解、选题调研、编码、测试、文档）
- 会话时间：2026-10-24 22:38 — 2026-10-25 凌晨

---

## 1. 会话起点

**用户输入**

> 今天 1024，workbuddy 有什么获取积分活动吗

WorkBuddy 检索了官方活动信息，给出三条可拿积分的 1024 活动（麦当劳 × WorkBuddy 创意开发大赛、1024 造物挑战、WaytoAGI 用餐赛道），并核对到麦当劳赛事的截止时间是 10 月 25 日 23:59。

**用户输入**

> 交给你全权处理，帮我拿奖

WorkBuddy 先给出了一句风险提示（原话）：

> 前 3 名按 GitHub Star 排名，这个我无法保证（也绝不刷星）；我能做的是把「作品质量 + 合规完整度」做到能进前 100、稳拿 3000 积分的水平。

随后开始按「能拿奖」倒推执行。

## 2. WorkBuddy 做的第一件事：把规则读成约束

拉取并逐条核对了官方仓库 `M-China/mcd-developer-innovation-challenge` 的
`activityGuidelines.md`、`CONTEST_DECLARATION.md`、`README.md`、`RANKING.md`，以及
`M-China/mcd-mcp-server` 的工具清单。提炼出的硬约束：

| 约束 | 对设计的影响 |
|---|---|
| 必须含 `README.md` / `CONTEST_DECLARATION.md` / `MCP_INTEGRATION.md` / `mcp-config.example.json` | 四个文件全部补齐，声明文件逐字复制官方原文 |
| 提交 `workbuddy.md` 才有 3000 积分 | 即本文件 |
| 排名按 GitHub 公开 Star，10-26 00:00 截止；Star=0 不进榜 | 只能靠作品本身传播，代码里不做任何刷量 |
| 榜单尾部门槛 52 Star，前三 1250+ | 目标设定为「进榜 + 冲前 100」，不承诺前三 |
| 项目须真实使用麦当劳 MCP 与 WorkBuddy | 真实 MCP 客户端 + 真实调用链 + 本记录 |

## 3. 选题：先看榜单的空白

WorkBuddy 拉取当时的 `RANKING.md`（100 个项目）后，按名称做了方向归类：

- 省钱/预算类 ≈ 30 个（`*-saver`、`*-budget-*`、`mai-suanpan`…）
- 热量/健身类 ≈ 15 个（`*-calorie-*`、`*-fit-*`、`walk-kcal`…）
- 拼单/团餐类 ≈ 8 个（`mcd-party-*`、`mcd-group-order`…）
- 娱乐类 ≈ 10 个（塔罗、抽卡、MBTI…）

结论：**「这桌人到底吃得下多少」几乎是空白**。于是定为「反浪费 + 适量点餐」，命名
`mcd-just-enough`（麦门刚好）。这个判断与取舍写在 `docs/design.md` 第 1 节。

## 4. 实现顺序与关键决策

| 顺序 | 产出 | 当时的理由 |
|---|---|---|
| 1 | `mcp_client.py` | 先用 curl 探测端点：不带 Token 返回 403、带假 Token 返回 401「当前鉴权码不存在或已失效!」，确认鉴权链路后再写客户端 |
| 2 | `catalog.py` | 菜单/营养/券统一模型；参数名不写死，读 `inputSchema` 做语义映射 |
| 3 | `portion.py` | 份量区间先落成可测试的纯函数，再谈求解 |
| 4 | `planner.py` | 两阶段求解（本地枚举 → 少量核价），直接为了绕开 600 次/分钟限流 |
| 5 | `render.py` / `cli.py` | 终端报表按东亚字符宽度对齐，避免中文表格错位 |
| 6 | `tests/` | 51 个离线单元测试，把「核价调用次数有上限」写成断言 |
| 7 | 文档 | README / MCP_INTEGRATION / design / 本文件 |

## 5. 踩坑与修复（真实发生）

1. **`Line` 不是 tuple**：`CatalogProvider.price` 起初只接受 `MenuItem` 或 `(item, qty)`，
   传入 `planner.Line` 时崩在 `'Line' object has no attribute 'price'`。
   修复：`_as_lines` 用鸭子类型同时支持三种形态，避免 `catalog ← planner` 的循环依赖。
2. **0 kcal 被当成「没有营养数据」**：`has_nutrition` 原本写 `kcal > 0`，导致零度可乐被过滤。
   修复：改为 `kcal is not None`，并加注释说明为什么不能用真值判断。
3. **核价结果解析错层**：`calculate-price` 返回 `{"data": {...}}` 时取不到 `payable`，算出 0 元。
   修复：加一层 `data` 解包，并用单测 `test_price_uses_calculate_price_result` 钉住。
4. **`verdict` 过于敏感**：2660 kcal 对 2665 kcal 的目标下限判「偏少」，读起来很怪。
   修复：给 verdict 加 2% 容差。
5. **429 重试测试写死了响应条数**：`initialize` 之后还有一次 `notifications/initialized`，
   固定 `side_effect` 列表会 `StopIteration`。修复：改成按调用计数返回。

这 5 个问题都是先跑出来、再定位、再补测试，不是「一次写对」。

## 6. 验证记录

```text
$ python3 -m unittest discover -s tests
Ran 51 tests in 0.004s
OK

$ python3 -m justenough demo
就餐 4 人 | 目标 2592–3168 kcal | 方案 2620 kcal | 判定 刚好
实付 89.00 元 | 人均 22.25 元 | 价格来源 estimate
对比「人手一份套餐」：120.00 元 / 3560 kcal → 省 31.00 元、少 940 kcal
```

离线链路（份量建模 → 枚举 → 选券 → 渲染）已闭环；真实 MCP 链路的自检命令是
`justenough doctor --check-menu`，见 `MCP_INTEGRATION.md` 第 6 节。

## 7. 人与 AI 的分工（如实说明）

**WorkBuddy 完成**：活动规则检索与核对、榜单方向分析、代码与测试实现、文档撰写、Git 与发布准备。

**作者完成**：确认选题与取舍（尤其「不刷星、不代下单」两条红线）、复核输出合理性、
提供 MCP Token 做真实接口验证、决定最终发布与报名。

代码由 AI 生成，但每一处设计取舍都能在 `docs/design.md` 里找到理由和被拒绝的替代方案——
这是本项目对「AI 协作」的理解：不是让 AI 代替判断，而是把判断的成本降到可以逐条复核。
