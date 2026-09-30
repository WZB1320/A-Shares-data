# 股票数据 API 接口文档

> **Base URL**: `http://localhost:8001`
> **Swagger 文档**: `http://localhost:8001/docs`
> **数据格式**: JSON
> **日期格式**: `YYYY-MM-DD`（ISO 8601）

---

## 零、数据覆盖范围（重要 — 先读这里）

**接口只对本节列出的股票返回数据。查询范围外的股票, 所有接口都会返回 `count: 0` /
空数组, 而不是报错。** 调用方若拿到 0/null/空值, 请先确认股票是否在覆盖清单内, 再怀疑字段。

当前覆盖 **22 只**（`src/config.py` 的 `STOCK_CODES`，与 `stock_master` 主数据表一一对应）：

```
sh600089 特变电工   sh600276 恒瑞医药   sh600309 万华化学   sh600346 恒力石化
sh600519 贵州茅台   sh600552 凯盛科技   sh600585 海螺水泥   sh600887 伊利股份
sh600900 长江电力   sh601318 中国平安   sh601857 中国石油   sh513700 香港医药ETF
sz000651 格力电器   sz000725 京东方A    sz000858 五粮液     sz002272 川润股份
sz002415 海康威视   sz002594 比亚迪     sz002648 卫星化学   sz002714 牧原股份
sz002843 华懋新材   sz300750 宁德时代
```

各表实际覆盖（2026-09-29 核对）：

| 表 / 接口 | 覆盖股票数 | 说明 |
|---|---|---|
| `stock_master` | 22 | 权威清单 |
| `stock_daily` / `/api/daily` | 22 | 日线, 数据起 2016-05-10 |
| `valuation_indicators` / `/api/indicators/valuation` | 21 | 无 ETF（ETF 无 PE/PB, 属正常） |
| `financial_statements` / `/api/financial` | 21 | 同上 |
| `financial_intermediate` / `/api/indicators/financial` | 21 | 同上 |
| `stock_capital` / `/api/capital` | 21 | 同上 |
| `stock_industry` / `/api/industry/*` | **5223** | 全市场行业成分, 可做同业对比 |
| `margin_trading` / `/api/margin` | 22 | |
| `dividends` / `/api/dividends` | 21 | |
| `capital_flow` / `/api/flow` | 见说明 | 数据源单次只返回最近约 121 个交易日；由每日采集累积，回填进行中（东财限流，慢速滴灌） |
| `risk_pledge` / `/api/risk` | 见说明 | 周频质押比例快照；零质押股票无行属正常 |
| `risk_holder_change` / `/api/risk` | 见说明 | 股东增减持公告记录；无记录属正常 |

> **维护约定**：新增自选股时必须**同时**改两处 —— `src/config.py` 的 `STOCK_CODES`
> 和 `scripts/init_master.py` 的 `STOCKS`，否则会出现"主数据里有、但采集不到"的空值假象。
> 改完执行 `python scripts/init_master.py` 同步主数据表，再跑一次数据更新。

> **数据一致性保证（2026-09-29 起）**：
> 1. **估值表与日线按日期对齐** —— `valuation_indicators.trade_date` 必定存在同日的
>    `stock_daily` 记录，不会出现"日线尚无这一天"的孤儿行。调用方按
>    `(stock_code, trade_date)` 关联日线取 `close` 不会落空。
>    估值表以本地计算器（基于日线 + 财报）为权威源；第三方实时估值仅在当日日线
>    已入库时才允许写入（`scripts/verify_valuation_guard.py` 可回归验证）。
> 2. **融资融券按交易日增量采集** —— 只请求水位线之后 + 最近 5 个交易日（回补窗口）
>    的数据，不再固定重采 60 个自然日；单次请求带 15s 超时，失败会记 WARNING。

---

## 一、接口清单

