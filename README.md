# 股票数据采集与分析系统

基于多数据源（mootdx + BaoStock + AKShare + 腾讯财经）+ DuckDB 的股票数据采集、存储和分析系统。

## 数据来源
**本项目的数据（DuckDB 数据库文件 `stock_data.duckdb`）来源于配套的本地采集项目**，该项目包含完整的数据采集、增量更新与存储逻辑。

- **采集项目仓库**：[https://github.com/WZB1320/A-Shares-data.git](https://github.com/WZB1320/A-Shares-data.git)
- **数据流转**：采集项目 → DuckDB 数据库 → 本项目的 API 服务对外提供数据查询
- **更新机制**：如需获取最新行情或财报数据，请先运行采集项目进行增量更新，随后启动本 API 服务即可读取最新数据。

## 数据配置
本项目只采集了部分股票相关数据，可以根据需要配置不同股票列表获取数据，配置方式在 `src/config.py` 中的 `STOCK_CODES` 列表。

## 功能特点

### 数据采集
- **多数据源冗余**：4个数据源主备切换，确保采集成功率
- **日线行情**：开盘价、收盘价、最高价、最低价、成交量、成交额、复权因子
- **财务报表**：利润表、资产负债表、现金流量表（含扣非归母净利润、利息支出等详细指标）
- **公告数据**：历史公告列表
- **分红数据**：分红记录
- **股本数据**：总股本、流通股本等
- **北向资金**：北向资金流入流出
- **融资融券**：融资融券余额
- **龙虎榜**：龙虎榜上榜记录及详细交易数据

### 数据治理
- **数据字典**：`src/metadata/data_dictionary.py` 集中维护 15 张表、247 个字段的权威定义（含数据来源、计算公式、单位）
- **数据血缘**：每个字段明确标注来源（mootdx/baostock/tencent/akshare/calculated），可追溯至采集器
- **增量更新**：`update_log` 表驱动增量采集，避免重复拉取全量数据
- **备份与校验**：每次运行后自动备份 DuckDB 文件，校验 15 张表数据完整性
- **Parquet 双存储**：DuckDB + Parquet 双副本，防止数据库损坏

### 指标计算
- **盈利能力**：ROE（净资产收益率）、ROA（总资产收益率）、毛利率、净利率（归母/扣非）
- **成长能力**：营收/净利润同比增速、3年复合增长率
- **杜邦分析**：三层分解（净利率 × 总资产周转率 × 权益乘数）
- **营运能力**：存货/应收/应付周转率、周转天数、现金周期
- **现金流质量**：盈利现金保障倍数、自由现金流（FCF）、FCF利润覆盖率、现金利息保障倍数
- **技术指标**：MA、MACD、RSI、KDJ、BOLL、布林带等

### 口径支持
- 年度口径（年报）
- TTM 口径（滚动12个月）

## 项目结构

```
A-Shares-data/
├── src/                    # 核心代码
│   ├── config.py           # 配置文件(股票列表/数据源/路径)
│   ├── main.py             # 采集主入口
│   ├── scheduler.py        # 流程调度(采集→计算→备份)
│   ├── collector/          # 数据采集模块
│   │   ├── base.py             # 基类(重试/回退/安全转换)
│   │   ├── multi_source_collector.py  # 多源编排器
│   │   ├── mootdx_collector.py    # 通达信TCP(日线行情)
│   │   ├── baostock_collector.py  # BaoStock(股本/行业/分红)
│   │   ├── tencent_collector.py   # 腾讯财经(PE/PB估值)
│   │   └── eastmoney.py           # 东方财富(财务/融资/龙虎榜)
│   ├── database/           # 数据库操作
│   │   ├── models.py            # DDL建表
│   │   ├── operations.py        # 增删改查
│   │   ├── parquet_store.py     # Parquet双存储
│   │   └── backup.py            # 备份与完整性校验
│   ├── indicators/         # 指标计算
│   │   ├── base.py              # 基类
│   │   ├── technical.py         # 技术指标(30+个)
│   │   ├── financial.py         # 财务指标(80+个)
│   │   └── valuation.py         # 估值指标(PE/PB/PS/股息率)
│   └── metadata/           # 数据字典
│       └── data_dictionary.py   # 所有表和字段的权威定义
├── api/                    # REST API 服务
│   └── main.py             # FastAPI 应用(15+接口)
├── data/                   # 数据存储
│   ├── stock_data.duckdb       # DuckDB数据库
│   ├── parquet/               # Parquet持久化
│   ├── samples/               # 数据采样
│   └── backups/               # 数据库备份
├── logs/                   # 日志
│   └── stock_collector.log      # 运行日志
└── scripts/                # 脚本
    ├── run_update.py           # 一键更新脚本
    └── _archive/               # 归档的历史脚本
```

## 快速开始

### 环境准备
```bash
pip install -r requirements.txt
# 或单独安装
pip install akshare mootdx baostock duckdb pandas fastapi uvicorn requests
```

### 配置股票列表
编辑 `src/config.py`：
```python
STOCK_CODES = [
    "sh600519",  # 贵州茅台
    "sh513700",  # ETF（支持）
    # 添加更多代码
]
START_DATE = "20160510"
```

### 运行数据采集（一键更新至最新）
```bash
# 方式一：一键更新（Windows，双击 update_data.bat）
#   脚本会自动: 停止 API(释放数据库写锁) → 增量采集13只股票 → 指标计算 → 备份 → 自动重启 API
#   日志: logs\update_YYYYMMDD_HHMMSS.log

# 方式二：命令行手动运行
python -m src.main

# 方式三：仅更新指定股票 / 指定日期区间（详见 scripts/run_update.py）
python scripts/run_update.py --stocks sh600519 sz002843
```

### 启动 REST API 服务
```bash
# 方式一：一键启动（Windows，双击 start_api.bat）
# 方式二：命令行启动
python -m api.main
```
服务会自动检测可用端口（默认 8001），启动后访问 http://localhost:8001/docs 查看 Swagger 文档。

---

## REST API 接口说明

### 服务启动
```bash
# 启动API服务（默认端口 8001，自动检测可用端口）
python -m api.main

# 或手动指定端口
python -m uvicorn api.main:app --host 0.0.0.0 --port 8001

# 访问文档
http://localhost:8001/docs  # Swagger UI
http://localhost:8001/redoc # Redoc
```

### 公共接口
| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/health` | GET | 服务健康检查 |
| `/api/stocks` | GET | 获取所有股票列表(默认仅代码数组;传 `with_price=true` 可返回含最新收盘价等完整基础信息) |
| `/api/basic_info` | GET | **批量获取股票基础信息(含最新收盘价 close_price)**,回测/研究项目取 `price_at_analysis` 请优先用此接口 |
| `/api/latest` | GET | 获取最新数据日期（支持 `stock_code` 参数） |

> 💡 回测闭环排雷提示：旧版本 `/api/stocks`、`/api/master` 均不含价格字段，会导致 `price_at_analysis` 全空。请改用 `/api/basic_info`，或调用 `/api/stocks?with_price=true`、`/api/master/{stock_code}`（已补齐 `close_price` 字段）。

### 行情与基本面接口
| 接口 | 方法 | 说明 | 支持参数 |
|------|------|------|----------|
| `/api/daily/{stock_code}` | GET | 日线行情 | `start_date`, `end_date`, `limit` |
| `/api/financial/{stock_code}` | GET | 财务报表 | `start_date`, `end_date`, `report_type` |
| `/api/indicators/technical/{stock_code}` | GET | 技术指标 | `start_date`, `end_date`, `limit` |
| `/api/indicators/financial/{stock_code}` | GET | 财务指标 | `start_date`, `end_date` |
| `/api/indicators/valuation/{stock_code}` | GET | 估值指标 | `start_date`, `end_date`, `limit` |

### 资金与交易接口
| 接口 | 方法 | 说明 | 支持参数 |
|------|------|------|----------|
| `/api/northbound/{stock_code}` | GET | 北向资金 | `start_date`, `end_date`, `limit` |
| `/api/margin/{stock_code}` | GET | 融资融券数据 | `start_date`, `end_date`, `limit` |
| `/api/dragon/{stock_code}` | GET | 龙虎榜数据 | `start_date`, `end_date`, `limit` |

### 基础信息接口
| 接口 | 方法 | 说明 | 支持参数 |
|------|------|------|----------|
| `/api/dividends/{stock_code}` | GET | 分红数据 | — |
| `/api/announcements/{stock_code}` | GET | 公告数据 | `limit` |
| `/api/capital/{stock_code}` | GET | 股本数据 | — |
| `/api/industry/{stock_code}` | GET | 股票所属行业 | — |
| `/api/industry/list` | GET | 获取所有行业列表 | — |
| `/api/industry/{industry_name}/stocks` | GET | 获取某行业下的所有股票 | — |

### 参数说明
- `start_date` / `end_date`: 日期格式 `YYYY-MM-DD`
- `limit`: 返回条数，默认 10000，最大 100000
- `report_type`: 财务报表类型，可选 `all`/`income`/`balance`/`cashflow`

### 返回格式
```json
{
  "stock_code": "sh600519",
  "count": 100,
  "data": [
    { "trade_date": "2026-05-12", "close": 1361.33, ... },
    ...
  ]
}
```

---

## 接口详细说明与示例

### 1. 公共接口

#### 健康检查
```bash
# 请求
GET /api/health

# 返回
{
  "status": "ok",
  "db_path": "E:\\AI\\data\\Stockdata\\data\\stock_data.duckdb"
}
```

#### 获取股票列表
```bash
# 默认: 仅返回代码数组(向后兼容)
GET /api/stocks
# 返回: { "stocks": ["sh513700","sh600089",...], "count": 12 }

# 推荐: 返回含最新收盘价的完整信息(回测项目取 price_at_analysis 请用此模式)
GET /api/stocks?with_price=true
GET /api/stocks?with_price=true&stock_codes=sh600519,sh600089
# 返回: { "stocks": [{ "stock_code":"sh600519","stock_name":"贵州茅台","close_price":1361.33,"pct_chg":1.23,"pe_ttm":28.5,... }], "count": N, "note": "close_price 即最新收盘价,可直接用于回测 price_at_analysis 字段" }
```

#### 批量获取基础信息(含价格)—推荐回测项目使用
```bash
# 全部股票
GET /api/basic_info

# 按代码过滤
GET /api/basic_info?stock_codes=sh600519,sh600089

# 返回字段(关键字段):
#  stock_code / stock_name / stock_name_cn / market / board / status / is_etf
#  price_date / open_price / close_price(映射 price_at_analysis) / high_price / low_price
#  volume / turnover / pct_chg / pe_ttm / pb
#
# 返回格式:
# {
#   "count": 2,
#   "data": [
#     { "stock_code":"sh600519","stock_name":"贵州茅台","close_price":1361.33,"pct_chg":1.23,... },
#     ...
#   ],
#   "price_field_hint": "字段 close_price 即最新收盘价,外部项目可直接映射到 price_at_analysis"
# }
```

#### 获取最新数据日期
```bash
# 请求 - 所有股票
GET /api/latest

# 请求 - 单只股票
GET /api/latest?stock_code=sh600089

# 返回
{
  "latest": [
    {"stock_code": "sh600089", "latest_date": "2026-05-13"},
    ...
  ]
}
```

### 2. 融资融券接口（新增）

#### 获取融资融券数据
```bash
# 请求
GET /api/margin/{stock_code}
GET /api/margin/sh600089?start_date=2026-04-01&end_date=2026-05-13&limit=50

# 返回
{
  "stock_code": "sh600089",
  "count": 50,
  "data": [
    {
      "stock_code": "sh600089",
      "trade_date": "2026-05-13",
      "rz_balance": 123456789.0,
      "rz_change": 1234567.0,
      "rz_change_pct": 1.01,
      "rq_balance": null,
      "rq_change": null,
      "rq_change_pct": null,
      "total_balance": 123456789.0,
      "total_change": 1234567.0,
      "total_change_pct": 1.01
    },
    ...
  ]
}
```

**字段说明：**
- `rz_balance`: 融资余额
- `rz_change`: 融资余额变化
- `rz_change_pct`: 融资余额变化率
- `rq_balance`: 融券余额
- `total_balance`: 融资融券总余额

### 3. 龙虎榜接口（新增）

#### 获取龙虎榜数据
```bash
# 请求
GET /api/dragon/{stock_code}
GET /api/dragon/sh600089?start_date=2026-01-01&end_date=2026-05-13

# 返回
{
  "stock_code": "sh600089",
  "count": 0,
  "data": []
}
```

**字段说明：**
- `trade_date`: 上榜日期
- `list_type`: 上榜类型
- `reason`: 上榜原因
- `buy_amount`: 龙虎榜买入额
- `sell_amount`: 龙虎榜卖出额
- `net_amount`: 龙虎榜净买额

### 4. 行业信息接口（新增）

#### 获取单只股票行业
```bash
# 请求
GET /api/industry/{stock_code}
GET /api/industry/sh600887

# 返回
{
  "stock_code": "sh600887",
  "count": 1,
  "data": [
    {
      "stock_code": "sh600887",
      "industry_name": "饮料乳品",
      "industry_level": "东财行业",
      "source": "Eastmoney",
      "update_date": "2026-05-14"
    }
  ]
}
```

#### 获取行业列表
```bash
# 请求
GET /api/industry/list

# 返回
{
  "count": 2,
  "data": [
    {
      "industry_name": "通用设备",
      "stock_count": 1,
      "latest_update": "2026-05-14"
    },
    {
      "industry_name": "饮料乳品",
      "stock_count": 1,
      "latest_update": "2026-05-14"
    }
  ]
}
```

#### 获取行业下的股票
```bash
# 请求
GET /api/industry/{industry_name}/stocks
GET /api/industry/饮料乳品/stocks

# 返回
{
  "industry_name": "饮料乳品",
  "count": 1,
  "data": [
    {
      "stock_code": "sh600887",
      "industry_name": "饮料乳品",
      "source": "Eastmoney",
      "update_date": "2026-05-14"
    }
  ]
}
```

### 5. 股本接口（新增）

#### 获取股本数据
```bash
# 请求
GET /api/capital/{stock_code}

# 返回
{
  "stock_code": "sh600089",
  "count": 0,
  "data": []
}
```

---

## Python 调用示例

### 完整示例代码
```python
import requests
import json

base_url = "http://127.0.0.1:8001"

# 1. 获取股票列表
print("=== 股票列表 ===")
stocks_resp = requests.get(f"{base_url}/api/stocks").json()
print(f"共 {stocks_resp['count']} 只股票: {stocks_resp['stocks']}")

# 2. 获取融资融券数据
stock_code = "sh600089"
print(f"\n=== {stock_code} 融资融券数据 ===")
margin_resp = requests.get(
    f"{base_url}/api/margin/{stock_code}",
    params={"limit": 5}
).json()
print(f"共 {margin_resp['count']} 条数据")
for item in margin_resp['data']:
    print(f"{item['trade_date']}: 融资余额 {item['rz_balance']:,.0f}")

# 3. 获取行业信息
print(f"\n=== {stock_code} 行业信息 ===")
industry_resp = requests.get(f"{base_url}/api/industry/{stock_code}").json()
if industry_resp['count'] > 0:
    print(f"所属行业: {industry_resp['data'][0]['industry_name']}")
else:
    print("暂无行业信息")

# 4. 获取行业列表
print("\n=== 行业列表 ===")
industry_list = requests.get(f"{base_url}/api/industry/list").json()
for industry in industry_list['data']:
    print(f"- {industry['industry_name']}: {industry['stock_count']} 只股票")

# 5. 获取日线行情
print(f"\n=== {stock_code} 最近3天行情 ===")
daily_resp = requests.get(
    f"{base_url}/api/daily/{stock_code}",
    params={"limit": 3}
).json()
for item in daily_resp['data']:
    print(f"{item['trade_date']}: 开盘 {item['open']}, 收盘 {item['close']}, 涨跌 {item['pct_chg']}%")
```

### 其他接口调用示例

#### 获取财务指标
```python
resp = requests.get(
    f"{base_url}/api/indicators/financial/sh600519",
    params={"start_date": "2025-01-01"}
).json()
print(f"财务指标数: {resp['count']}")
```

#### 获取北向资金
```python
resp = requests.get(
    f"{base_url}/api/northbound/sh600089",
    params={"limit": 10}
).json()
```

#### 获取技术指标
```python
resp = requests.get(
    f"{base_url}/api/indicators/technical/sh600089",
    params={"start_date": "2026-05-01"}
).json()
```

## 数据库表结构

> 详细的字段定义、数据来源、计算公式请参考 [数据字典](src/metadata/data_dictionary.py)。`ALL_TABLES` 变量包含全部 15 张表、247 个字段的完整定义。

| 表名 | 说明 | 主键 | 字段数 |
|------|------|------|--------|
| stock_daily | 股票日线行情数据 | stock_code, trade_date | 21 |
| financial_statements | 财务报表原始数据 | stock_code, report_date, report_type | 19 |
| financial_intermediate | 财务指标计算结果 | stock_code, report_date, report_type | 89 |
| technical_indicators | 技术指标(30+个) | stock_code, trade_date | 39 |
| valuation_indicators | 估值指标 | stock_code, trade_date | 13 |
| announcements | 公告数据 | stock_code, announcement_date, title | 6 |
| dividends | 分红数据 | stock_code, dividend_date | 4 |
| stock_capital | 股本数据 | stock_code, record_date | 3 |
| northbound_flow | 北向资金 | stock_code, trade_date | 9 |
| margin_trading | 融资融券 | stock_code, trade_date | 11 |
| dragon_tiger | 龙虎榜 | stock_code, trade_date, list_type | 11 |
| dragon_tiger_detail | 龙虎榜明细 | id | 8 |
| stock_industry | 股票行业信息 | stock_code | 5 |
| column_metadata | 字段元数据 | table_name, column_name | 4 |
| update_log | 更新记录 | stock_code, data_type | 5 |

### 数据来源分工

| 数据源 | 说明 | 采集的数据 |
|--------|------|-----------|
| mootdx | TCP直连通达信(零鉴权) | 日线行情(OHLCV) |
| BaoStock | 免费证券数据 | 股本、行业分类、分红 |
| 腾讯财经 | 零鉴权HTTP接口 | PE(TTM)/PB/总市值/换手率 |
| AKShare(Sina) | 新浪三大报表 | 利润表、资产负债表、现金流量表 |
| AKShare(东方财富) | 东方财富EM | 补充字段(扣非净利润、利息支出) |
| AKShare | 独有API | 融资融券、公告、龙虎榜 |

## 使用示例

### 使用数据字典
```python
from src.metadata.data_dictionary import ALL_TABLES, TABLE_MAP, SOURCE_INDEX

# 查看某张表的所有字段
daily = TABLE_MAP["stock_daily"]
for f in daily.fields:
    print(f"{f.column_name}: {f.display_name} ({f.unit}) - {f.source.value}")

# 按数据来源查看所有字段
for table, col, name in SOURCE_INDEX[DataSource.MOOTDX]:
    print(f"  {table}.{col} = {name}")

# 查看所有派生计算字段及公式
from src.metadata.data_dictionary import DERIVED_FIELDS
for table, col, name, formula in DERIVED_FIELDS[:5]:
    print(f"  {table}.{col}: {name} => {formula}")
```

### 查询财务数据
```python
import duckdb
conn = duckdb.connect('data/stock_data.duckdb')

# 查询贵州茅台的财务指标
df = conn.execute("""
    SELECT report_date, roe_annual, roa_annual, revenue_yoy_annual
    FROM financial_intermediate
    WHERE stock_code = 'sh600519' AND roe_annual IS NOT NULL
    ORDER BY report_date DESC
""").fetchdf()
print(df)
```

### 查询技术指标
```python
# 查询贵州茅台的日线和MA5
df = conn.execute("""
    SELECT trade_date, close, ma5, ma10, ma20, macd
    FROM stock_daily d LEFT JOIN technical_indicators t
    ON d.stock_code = t.stock_code AND d.trade_date = t.trade_date
    WHERE d.stock_code = 'sh600519'
    ORDER BY trade_date DESC LIMIT 100
""").fetchdf()
```

## 依赖
- mootdx：通达信行情数据（TCP直连，稳定可靠）
- baostock：股本/行业/分红数据
- akshare：财务数据、融资融券、公告、龙虎榜
- 腾讯财经：估值指标 PE/PB
- duckdb：列式数据库
- pandas：数据处理
- fastapi：REST API 框架
- uvicorn：ASGI 服务器
- requests：HTTP 请求

## 许可证
本项目仅供学习研究使用。
