import duckdb

conn = duckdb.connect('stock_data.duckdb')

print("=== 京东方各份财报的公告日期 ===")
df = conn.execute("""
    SELECT report_date, report_type, total_assets, total_liabilities, equity_parent, announcement_date
    FROM financial_statements 
    WHERE stock_code = 'sz000725' 
    ORDER BY report_date DESC
""").fetchdf()

print(df.to_string())

print("\n=== 2026-03-04 时已发布的财报 ===")
target_date = '2026-03-04'

df2 = conn.execute("""
    SELECT report_date, report_type, total_assets, total_liabilities, equity_parent, announcement_date
    FROM financial_statements 
    WHERE stock_code = 'sz000725' AND announcement_date <= ?
    ORDER BY report_date DESC
""", (target_date,)).fetchdf()

print(df2.to_string())

print("\n=== 财务中间指标数据（含公告日期）===")
df3 = conn.execute("""
    SELECT report_date, report_type, equity_parent, bvps, total_shares, announcement_date
    FROM financial_intermediate 
    WHERE stock_code = 'sz000725' 
    ORDER BY report_date DESC
    LIMIT 10
""").fetchdf()

print(df3.to_string())

conn.close()
