# 麦门刚好 · mcd-just-enough

> 四个人点麦当劳，最容易犯的错不是点贵了，是**点多了**。
> 这个 Skill 先算「这桌人到底吃得下多少」，再去找最接近这个量的组合——薯条按两人一份下单，饮料想喝才加，最后交给 `calculate-price` 核一遍真实价格。

[![tests](https://img.shields.io/badge/tests-51%20passing-brightgreen)](tests/)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

---

## 为什么还要再做一版点餐助手

榜单上的点餐 Skill 大多在回答「**怎么点更便宜**」。但真实的一桌饭经常是另一种失败：预算没超，菜剩了一半——薯条人手一包、饮料人手一杯，最后全进了垃圾桶。

`mcd-just-enough` 只回答一个问题：**这一桌人，点多少刚好够？**

| | 常见的点餐助手 | 麦门刚好 |
|---|---|---|
| 优化目标 | 更便宜 / 更快 | 更贴合实际食量，顺带更便宜 |
| 份量判断 | 交给大模型「感觉」 | 能量区间建模，可复算 |
| 总价 | 模型口算 | 只信 `calculate-price` 的返回 |
| 配菜 | 默认人手一份 | 默认可共享，两人一份 |
| 输出 | 一段推荐语 | 一张可复核的账单 + 「人手一份」对照 |

一句话区别：**别人帮你点单，它帮你少点。**

和「团餐预算最优」「凑单分账」类项目也不同：那些项目在预算和满意度上做最优解，本项目优化的是**总量贴合度**——把「这桌人吃不吃得下」量化成能量区间，并把这个区间的误差写进输出，供人复核。

## 三个真实场景

1. **办公室下午茶**：6 个人、预算 150、有人不吃辣。它按 6 人的能量区间配餐，薯条下 3 份，而不是 6 份。
2. **带娃出门**：3 个大人 + 1 个 8 岁小孩。小孩按儿童区间单独建模，不会跟着大人吃一份巨无霸。
3. **控制体重的打工人**：把食量调成「小」，它给的是「麦香鸡 + 半份薯条」这种组合，而不是「套餐 + 零度可乐」的自我安慰。

## 30 秒上手

**① 先看效果（不需要 Token，零依赖，离线数据）**

```bash
git clone https://github.com/fenzaiway/mcd-just-enough.git
cd mcd-just-enough
python3 -m justenough demo
```

**② 接真实门店（麦当劳 MCP）**

```bash
# 1. 到 https://open.mcd.cn/mcp 用手机号登录并激活 MCP Token
export MCD_MCP_TOKEN="你的 Token"

# 2. 自检：握手、工具清单、菜单与营养是否真的取到了
python3 -m justenough doctor --check-menu

# 3. 出方案
python3 -m justenough plan --people 4 --budget 120
python3 -m justenough plan --group 3:adult:big --group 1:child:light:辣 --budget 150
python3 -m justenough plan --people 2 --appetite light --markdown
```

`--group` 的格式是 `人数[:人群[:食量[:忌口]]]`，可以重复传；人群 `adult/teen/child`，食量 `light/normal/big`。

**③ 装成命令（可选）**

```bash
pip install .
justenough plan --people 4
```

## 输出长这样

下面是 `python3 -m justenough demo` 的真实输出（离线示例数据，已标注）：

```text
🍟 麦门刚好 · 点餐方案
========================================================
就餐 4 人 | 目标 2592–3168 kcal | 方案 2620 kcal | 判定 刚好
[████████████████████████████──────············]  ▏区间 2592–3168

分组
  · 成人×4（正常）: 2620 kcal（目标 2592–3168 kcal，人均 655 kcal）

┌────────────┬──────┬───────┬───────┬──────┐
│ 餐品       │ 数量 │ 单价  │ 小计  │ kcal │
├────────────┼──────┼───────┼───────┼──────┤
│ 麦辣鸡腿卷 │    4 │ 19.50 │ 78.00 │ 1680 │
│ 大薯条     │    2 │ 15.50 │ 31.00 │  940 │
└────────────┴──────┴───────┴───────┴──────┘

菜品小计 109.00 元 | 优惠 -20.00 元
实付 89.00 元 | 人均 22.25 元 | 价格来源 estimate
对比「人手一份套餐」：120.00 元 / 3560 kcal → 省 31.00 元、少 940 kcal

说明
  - 目标区间 2592–3168 kcal，方案 2620 kcal（每人 655 kcal），判定「刚好」。
  - 配菜按共享份量下单（大薯条×2），而不是人手一份。
  - 已套用优惠：满 99 减 20，立减 20.00 元。
```

更多示例见 [`examples/`](examples/)：混合人群、纯 Markdown 账单、JSON 输出。

## 它是怎么算的

**第一步 · 份量模型**（`justenough/portion.py`）

| 人群 | 一餐能量区间 | 依据 |
|---|---|---|
| 成人 | 620–820 kcal | 2000–2200 kcal/日 的 30%–37% |
| 青少年 | 760–960 kcal | 生长发育期需求更高 |
| 儿童（6–12 岁） | 380–520 kcal | 成人约 60% |

食量档位再乘系数：少 0.82 / 正常 1.00 / 大 1.20。区间取 ±10%，因为「刚好」本来就是带容差的判断。

**第二步 · 两阶段求解**（`justenough/planner.py`）

1. **营养阶段**：只读本地菜单，枚举「主食 × 配菜（可共享）× 饮料」的组合，按离目标区间的距离打分。几千种组合毫秒级完成，**不发一次网络请求**。
2. **核价阶段**：只把排名前 6 的方案交给 `calculate-price` 真实核价（含券、配送费），再按「份量贴合度 + 人均价格」排序。

这样设计是为了避开官方 600 次/分钟的限流：一次 `plan` 通常只打 1–7 次接口，而不是几百次。

**罚分不对称**：点多了的罚分系数是 1.2，点少了是 0.8。反浪费是这个项目的主目标，宁可差一点，也别剩一盒。

**配菜共享**：`shared=true` 的餐品（薯条、鸡块、鸡翅、玉米杯…）按 `ceil(人数/2)` 下单，这是「人手一份」和「刚好够」之间最大的一笔差价。

更多设计取舍见 [`docs/design.md`](docs/design.md)。

## 和麦当劳 MCP 的对接

真实运行时用到的工具、调用顺序、字段映射与降级策略，全部写在 **[`MCP_INTEGRATION.md`](MCP_INTEGRATION.md)**。要点：

- 走 `https://mcp.mcd.cn` 的 Streamable HTTP，`Authorization: Bearer <Token>`；
- **不猜参数名**：先 `tools/list` 拿 `inputSchema`，再按语义别名（`storeCode`/`storeCode`/`code`…）映射，缺必填字段时直接报错并提示用 `doctor --schema` 查；
- 价格永远以 `calculate-price` 为准；只有拿不到时才降级为本地估算，并在输出里明确标注 `价格来源 estimate`。

## 目录结构

```text
mcd-just-enough/
├── justenough/
│   ├── mcp_client.py   # 零依赖 MCP 客户端：握手/会话/SSE/429 退避/Token 脱敏
│   ├── catalog.py      # 菜单·营养·券的模型与两种数据源（真实 MCP / 离线 fixture）
│   ├── portion.py      # 份量模型：人数+人群+食量 → 能量区间
│   ├── planner.py      # 两阶段组合求解 + 券选择 + 「人手一份」对照
│   ├── render.py       # 终端报表（按东亚字符宽度对齐）/ Markdown
│   └── cli.py          # doctor / plan / demo
├── fixtures/           # 离线示例数据（明确标注非真实菜单）
├── examples/           # 可直接查看的输出样例
├── tests/              # 51 个单元测试，全部离线
└── docs/design.md      # 设计取舍与复杂度
```

## 测试

```bash
python3 -m unittest discover -s tests -v   # 51 tests, 0.01s
python3 -m justenough demo                 # 端到端冒烟
```

测试全部使用合成数据与替身对象（`StubProvider` / `FakeClient`），不依赖网络、不需要 Token、不读任何真实用户数据。CI 跑 Python 3.10 与 3.12 两档。

## 隐私与安全

- Token 只从环境变量 `MCD_MCP_TOKEN` 读取，**不写入日志、不回显、不落盘**，错误信息里出现的 Token 会被替换成 `***REDACTED***`；
- 仓库内 `mcp-config.example.json` 只使用 `${MCD_MCP_TOKEN}` 占位符，**不含任何真实凭据**；
- 本项目**不执行下单**：只做「算多少 + 算多少钱」，付款与提交订单始终由人在麦当劳官方渠道完成。这样一个自动化的 Agent 不会替你花钱。

## 已知限制（先说清楚）

1. **它是建议，不是医嘱**：营养数值来自公开常见品类的量级参考，不构成医疗或营养建议。
2. **份量模型是启发式**：同一个人今天饿不饿，模型不知道。所以它给区间、给对照，也允许你手动改食量档位。
3. **真实接口字段以 `doctor` 输出为准**：MCP 的返回结构可能调整；本项目用 schema 驱动 + 语义别名映射来吸收差异，若某天字段改名，`doctor --schema` 会直接指出缺哪个字段。
4. **不做支付**：见上。
5. **离线 fixture 不是真实价格**：仅用于让没有 Token 的人也能跑通流程，输出里始终标注 `价格来源 estimate`。

## 声明

本项目是「麦当劳程序员创意开发大赛」参赛作品，由参赛者独立开发，**非麦当劳官方产品**。餐品信息、价格与供应状态以麦当劳官方渠道的实时结果为准。完整声明见 [`CONTEST_DECLARATION.md`](CONTEST_DECLARATION.md)。

## License

[MIT](LICENSE)
