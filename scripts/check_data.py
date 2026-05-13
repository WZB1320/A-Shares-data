import duckdb

conn = duckdb.connect('stock_data.duckdb')

print("Report types in financial_statements for sh600519:")
r = conn.execute("""
    SELECT DISTINCT report_type FROM financial_statements WHERE stock_code = 'sh600519'
""").fetchall()
for x in r:
    print(f"  '{x[0]}'")

print("\nSample financial data (last 8 reports):")
r = conn.execute("""
    SELECT report_date, report_type, eps, total_revenue, net_profit
    FROM financial_statements 
    WHERE stock_code = 'sh600519'
    ORDER BY report_date DESC
    LIMIT 8
""").fetchall()
for x in r:
    print(f"  {x[0]} | {x[1]} | EPS={x[2]} | 营收={x[3]} | 净利润={x[4]}")

conn.close()