import duckdb
from stock_data_collector import StockDataCollector

# 连接数据库
conn = duckdb.connect('stock_data.duckdb')

# 清除京东方的更新记录，让它重新采集
print("清除京东方的更新记录...")
conn.execute("DELETE FROM update_log WHERE stock_code = 'sz000725'")
print("清除完成")

# 关闭连接
conn.close()

print("\n开始重新采集京东方数据...")
collector = StockDataCollector(db_path="stock_data.duckdb")

# 先重新采集财务数据
print("\n1. 重新采集财务数据...")
collector.collect_financial_data('sz000725')

# 重新计算财务中间指标
print("\n2. 重新计算财务中间指标...")
collector.calculate_financial_intermediate('sz000725')

# 重新计算估值指标
print("\n3. 重新计算估值指标...")
collector.calculate_valuation_indicators('sz000725')

print("\n京东方数据重新采集完成！")

# 验证数据
print("\n=== 验证京东方 2026-03-04 数据 ===")
conn = duckdb.connect('stock_data.duckdb')

# 股价数据
df_price = conn.execute("""
    SELECT trade_date, close
    FROM stock_daily
    WHERE stock_code = 'sz000725' AND trade_date = '2026-03-04'
""").fetchdf()
print(f"\n股价数据：{df_price}")

# 财务中间指标（2025年3季报）
df_inter = conn.execute("""
    SELECT report_date, report_type, equity, equity_parent, bvps, total_shares
    FROM financial_intermediate
    WHERE stock_code = 'sz000725' AND report_date <= '2026-03-04'
    ORDER BY report_date DESC
    LIMIT 3
""").fetchdf()
print(f"\n财务指标：\n{df_inter}")

# 估值指标
df_val = conn.execute("""
    SELECT trade_date, pe_ttm, pb
    FROM valuation_indicators
    WHERE stock_code = 'sz000725' AND trade_date = '2026-03-04'
""").fetchdf()
print(f"\n估值指标：{df_val}")

conn.close()
