import duckdb

conn = duckdb.connect('stock_data.duckdb')

print("=" * 70)
print("sz000725 京东方 A - 2026-03-04 PB 异常调查")
print("=" * 70)

# 1. 当日收盘价
df_price = conn.execute("""
    SELECT trade_date, close FROM stock_daily
    WHERE stock_code = 'sz000725' AND trade_date = '2026-03-04'
""").fetchdf()
print(f"\n1. 2026-03-04 收盘价: {df_price.iloc[0]['close']}")

# 2. 当日 PB
pb_db = conn.execute("""
    SELECT pb FROM valuation_indicators
    WHERE stock_code = 'sz000725' AND trade_date = '2026-03-04'
""").fetchone()[0]
print(f"2. 数据库 PB: {pb_db}")

# 3. 总股本
cap = conn.execute("""
    SELECT total_shares FROM stock_capital WHERE stock_code = 'sz000725'
""").fetchone()[0]
print(f"3. 总股本: {cap}")

# 4. 最近财报（用于计算 BVPS）
print("\n4. 最近财报数据 (financial_intermediate):")
r = conn.execute("""
    SELECT report_date, report_type, total_assets, total_liabilities, 
           net_profit, total_revenue, equity, bvps
    FROM financial_intermediate
    WHERE stock_code = 'sz000725'
    ORDER BY report_date DESC LIMIT 5
""").fetchall()
for x in r:
    print(f"   {x[0]} | {x[1]} | 资产={x[2]} | 负债={x[3]} | 净利润={x[4]} | 营收={x[5]} | 权益={x[6]} | BVPS={x[7]}")

# 5. 手动计算 BVPS
latest = r[0]
total_assets = float(latest[2]) if latest[2] else None
total_liabilities = float(latest[3]) if latest[3] else None
equity = total_assets - total_liabilities if total_assets and total_liabilities else None
bvps_manual = equity / cap if equity and cap else None
print(f"\n5. 手动计算:")
print(f"   净资产 = {total_assets} - {total_liabilities} = {equity}")
print(f"   BVPS = {equity} / {cap} = {bvps_manual}")
close_price = float(df_price.iloc[0]['close'])
print(f"   PB = {close_price} / {bvps_manual} = {close_price / bvps_manual if bvps_manual else 'N/A'}")

# 6. 检查原始财报数据
print("\n6. 原始财报数据 (financial_statements):")
r2 = conn.execute("""
    SELECT report_date, report_type, total_assets, total_liabilities, net_profit, total_revenue, eps
    FROM financial_statements
    WHERE stock_code = 'sz000725'
    ORDER BY report_date DESC LIMIT 5
""").fetchall()
for x in r2:
    print(f"   {x[0]} | {x[1]} | 资产={x[2]} | 负债={x[3]} | 净利润={x[4]} | 营收={x[5]} | EPS={x[6]}")

# 7. 检查所有股票的 BVPS 范围
print("\n7. 各股票最新 BVPS:")
r3 = conn.execute("""
    SELECT stock_code, MAX(report_date) as d, bvps
    FROM financial_intermediate
    GROUP BY stock_code
    ORDER BY stock_code
""").fetchall()
for x in r3:
    print(f"   {x[0]}: BVPS={x[2]}")

conn.close()