| # | 接口 | 方法 | 说明 |
|---|------|------|------|
| 1 | `/api/health` | GET | 健康检查 |
| 2 | `/api/stocks` | GET | 股票列表（`with_price=true` 时带最新价） |
| 3 | `/api/basic_info` | GET | 批量基础信息（含最新收盘价，回测取价推荐用这个） |
| 4 | `/api/latest` | GET | 各股票最新数据日期 |
| 5 | `/api/daily/{stock_code}` | GET | 日线行情 |
| 6 | `/api/indicators/technical/{stock_code}` | GET | 技术指标 |
| 7 | `/api/indicators/valuation/{stock_code}` | GET | 估值指标 |
| 8 | `/api/indicators/financial/{stock_code}` | GET | 财务指标（中间表，含算好的比率） |
| 9 | `/api/financial/{stock_code}` | GET | 财务原始报表（含 current_assets / current_liabilities / monetary_funds） |
| 10 | `/api/summary/{stock_code}` | GET | 单股综合摘要（价格+估值+财务+北向，附不可用字段原因） |
| 11 | `/api/northbound/market` | GET | 北向资金**整体**流向（个股接口失效后的替代） |
| 12 | `/api/northbound/{stock_code}` | GET | 北向资金（**个股数据止于 2024-08-16**） |
| 13 | `/api/margin/{stock_code}` | GET | 融资融券 |
| 14 | `/api/flow/{stock_code}` | GET | 个股主力资金流向（超大/大/中/小单净额与净占比，**历史约 121 交易日**） |
| 15 | `/api/capital/{stock_code}` | GET | 股本数据 |
| 16 | `/api/dividends/{stock_code}` | GET | 分红数据 |
| 17 | `/api/announcements/{stock_code}` | GET | 公告数据 |
| 18 | `/api/dragon/{stock_code}` | GET | 龙虎榜 |
| 19 | `/api/risk/{stock_code}` | GET | 风险面/治理事件汇总（质押+增减持+回购+解禁+股东户数） |
| 20 | `/api/industry/{stock_code}` | GET | 个股行业信息 |
| 21 | `/api/industry/list` | GET | 行业列表（含成分股数量） |
| 22 | `/api/industry/{industry_name}/stocks` | GET | 行业内股票（全市场，样本充足） |
| 23 | `/api/coverage` | GET | 数据覆盖度自检（各表覆盖/最新日期/缺失清单） |
| 24 | `/api/master` | GET | 股票主数据 |
| 25 | `/api/master/{stock_code}` | GET | 单只股票主数据（含最新收盘价 `close_price`） |

> 排序约定：**所有列表接口均为日期降序（最新在前）**，`limit=1` 即取最新一条。
> `daily` / `technical` / `valuation` 按 `trade_date` 降序；`financial` 按 `report_date` 降序；
> `capital` 按 `record_date` 降序。


---

## 二、统一响应格式

### 列表接口
```json
{
  "stock_code": "sz002272",
  "count": 100,
  "data": [ { ... }, { ... } ]
}
```

### 单条/聚合接口
直接返回对象，如：
```json
{
  "status": "ok",
  "db_path": "..."
}
```

---

## 三、股票代码格式

**重要**：股票代码必须带交易所前缀，**小写**：

| 交易所 | 前缀 | 示例 |
|--------|------|------|
| 上海主板 | `sh` | `sh600519`（贵州茅台） |
| 深圳主板/中小板 | `sz` | `sz002272`（川润股份） |
| 创业板 | `sz` | `sz300750` |
| 科创板 | `sh` | `sh688981` |

**错误示例**：`600519`、`SH600519`、`002272`

---

## 四、各接口字段详解

### 1. `/api/daily/{stock_code}` 日线行情

**参数**：
- `start_date` (可选): 开始日期 YYYY-MM-DD
- `end_date` (可选): 结束日期 YYYY-MM-DD
- `limit` (可选): 最大返回条数，默认 10000，最大 100000

**返回字段**：

| 字段 | 类型 | 说明 | 状态 |
|------|------|------|------|
| stock_code | string | 股票代码 | ✓ |
| trade_date | string | 交易日期 | ✓ |
| open | float | 开盘价 | ✓ |
| high | float | 最高价 | ✓ |
| low | float | 最低价 | ✓ |
| close | float | 收盘价 | ✓ |
| volume | int | 成交量（股） | ✓ |
| amount | float | 成交额（元） | ✓ |
| adjust_factor | float | 复权因子 | 可能为 null |
| prev_close | float | 前收盘价 | ✓ |
| change_pct | float | 涨跌幅（%） | ✓ |
| amplitude | float | 振幅（%） | ✓ |
| body_size | float | 实体大小 | ✓ |
| upper_shadow | float | 上影线 | ✓ |
| lower_shadow | float | 下影线 | ✓ |
| high_20 | float | 20日最高 | ✓ |
| low_20 | float | 20日最低 | ✓ |
| high_60 | float | 60日最高 | ✓ |
| low_60 | float | 60日最低 | ✓ |
| **turnover** | float | 换手率（%） | ✓ 已填充 |
| **outstanding_share** | int | 流通股本 | ✓ 已填充 |

