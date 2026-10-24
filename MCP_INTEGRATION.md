# 麦当劳 MCP 集成说明

本文件按赛事要求说明：**实际使用了哪些麦当劳 MCP Server / Tool、调用流程、字段映射与业务价值**。

- MCP Server：麦当劳中国官方 MCP 服务，`https://mcp.mcd.cn`，Streamable HTTP
- 鉴权：`Authorization: Bearer <MCD_MCP_TOKEN>`（Token 由使用者在 https://open.mcd.cn/mcp 激活，仓库内只有环境变量占位符）
- 客户端实现：`justenough/mcp_client.py`，仅用 Python 标准库（`urllib` + `json`），零第三方依赖

---

## 1. 用到的 Tool

| # | Tool | 用途 | 是否必需 | 调用次数（一次 `plan`） |
|---|---|---|---|---|
| 1 | `query-meals` | 拉取门店可售菜单（编码、名称、价格、分类、标签） | 必需 | 1 |
| 2 | `list-nutrition-foods` | 拉取餐品营养（能量/蛋白/脂肪/碳水/钠），用于份量建模 | 必需 | 1（失败则中止并提示） |
| 3 | `calculate-price` | 对候选方案做真实核价（含券、配送费） | 强烈建议 | ≤ `--price-top`（默认 6） |
| 4 | `query-store-coupons` / `available-coupons` / `query-my-coupons` | 取当前可用券，选满减最优的一张 | 可选 | 1（依次降级尝试） |
| 5 | `query-nearby-stores` | 按城市/地址找门店，拿 `storeCode` | 可选（`doctor --check-menu`、未指定门店时） | 0–1 |

**明确未使用**：`create-order`、`cancel-order`、`mall-create-order`、`draw-lottery`。
本项目只做「算份量 + 算价格」，**下单与支付始终由用户在麦当劳官方渠道完成**，避免自动化流程替用户花钱。

## 2. 调用流程

```text
                 ┌──────────────────────────────────────────────┐
                 │  1. initialize（JSON-RPC）                    │
                 │     ← Mcp-Session-Id，后续请求携带            │
                 │  2. notifications/initialized                 │
                 │  3. tools/list  → 读取每个工具的 inputSchema  │
                 └───────────────────┬──────────────────────────┘
                                     │
      ┌──────────────────────────────┼───────────────────────────────┐
      │                              │                               │
┌─────▼─────────┐          ┌─────────▼──────────┐          ┌─────────▼─────────┐
│ query-meals   │          │ list-nutrition-    │          │ 券类工具（可选）   │
│ 菜单+价格     │          │ foods  营养        │          │ query-store-      │
└─────┬─────────┘          └─────────┬──────────┘          │ coupons → ...     │
      │                              │                     └─────────┬─────────┘
      └──────────────┬───────────────┘                               │
                     ▼                                               │
        ┌────────────────────────────┐                               │
        │ 名称归一 + 模糊匹配（0.72）│  营养并入菜单项                │
        │ 分类 main / side / drink   │                               │
        └─────────────┬──────────────┘                               │
                      ▼                                              │
        ┌────────────────────────────┐                               │
        │ 阶段一：本地枚举 + 打分     │  0 次网络请求                  │
        │ 目标能量区间 / 共享配菜     │                               │
        └─────────────┬──────────────┘                               │
                      ▼                                              │
        ┌────────────────────────────┐                               │
        │ 阶段二：仅前 6 个方案核价   │◄──────────────────────────────┘
        │ calculate-price + 选券     │  1–6 次网络请求
        └─────────────┬──────────────┘
                      ▼
        ┌────────────────────────────┐
        │ 输出账单 + 「人手一份」对照 │
        └────────────────────────────┘
```

**为什么分两阶段**：官方限流 600 次/分钟。若每个组合都核价，一次 4 人方案会产生上千次调用。本地打分把网络调用压到 1–7 次，既稳又便宜。

## 3. 参数映射：不猜字段名

MCP 工具的 `inputSchema` 可能随服务端演进调整字段命名。本项目在运行时读取 `tools/list` 的
`inputSchema.properties`，再按**语义别名**匹配，而不是写死字段名：

| 语义 | 候选字段名（按顺序匹配） |
|---|---|
| 门店编码 | `storeCode` → `storeId` → `store_code` → `code` |
| 商品列表 | `products` → `items` → `productList` → `goodsList` → `mealList` |
| 券 ID | `couponId` → `coupon_id` → `couponCode` |
| 城市 / 地址 | `city`/`cityName`/`cityCode`、`address`/`addressName`/`keyword` |
| 就餐方式 | `deliveryType` → `diningType` → `orderType` → `takeWay` |

若 `inputSchema.required` 里的字段无法被映射，会立刻抛出明确错误：

```text
工具 query-meals 的必填参数 ['storeCode'] 无法从语义映射得到；
请运行 `justenough doctor --schema query-meals` 查看真实字段。
```

这样字段改名时是**可诊断的失败**，而不是悄悄地拿错误参数去请求。

## 4. 返回结构解析与降级

| 场景 | 处理 |
|---|---|
| 返回 `content[0].text` 内是 JSON 字符串 | 反序列化后使用（`_unwrap_content`） |
| 返回 `{"data": {...}}` / `{"result": {"list": [...]}}` 多层包装 | `_first_list` 逐层下钻取第一个对象数组 |
| `isError: true` | 抛出 `McpError`，错误文本进日志摘要，不吞掉 |
| HTTP 401 | 提示「Token 无效或过期」并给出重新激活地址 |
| HTTP 429 | 读 `Retry-After` 指数退避，最多重试 3 次 |
| `calculate-price` 不可用 | 降级为本地菜单估算，输出中标注 `价格来源 estimate` |
| 券工具全部失败 | 视为「当前无可用券」，照常出方案，notes 里说明 |

## 5. 业务价值

1. **减少食物浪费**：把「人手一份配菜」改成「两人一份」，4 人场景实测可少点 2 份薯条、约 940 kcal 的过量供给，同时少花 25–30 元（见 `examples/demo-4adults.txt`）。
2. **降低决策成本**：用户只需要回答「几个人、谁在、预算多少」，不用逐项挑选。
3. **价格可信**：总价来自 `calculate-price`，不是模型口算，避免「推荐了一单算错钱的方案」。
4. **对 Agent 友好**：`--json` 输出可被上游 Agent 直接消费；同时项目**不发起下单**，把支付留给真人，降低自动化风险。

## 6. 自检命令

```bash
export MCD_MCP_TOKEN="..."

python3 -m justenough doctor                 # 握手 + 工具清单
python3 -m justenough doctor --schema query-meals   # 查看真实字段结构
python3 -m justenough doctor --check-menu    # 真实拉一次菜单/营养/券，打印前 5 项
python3 -m justenough plan --people 4 --budget 120  # 真实出方案（价格来源应为 mcp）
```

`doctor --check-menu` 的输出可以直接判断接入是否健康：菜单条数、营养匹配条数、券张数三项都不为 0，才说明数据链路完整。
