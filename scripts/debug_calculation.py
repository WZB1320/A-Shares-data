import duckdb
import pandas as pd
from datetime import datetime

conn = duckdb.connect('stock_data.duckdb')

target_date = '2026-03-04'

print("=== 2026-03-04 的股价 ===")
df_price = conn.execute("""
    SELECT trade_date, close 
    FROM stock_daily 
    WHERE stock_code = 'sz000725' AND trade_date = ?
""", (target_date,)).fetchdf()

print(df_price)
close_price = df_price.iloc[0]['close']

print("\n=== 2026-03-04 前已发布的财务中间指标 ===")
df_inter = conn.execute("""
    SELECT report_date, report_type, equity_parent, bvps, total_shares, announcement_date
    FROM financial_intermediate 
    WHERE stock_code = 'sz000725' 
    ORDER BY report_date
""").fetchdf()

print(df_inter.tail(10).to_string())

print("\n=== 筛选出在 2026-03-04 前已发布的 ===")
trade_dt = pd.to_datetime(target_date)
df_inter['report_date'] = pd.to_datetime(df_inter['report_date'])
df_inter['announcement_date'] = pd.to_datetime(df_inter['announcement_date'])

# 筛选条件：
# 1. report_date <= trade_date
# 2. 如果有 announcement_date，则 announcement_date <= trade_date
mask = df_inter['report_date'] <= trade_dt
mask = mask & (df_inter['announcement_date'].isna() | (df_inter['announcement_date'] <= trade_dt))

filtered = df_inter[mask]
print(filtered.tail(5).to_string())

latest_inter = filtered.iloc[-1]
print("\n=== 最新的财务数据 ===")
print(latest_inter)

bvps = latest_inter['bvps']
equity_parent = latest_inter['equity_parent']

pb_calc = close_price / bvps
print(f"\n手动计算 PB = {close_price} / {bvps} = {pb_calc}")

print("\n=== 从数据库中获取的估值指标 ===")
df_val = conn.execute("""
    SELECT trade_date, pe_ttm, pb, ps_ttm, dividend_yield, roe
    FROM valuation_indicators
    WHERE stock_code = 'sz000725' AND trade_date = ?
""", (target_date,)).fetchdf()
print(df_val)

conn.close()