> **换手率说明**：`turnover` 由 `volume / outstanding_share × 100` 计算，**绝大多数行有值**。
> 仅两类行为 NULL：① ETF（如 sh513700，本身无流通股本概念）；
> ② 个股在 `outstanding_share` 缺失的日期（数据源未返回股本）。
> 若某只股票长期全空，请检查 `stock_capital` 是否有该股票的股本记录。

---

### 2. `/api/indicators/technical/{stock_code}` 技术指标

**参数**：同日线接口

**返回字段**（共 35 个技术指标）：

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | string | 股票代码 |
| trade_date | string | 交易日期 |
| ma5 / ma10 / ma20 / ma60 | float | 5/10/20/60日均线 |
| ema12 / ema26 | float | 12/26日指数均线 |
| boll_mid / boll_upper / boll_lower | float | 布林带中轨/上轨/下轨 |
| macd_dif / macd_dea / macd_hist | float | MACD 三线 |
| bias5 / bias10 / bias20 / bias60 | float | 乖离率 |
| rsi6 / rsi12 / rsi24 | float | RSI 指标 |
| kdj_k / kdj_d / kdj_j | float | KDJ 三线 |
| cci20 | float | CCI 指标 |
| wr14 | float | WR 指标 |
| atr14 | float | ATR 指标 |
| std20 | float | 20日标准差 |
| vol_ma5 / vol_ma10 | int | 成交量均线 |
| obv | int | OBV 指标 |
| mfi14 | float | MFI 指标 |
| vr | float | VR 指标 |
| dmi_pdi / dmi_mdi / dmi_adx / dmi_adxr | float | DMI 四线 |
| sar | float | SAR 抛物线 |
| wvad | float | WVAD 指标 |

> **说明**：技术指标数据完整，无缺失问题。用户报告中"MA5/MA20 缺失"应为调用方解析错误。

---

### 3. `/api/indicators/valuation/{stock_code}` 估值指标

**参数**：同日线接口

**返回字段**：

| 字段 | 类型 | 说明 | 备注 |
|------|------|------|------|
| stock_code | string | 股票代码 | |
| trade_date | string | 交易日期 | |
| pe_ttm | float | 市盈率TTM | 亏损时为负值 |
| pb | float | 市净率 | |
| ps_ttm | float | 市销率TTM | |
| dividend_yield | float | 股息率（%） | 可能为 null |
| roe | float | 净资产收益率 | 亏损时为负值 |
| pe_annual | float | 年度市盈率 | |
| ps_annual | float | 年度市销率 | |
| roe_ttm | float | ROE TTM | |
| roe_annual | float | 年度ROE | 可能为 null |
| used_report_date | string | 使用的财报日期 | |
| used_announcement_date | string | 使用的公告日期 | |
| close | float | 同日收盘价（联结日线带出） | 与 `/api/daily` 同日 close 一致 |

> **重要说明**：
> - **PE_TTM 为负值是正常现象**，表示公司亏损（如 sz002272 2026Q2 亏损，PE_TTM 为负）。调用方应判断 `pe_ttm < 0` 时标注"公司亏损，PE 不适用"，而非报错。
> - **没有 `pe` 字段**，只有 `pe_ttm` 和 `pe_annual`，请勿调用 `pe`。
> - **`close` 已提供**（2026-09-29 起）：估值表本身不存价格，接口按
>   `(stock_code, trade_date)` 联结 `stock_daily` 实时带出，与日线同源不会出现
>   两处不一致。也可继续用 `/api/daily/{code}` 或 `/api/master/{code}`（字段 `close_price`）。
> - **日期对齐**：估值表的 `trade_date` 与日线表的 `trade_date` 一一对应，
>   `limit=1` 取到的最新估值行**一定能**在 `/api/daily` 找到同日的 `close`。
>   若发现估值最新日期晚于日线，说明采集流程被中断，重跑一次数据更新或
>   `python scripts/recalc_indicators.py` 即可（估值计算会整体重写并清除异常行）。

---

### 4. `/api/indicators/financial/{stock_code}` 财务指标

**参数**：
- `start_date` / `end_date`：按 `report_date` 过滤

