"""测试多数据源架构完整流程"""
import sys
sys.path.insert(0, '.')

from pathlib import Path
from src.config import DB_PATH, PARQUET_DIR
from src.database.models import init_tables
from src.database.operations import DatabaseOperations
from src.database.parquet_store import ParquetStore
from src.collector.multi_source_collector import MultiSourceCollector
from src.indicators.technical import TechnicalIndicatorCalculator
from src.indicators.financial import FinancialIndicatorCalculator
from src.indicators.valuation import ValuationIndicatorCalculator

# 使用临时数据库
test_db_path = DB_PATH.parent / "test_multi_source.duckdb"
if test_db_path.exists():
    test_db_path.unlink()

db_ops = DatabaseOperations(test_db_path)
parquet_store = ParquetStore(PARQUET_DIR)
init_tables(db_ops.conn)

stock_code = "sh600519"
collector = MultiSourceCollector(db_ops, parquet_store, "20250501")

print("=" * 60)
print("多数据源架构测试 - 茅台(sh600519)")
print("=" * 60)

# 1. 采集数据
print("\n--- 1. 数据采集 ---")
collector.collect_stock(stock_code)

# 2. 检查采集结果
print("\n--- 2. 数据检查 ---")
tables = ["stock_daily", "financial_statements", "stock_capital", "stock_industry",
          "dividends", "announcements", "margin_trading", "dragon_tiger"]

for table in tables:
    try:
        count = db_ops.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE stock_code = '{stock_code}'").fetchone()[0]
        status = "OK" if count > 0 else "EMPTY"
        print(f"  {table}: {count} 条记录 [{status}]")
    except Exception as e:
        print(f"  {table}: 查询失败 - {e}")

# 3. 检查估值数据
print("\n--- 3. 估值数据检查 ---")
try:
    df = db_ops.conn.execute("""
        SELECT stock_code, trade_date, pe_ttm, pb
        FROM valuation_indicators
        WHERE stock_code = 'sh600519'
        ORDER BY trade_date DESC LIMIT 5
    """).fetchdf()
    print(df.to_string())
except Exception as e:
    print(f"估值查询失败: {e}")

# 4. 日线数据样本
print("\n--- 4. 日线数据样本 ---")
try:
    df = db_ops.conn.execute("""
        SELECT trade_date, open, high, low, close, volume
        FROM stock_daily
        WHERE stock_code = 'sh600519'
        ORDER BY trade_date DESC LIMIT 5
    """).fetchdf()
    print(df.to_string())
except Exception as e:
    print(f"日线查询失败: {e}")

# 5. 财务数据样本
print("\n--- 5. 财务数据样本 ---")
try:
    df = db_ops.conn.execute("""
        SELECT report_date, report_type, total_revenue, net_profit, eps, roe, total_assets
        FROM financial_statements
        WHERE stock_code = 'sh600519'
        ORDER BY report_date DESC LIMIT 5
    """).fetchdf()
    print(df.to_string())
except Exception as e:
    print(f"财务查询失败: {e}")

# 6. 计算指标
print("\n--- 6. 计算指标 ---")
tech_calc = TechnicalIndicatorCalculator(db_ops)
fin_calc = FinancialIndicatorCalculator(db_ops)
val_calc = ValuationIndicatorCalculator(db_ops)

try:
    tech_calc.calculate_for_stock(stock_code)
    print("  技术指标: OK")
except Exception as e:
    print(f"  技术指标: 失败 - {e}")

try:
    fin_calc.calculate_for_stock(stock_code)
    print("  财务指标: OK")
except Exception as e:
    print(f"  财务指标: 失败 - {e}")

try:
    val_calc.calculate_for_stock(stock_code)
    print("  估值指标: OK")
except Exception as e:
    print(f"  估值指标: 失败 - {e}")

# 7. 关闭连接
collector.close()
db_ops.close()

print("\n" + "=" * 60)
print("测试完成!")
print("=" * 60)
