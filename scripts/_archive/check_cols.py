import duckdb
conn = duckdb.connect('data/stock_data.duckdb', read_only=True)
cols = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='valuation_indicators' ORDER BY ordinal_position").fetchall()
for c in cols:
    print(c[0])
conn.close()