**返回字段**（共 88 个字段，关键字段如下）：

| 字段 | 类型 | 说明 | 状态 |
|------|------|------|------|
| stock_code | string | 股票代码 | ✓ |
| report_date | string | 报告期 | ✓ |
| report_type | string | 报告类型（合并期末） | ✓ |
| eps | float | 每股收益 | ✓ |
| bvps | float | 每股净资产 | ✓ |
| net_profit | float | 净利润 | ✓ |
| total_revenue | float | 营业总收入 | ✓ |
| operating_cost | float | 营业成本 | ✓ |
| gross_margin_annual | float | 毛利率（年度） | ✓ |
| gross_margin_ttm | float | 毛利率（TTM） | ✓ |
| net_margin_parent_annual | float | 净利率（年度） | ✓ |
| roe_annual / roe_ttm | float | ROE | ✓ |
| roa_annual / roa_ttm | float | ROA | ✓ |
| revenue_yoy_annual | float | 营收同比 | ✓ |
| net_profit_yoy_annual | float | 净利润同比 | ✓ |
| inventory_turnover_annual | float | 存货周转率 | ✓ |
| accounts_receivable_turnover_annual | float | 应收账款周转率 | ✓ |
| fcf_ttm | float | 自由现金流TTM | ✓ |
| **current_ratio** | float | 流动比率（已算好） | ✓ |
| **quick_ratio** | float | 速动比率（已算好） | ✓ |
| inventory | float | 存货 | ✓ |
| accounts_receivable | float | 应收账款 | ✓ |
| **current_assets** | - | 流动资产 | ❌ 本接口无，见 `/api/financial/{code}` |
| **current_liabilities** | - | 流动负债 | ❌ 本接口无，见 `/api/financial/{code}` |
| **monetary_funds** | - | 货币资金 | ❌ 本接口无，见 `/api/financial/{code}`（2026-09-29 起提供） |
| gross_margin_annual / gross_margin_ttm | float | 毛利率 | ✓ |

> **重要说明**：
> 1. **`current_ratio` / `quick_ratio` 已由服务端算好并返回**，请**直接使用**，
>    不要用 `current_assets / current_liabilities` 自行相除——本接口（中间表）不含这两个原始字段，
>    自行相除会得到 0 或异常值。
> 2. **`receivables` 的正确字段名是 `accounts_receivable`**。
> 3. 需要**原始资产负债科目**（`current_assets`、`current_liabilities`、`monetary_funds`、
>    `inventory`、`accounts_receivable`、`total_assets`、`total_liabilities`）请调
>    `/api/financial/{stock_code}`（原始报表），而不是本接口。
> 4. **没有名为 `cash` 的字段**：货币资金的正确字段名是 **`monetary_funds`**（2026-09-29 起在
>    `/api/financial/{stock_code}` 提供），调用 `cash` 会拿不到数据。
> 5. 毛利率字段名带后缀：用 `gross_margin_ttm`，**没有**不带后缀的 `gross_margin`。

---

### 5. `/api/northbound/{stock_code}` 北向资金

**返回字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | string | 股票代码 |
| trade_date | string | 交易日期 |
| net_inflow | float | 当日净流入（元） |
| holding_shares | int | 持股数量（股） |
| holding_value | float | 持股市值（元） |
| holding_ratio | float | 持股占比（%） |
| inflow_5d | float | 5日累计净流入 |
| inflow_10d | float | 10日累计净流入 |
| inflow_30d | float | 30日累计净流入 |

> ⚠️ **个股北向资金数据止于 2024-08-16**：因监管政策调整，交易所自 2024-08-16 起停止披露
> 个股级北向持股明细，**这是政策原因，不是数据源故障，无需修复**。
> 2024-08-16 之后的**整体**（沪深港通合计）流向仍可通过 `/api/northbound/market` 获取，
> 该接口数据持续更新至今。

---

### 6. `/api/margin/{stock_code}` 融资融券

**返回字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | string | 股票代码 |
| trade_date | string | 交易日期 |
| rz_balance | float | 融资余额（元） |
| rz_change | float | 融资余额变动 |
| rz_change_pct | float | 融资余额变动百分比 |
| rq_balance | float | 融券余额（元） |
| rq_change | float | 融券余额变动 |
| rq_change_pct | float | 融券余额变动百分比 |
| total_balance | float | 融资融券余额合计 |
| total_change | float | 余额合计变动 |
| total_change_pct | float | 余额合计变动百分比 |

