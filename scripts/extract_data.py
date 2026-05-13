import duckdb

conn = duckdb.connect('stock_data.duckdb')

print("=" * 80)
print("京东方 A (sz000725) 2026-03-04 相关数据")
print("=" * 80)

# 1. 股价数据
print("\n【1】股价数据 (2026-03-04)")
df_price = conn.execute("""
    SELECT trade_date, close, open, high, low, volume, amount
    FROM stock_daily
    WHERE stock_code = 'sz000725' AND trade_date = '2026-03-04'
""").fetchdf()
print(df_price.to_string(index=False))

# 2. 总股本数据
print("\n【2】总股本数据")
df_cap = conn.execute("""
    SELECT stock_code, record_date, total_shares
    FROM stock_capital
    WHERE stock_code = 'sz000725'
    ORDER BY record_date DESC
    LIMIT 3
""").fetchdf()
print(df_cap.to_string(index=False))

# 3. 估值指标数据
print("\n【3】估值指标数据 (2026-03-04)")
df_val = conn.execute("""
    SELECT trade_date, pe_ttm, pb, ps_ttm, dividend_yield, roe
    FROM valuation_indicators
    WHERE stock_code = 'sz000725' AND trade_date = '2026-03-04'
""").fetchdf()
print(df_val.to_string(index=False))

# 4. 财务中间指标数据（2026-03-04 之前的财报）
print("\n【4】最近财务中间指标数据")
df_inter = conn.execute("""
    SELECT report_date, report_type, equity, bvps, revenue_per_share, eps, net_profit, total_revenue, total_shares
    FROM financial_intermediate
    WHERE stock_code = 'sz000725' AND report_date <= '2026-03-04'
    ORDER BY report_date DESC
    LIMIT 3
""").fetchdf()
print(df_inter.to_string(index=False))

# 5. 原始财务报表数据
print("\n【5】原始财务报表数据")
df_fin = conn.execute("""
    SELECT report_date, report_type, total_assets, total_liabilities, net_profit, total_revenue, eps
    FROM financial_statements
    WHERE stock_code = 'sz000725' AND report_date <= '2026-03-04'
    ORDER BY report_date DESC
    LIMIT 3
""").fetchdf()
print(df_fin.to_string(index=False))

print("\n" + "=" * 80)
print("PB 计算公式验证")
print("=" * 80)

if not df_price.empty and not df_inter.empty and not df_val.empty:
    close_price = float(df_price.iloc[0]['close'])
    bvps = float(df_inter.iloc[0]['bvps']) if df_inter.iloc[0]['bvps'] is not None else None
    pb_db = float(df_val.iloc[0]['pb']) if df_val.iloc[0]['pb'] is not None else None
    equity = float(df_inter.iloc[0]['equity']) if df_inter.iloc[0]['equity'] is not None else None
    total_shares = float(df_inter.iloc[0]['total_shares']) if df_inter.iloc[0]['total_shares'] is not None else None
    
    print(f"收盘价 (close) = {close_price} 元")
    
    if equity and total_shares and total_shares > 0:
        bvps_calc = equity / total_shares
        print(f"净资产 (equity) = {equity} 元")
        print(f"总股本 (total_shares) = {total_shares} 股")
        print(f"每股净资产 (BVPS) = 净资产 / 总股本 = {equity} / {total_shares} = {bvps_calc} 元")
    
    if bvps and bvps > 0:
        pb_calc = close_price / bvps
        print(f"\n数据库 BVPS = {bvps} 元")
        print(f"数据库 PB = {pb_db}")
        print(f"手动计算 PB = 收盘价 / BVPS = {close_price} / {bvps} = {pb_calc}")
        print(f"\n结论：PB 计算公式确实是 = 股价 / 每股净资产 (BVPS)")
        print(f"验证：手动计算结果与数据库结果差异 = {abs(pb_calc - pb_db) if pb_db else 'N/A'}")
    else:
        print(f"BVPS 数据不可用，无法验证")
else:
    print(f"缺少必要数据")

conn.close()
