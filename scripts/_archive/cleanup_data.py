import duckdb
conn = duckdb.connect('stock_data.duckdb')
conn.execute("DELETE FROM financial_intermediate WHERE stock_code IN ('sh600519', 'sh600309', 'sz000725')")
conn.execute("DELETE FROM valuation_indicators WHERE stock_code IN ('sh600519', 'sh600309', 'sz000725')")
print("Cleared intermediate and valuation data for sh600519, sh600309, sz000725")
conn.close()