---

### 7. `/api/flow/{stock_code}` 主力资金流向

**返回字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | string | 股票代码 |
| trade_date | string | 交易日期 |
| main_net_amount | float | 主力净额（元）= 大单净额 + 超大单净额 |
| xlarge_net_amount | float | 超大单净额（元） |
| large_net_amount | float | 大单净额（元） |
| medium_net_amount | float | 中单净额（元） |
| small_net_amount | float | 小单净额（元） |
| main_net_ratio | float | 主力净占比（%） |
| xlarge_net_ratio | float | 超大单净占比（%） |
| large_net_ratio | float | 大单净占比（%） |
| medium_net_ratio | float | 中单净占比（%） |
| small_net_ratio | float | 小单净占比（%） |

> **口径与恒等式**（服务端采集时已自检）：
> 1. `main_net_amount` = `large_net_amount` + `xlarge_net_amount`
> 2. 四类单净额合计 ≈ 0（资金守恒：有买必有卖）
> 3. `main_net_ratio` ≈ `large_net_ratio` + `xlarge_net_ratio`
> 4. 本表**不含收盘价字段**——收盘价请关联 `/api/daily` 按 `(stock_code, trade_date)` 取，
>    采集时已做跨源一致性校验。

> ⚠️ **历史深度约 121 个交易日（约半年）**：数据源单次只提供最近约 121 个交易日，
> 更早的历史拿不到。表内数据由每日采集 UPSERT 累积，会随时间超过 121 天。
> 当日盘中采集的是临时值，收盘后次日采集会覆盖为最终值。

---

### 8. `/api/capital/{stock_code}` 股本数据

**返回字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | string | 股票代码 |
| record_date | string | 记录日期 |
| total_shares | int | 总股本（股） |

> **注意**：日期字段是 `record_date`（不是 `trade_date`）。此表仅存总股本，**没有流通股本字段**。

---

### 9. `/api/dividends/{stock_code}` 分红数据

**返回字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | string | 股票代码 |
| dividend_date | string | 除权除息日 |
| cash_per_share | float | 每股派息（元） |
| announcement_date | string | 公告日期 |

---

### 10. `/api/industry/{stock_code}` 行业信息

**返回字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | string | 股票代码 |
| industry_name | string | 行业名称（如"C34通用设备制造业"） |
| industry_level | string | 分类级别（"证监会行业分类"） |
| source | string | 数据来源（"BaoStock"） |
| update_date | string | 更新日期 |

---

### 11. `/api/dragon/{stock_code}` 龙虎榜

**说明**：按 `stock_code` 过滤，日期降序。采集采用**滚动窗口自愈**（每次重拉最近 20 天并 UPSERT），
历史空洞由 `scripts/backfill_dragon_tiger.py` 按月分块补齐（可回溯至 2016 年）。
注意：**大盘蓝筹自选股本就极少上榜**，接口返回空属正常选择效应，并非数据缺失。
同一只股票同日可能有多条（不同上榜原因），`list_type` 承载上榜原因以区分。

**返回字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int | 记录ID |
| stock_code | string | 股票代码 |
| trade_date | string | 交易日期 |
| list_type | string | 上榜类型 |
| reason | string | 上榜原因 |
| buy_amount | float | 买入金额 |
| sell_amount | float | 卖出金额 |
| net_amount | float | 净额 |
| institution_buy_ratio | float | 机构买入占比 |
| institution_sell_ratio | float | 机构卖出占比 |
| institution_net_ratio | float | 机构净额占比 |

---

### 12. `/api/risk/{stock_code}` 风险面/治理事件汇总

**返回结构**：`{pledge, holder_change, buyback, unlock, holder_num, summary, note?}`

**pledge.data（股权质押，周频快照，降序）**：

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | string | 股票代码 |
| trade_date | string | 快照交易日期（周频，约每周五） |
| pledge_ratio | float | 质押比例（占总股本 %） |
| pledge_shares | int | 质押股数（股） |
| pledge_market_value | float | 质押市值（元） |
| pledge_count | int | 质押笔数 |

