# -*- coding utf-8 -*-
"""Diagnose: why sz000725 tech=0 and why BVPS is null"""
import duckdb
import pandas as pd

conn = duckdb.connect('data/stock_data.duckdb')

print("=== sz000725 diagnosis ===")
dc = conn.execute("SELECT COUNT(*) FROM stock_daily WHERE stock_code='sz000725'").fetchone()[0]
tc = conn.execute("SELECT COUNT(*) FROM technical_indicators WHERE stock_code='sz000725'").fetchone()[0]
fc = conn.execute("SELECT COUNT(*) FROM financial_intermediate WHERE stock_code='sz000725'").fetchone()[0]
print(f"daily={dc}, tech={tc}, inter={fc}")

# Check if sz000725 daily has required OHLCV columns
df = conn.execute("SELECT * FROM stock_daily WHERE stock_code='sz000725' ORDER BY trade_date LIMIT 5").fetchdf()
print(f"Daily columns: {list(df.columns)}")
print(f"Sample close: {df['close'].iloc[0] if len(df)>0 else 'N/A'}")
print(f"Sample volume: {df['volume'].iloc[0] if len(df)>0 else 'N/A'}")

# Check for NaN in OHLCV
nulls = conn.execute("""
    SELECT 
        COUNT(CASE WHEN close IS NULL THEN 1 END) as null_close,
        COUNT(CASE WHEN volume IS NULL THEN 1 END) as null_vol,
        COUNT(CASE WHEN high IS NULL THEN 1 END) as null_high,
        COUNT(CASE WHEN low IS NULL THEN 1 END) as null_low,
        COUNT(CASE WHEN open IS NULL THEN 1 END) as null_open
    FROM stock_daily WHERE stock_code='sz000725'
""").fetchone()
print(f"NULLs: close={nulls[0]}, vol={nulls[1]}, high={nulls[2]}, low={nulls[3]}, open={nulls[4]}")

print("\n=== BVPS diagnosis (sh600519) ===")
idf = conn.execute("SELECT report_date, report_type, bvps, revenue_per_share, total_shares, equity_parent, net_profit, roe_annual, gross_margin_annual FROM financial_intermediate WHERE stock_code='sh600519' ORDER BY report_date DESC LIMIT 6").fetchdf()
print(idf.to_string())

print("\n=== stock_capital check ===")
sc = conn.execute("SELECT * FROM stock_capital").fetchdf()
print(sc.to_string())

print("\n=== Check if bvps is null for ALL stocks ===")
bvps_null = conn.execute("SELECT COUNT(*) FROM financial_intermediate WHERE bvps IS NULL").fetchone()[0]
bvps_total = conn.execute("SELECT COUNT(*) FROM financial_intermediate").fetchone()[0]
print(f"BVPS NULL across all: {bvps_null}/{bvps_total}")

rps_null = conn.execute("SELECT COUNT(*) FROM financial_intermediate WHERE revenue_per_share IS NULL").fetchone()[0]
print(f"Revenue_per_share NULL across all: {rps_null}/{bvps_total}")

conn.close()