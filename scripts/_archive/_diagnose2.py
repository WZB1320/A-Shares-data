# -*- coding utf-8 -*-
"""Diagnose sz000725 tech indicator error: 'No numeric types to aggregate'"""
import duckdb
import pandas as pd

conn = duckdb.connect('data/stock_data.duckdb')

df = conn.execute("""
    SELECT stock_code, trade_date, open, high, low, close, volume, amount
    FROM stock_daily WHERE stock_code='sz000725' 
    ORDER BY trade_date
""").fetchdf()

print(f"Rows: {len(df)}")
print(f"Dtypes:\n{df.dtypes}")
print()

# Check for object columns
for col in df.columns:
    if col in ('stock_code', 'trade_date'): 
        continue
    print(f"  {col}: dtype={df[col].dtype}, sample={df[col].iloc[:5].tolist()}, has_nan={df[col].isna().sum()}")

# Check if there's an issue with numeric conversion
for col in ['open','high','low','close','volume']:
    try:
        pd.to_numeric(df[col])
        print(f"  {col}: numeric conversion OK")
    except Exception as e:
        print(f"  {col}: numeric conversion FAILED - {e}")
        print(f"    sample values: {df[col].iloc[:10].tolist()}")

# Check Decimal type from DuckDB
print(f"\n  close type: {type(df['close'].iloc[0])}")
print(f"  volume type: {type(df['volume'].iloc[0])}")

conn.close()