**holder_change.data（股东增减持，按公告日降序）**：

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | string | 股票代码 |
| announcement_date | string | 公告日 |
| holder_name | string | 股东名称 |
| change_type | string | `增持` / `减持` |
| change_shares | int | 变动股数（股） |
| change_ratio | float | 变动占总股本比例（%） |
| hold_after_shares | int | 变动后持股数（股，可能为 null） |
| hold_after_ratio | float | 变动后持股比例（%，可能为 null） |
| change_start_date / change_end_date | string | 变动区间 |

**summary**：`latest_pledge_ratio`（最新质押比例）、`risk_level`
（无质押记录 / 低 ≤5% / 中 5~20% / 高 >20%）、`reduce_count_recent_90d`（近 90 天减持公告次数）。

**buyback.data（股票回购方案与进度，按公告日降序）**：

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | string | 股票代码 |
| announcement_date | string | 最新公告日 |
| progress | string | 实施进度：董事会预案/股东大会通过/股东大会否决/实施中/停止实施/完成实施 |
| plan_start_date | string | 回购起始时间 |
| price_cap | float | 计划回购价格（上限） |
| shares_lower / shares_upper | int | 计划回购数量区间（股，可能为 null） |
| amount_lower / amount_upper | float | 计划回购金额区间（元） |
| done_shares | int | 已回购股份数量（股） |
| done_amount | float | 已回购金额（元） |
| done_price_low / done_price_high | float | 已回购价格区间（元） |

**unlock.data（限售解禁批次，按解禁日降序，含未来计划）**：

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | string | 股票代码 |
| free_date | string | 解禁日（未来批次即"待解禁日历"） |
| free_type | string | 限售股类型（首发原股东/定增机构配售/股权激励等） |
| free_shares | int | 解禁数量（股） |
| actual_free_shares | int | 实际解禁数量（股；**未来批次为 null 属正常**） |
| actual_free_value | float | 实际解禁市值（元） |
| ratio_to_float | float | 占解禁前流通市值**小数**（0.05 = 5%，调用方需 ×100） |
| close_before | float | 解禁前一交易日收盘价 |

**holder_num.data（股东户数，按统计截止日降序）**：

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | string | 股票代码 |
| stat_date | string | 股东户数统计截止日 |
| holder_num / holder_num_prev | int | 本次/上次股东户数 |
| holder_num_change / change_ratio | - | 增减户数 / 增减比例（%） |
| avg_mktcap / avg_shares | float | 户均持股市值（元）/ 户均持股数量（股） |
| total_shares | int | 总股本 |
| notice_date | string | 公告日期 |

**summary 扩展字段**：`ongoing_buyback_count` / `ongoing_buyback_amount`（进行中回购方案数与已回购金额）、`unlock_upcoming_90d_count` / `unlock_upcoming_90d_shares`（未来 90 天解禁批次数与股数）、`holder_num_latest` / `holder_num_trend`（最新户数与近 4 期趋势）。

> - **零质押的股票 `pledge.count = 0` 属正常**（无质押记录即健康），不是数据缺失。
> - 无回购/解禁记录同样属正常（没有相关公告）；ETF 五段全为 0 属正常。
> - `holder_num` 持续减少 = 筹码集中（常被解读为主力吸筹信号）。
> - 数据源为东方财富 datacenter（质押/增减持/回购/解禁/股东户数），随每日数据更新自动累积。

---

## 五、调用方常见问题与修复建议

### 问题 1：股价 = 0.0

**排查顺序（先查覆盖度，再查字段）**：

1. **该股票在覆盖清单里吗？**（见"零、数据覆盖范围"）范围外股票 `count: 0`，调用方若写
   `data[0]` 会 IndexError，若写 `data[0].get("close", 0)` 会得 0。
2. **是否把估值接口当成了行情接口？** `/api/indicators/valuation` **不含 `close` 字段**，
   `valuation.get("close", 0)` 恒得 0。收盘价应取自 `/api/daily`、`/api/master/{code}` 的
   `close_price`，或 `/api/basic_info`。
3. **是否取错了数组下标？** 所有列表接口按日期**降序**，`data[0]` 是最新，
   `data[-1]` 是最旧（早期数据）。

**修复**：
```python
resp = requests.get(f"{BASE_URL}/api/daily/{code}?limit=1").json()
close = resp["data"][0]["close"] if resp.get("count") else None
if close is None:
    # 先判断是否在覆盖清单内, 再判断是否停牌/未采集
    print(f"{code} 无日线数据(不在覆盖清单/停牌/未采集)")
```

