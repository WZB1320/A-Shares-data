# 股票数据采集与分析系统

基于 akshare + DuckDB 的股票数据采集、存储和分析系统。

## 功能特点

### 数据采集
- **日线行情**：开盘价、收盘价、最高价、最低价、成交量、成交额、复权因子
- **财务报表**：利润表、资产负债表、现金流量表（含扣非归母净利润、利息支出等详细指标）
- **公告数据**：历史公告列表
- **分红数据**：分红记录
- **股本数据**：总股本、流通股本等
- **北向资金**：北向资金流入流出
- **融资融券**：融资融券余额
- **龙虎榜**：龙虎榜上榜记录及详细交易数据

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
Stockdata/
├── src/                    # 核心代码
│   └── stock_data_collector.py  # 主程序
├── data/                   # 数据存储
│   └── stock_data.duckdb       # DuckDB数据库
├── logs/                   # 日志
│   └── stock_collector.log      # 运行日志
└── scripts/                # 临时脚本
    ├── check_*.py          # 数据检查脚本
    ├── debug_*.py          # 调试脚本
    ├── fix_*.py            # 修复脚本
    └── ...
```

## 快速开始

### 环境准备
```bash
pip install akshare duckdb pandas
```

### 配置
编辑 `src/stock_data_collector.py` 中的配置区域：
```python
STOCK_CODES = [
    "sh600519",  # 贵州茅台
    "sz000001",  # 平安银行
    # 添加更多股票代码
]
START_DATE = "20160510"
```

### 运行
```bash
cd Stockdata
python src/stock_data_collector.py
```

## 数据库表结构

| 表名 | 说明 |
|------|------|
| stock_daily | 股票日线行情数据 |
| financial_statements | 财务报表原始数据 |
| financial_intermediate | 财务指标计算结果 |
| technical_indicators | 技术指标 |
| valuation_indicators | 估值指标 |
| announcements | 公告数据 |
| dividends | 分红数据 |
| stock_capital | 股本数据 |
| northbound_flow | 北向资金 |
| margin_trading | 融资融券 |
| dragon_tiger | 龙虎榜 |
| dragon_tiger_detail | 龙虎榜详细 |
| column_metadata | 元数据 |
| update_log | 更新记录 |

## 使用示例

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
- akshare：金融数据采集
- duckdb：列式数据库
- pandas：数据处理
- logging：日志

## 许可证
本项目仅供学习研究使用。
