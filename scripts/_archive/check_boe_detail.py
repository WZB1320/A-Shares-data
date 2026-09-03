import duckdb

conn = duckdb.connect('stock_data.duckdb')

print("=== 京东方 2026-03-04 详细验证 ===")

# 1. 获取 2026-03-04 的股价
print("\n1. 2026-03-04 股价数据：")
df_price = conn.execute("""
    SELECT trade_date, close
    FROM stock_daily
    WHERE stock_code = 'sz000725' AND trade_date = '2026-03-04'
""").fetchdf()
print(df_price)
close_price = float(df_price.iloc[0]['close']) if not df_price.empty else None

# 2. 获取所有财务数据
print("\n2. 财务报表数据（包含 equity_parent）：")
df_fin = conn.execute("""
    SELECT report_date, report_type, total_assets, total_liabilities, equity_parent
    FROM financial_statements
    WHERE stock_code = 'sz000725' AND report_date <= '2026-03-04'
    ORDER BY report_date DESC
    LIMIT 5
""").fetchdf()
print(df_fin)

# 3. 检查 2026-03-04 估值指标计算时具体用的是哪条财务数据
print("\n3. 检查估值指标计算过程：")
print("  对于 2026-03-04，程序会查找 report_date <= 2026-03-04 的最新财务数据")
print("  从上面的列表可以看到，最新的是 2025-12-31（年报）")

# 4. 用 2025-09-30 的数据手动计算
print("\n4. 手动计算（用 2025-09-30 3季报数据）：")
eq_parent_3q = 1.337892e+11  # 从之前的输出看到
shares = 3.704433e+10
bvps_3q = eq_parent_3q / shares
pb_3q = close_price / bvps_3q if close_price else None
print(f"  归属于母公司股东权益（2025Q3）：{eq_parent_3q:,} 元")
print(f"  总股本：{shares:,} 股")
print(f"  每股净资产（BVPS）：{eq_parent_3q} / {shares} = {bvps_3q} 元")
print(f"  PB：{close_price} / {bvps_3q} = {pb_3q}")

# 5. 检查为什么股本是 370.44 亿而不是 374.14 亿
print("\n5. 股本数据检查：")
df_cap = conn.execute("""
    SELECT record_date, total_shares
    FROM stock_capital
    WHERE stock_code = 'sz000725'
    ORDER BY record_date DESC
""").fetchdf()
print(df_cap)

conn.close()