**验证**：22 只覆盖股票的最新 `close` 全部非 0 非空（如 sz300750 = 291.99，sz002594 = 83.35）。

---

### 问题 2：MA5/MA20 缺失

**根因**：调用了错误接口，或字段名拼写错误。

**修复**：
```python
# 技术指标在 /api/indicators/technical/ 接口，不在 /api/daily/
resp = requests.get(f"{BASE_URL}/api/indicators/technical/{code}?limit=1").json()
if resp["count"] > 0:
    ma5 = resp["data"][0]["ma5"]   # 小写 ma5
    ma20 = resp["data"][0]["ma20"] # 小写 ma20
```

---

### 问题 3：流动比率/速动比率 = 0

**根因**：调用方用 `current_assets / current_liabilities` 自行相除，但
`/api/indicators/financial`（中间表）**不含这两个原始字段**，相除必然得 0 或异常值。

**修复**：
```python
# 直接取服务端算好的比率(中间表接口)
r = requests.get(f"{BASE_URL}/api/indicators/financial/{code}?limit=1").json()
d = r["data"][0]
current_ratio = d["current_ratio"]   # 已算好, 直接用
quick_ratio   = d["quick_ratio"]

# 若要原始科目, 用原始报表接口
raw = requests.get(f"{BASE_URL}/api/financial/{code}?limit=1").json()["data"][0]
current_assets      = raw["current_assets"]
current_liabilities = raw["current_liabilities"]
```

**验证**：两表口径一致（sz002594 手算 0.8705 = 中间表 0.8705；sz300750 1.5595 = 1.5595）。

> 注意：货币资金字段名为 **`monetary_funds`**（`/api/financial/{stock_code}`，2026-09-29 起提供），
> 没有 `cash` 字段。

---

### 问题 4：毛利率缺失

**根因**：字段名带后缀，调用方用了不带后缀的 `gross_margin`。

**修复**：
```python
# 错误
gross_margin = data["gross_margin"]  # KeyError

# 正确（推荐用 TTM 口径）
gross_margin = data.get("gross_margin_ttm")  # TTM 毛利率
# 或
gross_margin = data.get("gross_margin_annual")  # 年度毛利率
```

---

### 问题 5：PE_TTM 为负值

**根因**：公司亏损时 PE 为负是正常现象。

**修复**：
```python
pe_ttm = data["pe_ttm"]
if pe_ttm is None:
    pe_status = "无数据"
elif pe_ttm < 0:
    pe_status = "公司亏损，PE不适用"
else:
    pe_status = f"PE={pe_ttm:.2f}"
```

---

### 问题 6：北向资金缺失

**根因**：个股级北向资金自 **2024-08-16** 起停止披露（监管政策），非数据源故障。

**修复**：区分历史与最新，并改用整体流向兜底。
```python
resp = requests.get(f"{BASE_URL}/api/northbound/{code}").json()
if resp["count"] == 0:
    nb_status = "非沪深港通标的"
else:
    nb_latest = resp["data"][0]        # 最新日期不会晚于 2024-08-16
    if nb_latest["trade_date"] < "2024-08-16":
        nb_status = "个股北向数据已于 2024-08-16 停止披露（政策原因）"

# 需要最新的北向整体资金, 用这个接口(数据持续更新)
market = requests.get(f"{BASE_URL}/api/northbound/market?limit=5").json()
```

---

### 问题 7：换手率缺失

**根因**：`turnover` 已正常填充，NULL 仅出现在 ETF（无流通股本概念）和
个别 `outstanding_share` 缺失的日期。**不再是"恒为 null"。**

**修复**：直接取用；为空时按上文口径回退计算。
```python
daily = requests.get(f"{BASE_URL}/api/daily/{code}?limit=1").json()["data"][0]
turnover = daily["turnover"]

if turnover is None and daily.get("outstanding_share"):
    turnover = daily["volume"] / daily["outstanding_share"] * 100   # %
elif turnover is None:
    turnover = None   # ETF 或股本缺失, 标注 N/A 而非 0
```

---

### 问题 8：总股本取不到

**根因**：股票不在覆盖清单内（范围内 21 只均有值）。注意日期字段是 `record_date`。

**修复**：
```python
resp = requests.get(f"{BASE_URL}/api/capital/{code}").json()
if resp["count"] == 0:
    print(f"{code} 不在数据覆盖范围内（见文档「零、数据覆盖范围」）")
    total_shares = None
else:
    total_shares = resp["data"][0]["total_shares"]   # 降序, [0] 即最新
```

---

### 问题 9：同行业可比公司只有 1 只

**根因**：旧版本 `stock_industry` 只登记自选股，样本不足。

**已修复**：现全市场行业成分已入库（5223 只 / 84 个行业，最大行业组 666 只）。

**修复**：先查行业名，再取成分股。
```python
info = requests.get(f"{BASE_URL}/api/industry/{code}").json()
industry_name = info["data"][0]["industry_name"]          # 如 "C36汽车制造业"

peers = requests.get(
    f"{BASE_URL}/api/industry/{quote(industry_name)}/stocks"
).json()
peer_codes = [x["stock_code"] for x in peers["data"]]     # 全市场同业清单
```
> 实操建议：可比公司通常还需叠加规模/业务筛选。行业成分已给全量，
> 可再用 `/api/basic_info?stock_codes=...` 批量取价与估值做二次筛选。

---

### 问题 10：主力资金数据为空或历史不足

**根因**：`capital_flow` 为 2026-09-29 新建表。数据源（东方财富）单次只提供
最近约 **121 个交易日**历史，更早的拿不到；且该数据源 **IP 限流极严**，
批量请求会触发临时封禁（实测：闲置一天后单发可成功，5 分钟后第 2 笔即被再封）。

**现状**：历史回填采用慢速滴灌（每成功 1 只等 20+ 分钟）逐步补齐，
未覆盖的股票短期会返回 `count: 0`。每日增量采集随每轮数据更新自动累积。

**调用方建议**：对主力资金字段做判空处理，勿假设所有股票都有全量历史；
判断覆盖进度可直接调 `/api/flow/{code}` 看返回的最早日期。

---

## 六、字段命名约定

1. **全部小写 + 下划线**：如 `pe_ttm`、`gross_margin_annual`（非驼峰命名）
2. **日期字段**：`trade_date`（交易日期）、`report_date`（报告期）、`record_date`（记录日期）、`announcement_date`（公告日期）、`dividend_date`（除权日）
3. **后缀约定**：
   - `_ttm`：滚动十二个月（Trailing Twelve Months）
   - `_annual`：年度口径
   - `_q`：单季度
   - `_yoy`：同比
   - `_qoq`：环比
   - `_cagr_3y`：三年复合增长率

---

## 七、已下线接口

以下接口**代码已移除**，调用会返回 404，请勿依赖：

- `/api/metadata/*`（数据字典，原 `src/metadata/` 已删除）
- `/api/quality/*`（质量检查，原 `src/quality/` 已删除）

字段定义请直接查阅 `src/database/models.py` 的建表语句。

---

## 八、快速验证

仓库自带冒烟测试脚本，覆盖上述全部易错点（收盘价、估值口径与日期对齐、
两表流动比率一致性、股本、行业样本量），启动 API 后执行：

```bash
python api/main.py                 # 另开一个窗口启动服务
python scripts/smoke_test_api.py   # 全量断言, 全 PASS 即接口正常
```

手工快速核对某只股票：

```python
import requests
s = requests.Session(); s.trust_env = False   # 绕过系统代理, 否则 localhost 会被代理拦截返回 502

BASE = "http://127.0.0.1:8001"
code = "sz300750"

r = s.get(f"{BASE}/api/daily/{code}", params={"limit": 1}).json()
print("收盘价:", r["data"][0]["close"], "换手率:", r["data"][0]["turnover"])

v = s.get(f"{BASE}/api/indicators/valuation/{code}", params={"limit": 1}).json()
print("估值日:", v["data"][0]["trade_date"], "PE_TTM:", v["data"][0]["pe_ttm"],
      "| 注意: 估值接口不含 close 字段")

f = s.get(f"{BASE}/api/indicators/financial/{code}", params={"limit": 1}).json()
print("流动比率:", f["data"][0]["current_ratio"], "存货:", f["data"][0]["inventory"])

c = s.get(f"{BASE}/api/capital/{code}").json()
print("总股本:", c["data"][0]["total_shares"])
```

> **踩坑提示**：若用 `requests` 请求 `localhost` 得到 `502 upstream connect failed`，
> 是本机 HTTP 代理拦截所致，设置 `session.trust_env = False` 即可。
