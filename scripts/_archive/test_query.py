import duckdb
import pandas as pd

print("="*70)
print("Stock Data Query Script")
print("="*70)

try:
    conn = duckdb.connect('stock_data.duckdb')
    
    print("\n[Database Tables]")
    tables = conn.execute("SHOW TABLES").fetchall()
    for table in tables:
        print(f"  - {table[0]}")
    
    expected_tables = [
        'stock_daily', 'financial_statements', 'announcements', 'update_log',
        'technical_indicators', 'valuation_indicators', 
        'stock_capital', 'dividends', 'financial_intermediate'
    ]
    
    print("\n[Table Status Check]")
    all_ok = True
    for table in expected_tables:
        exists = any(t[0] == table for t in tables)
        status = "OK" if exists else "MISSING!"
        if not exists:
            all_ok = False
        print(f"  {table}: {status}")
    
    if not all_ok:
        print("\n[WARNING] Some tables are missing! Check _create_tables() method.")
    
    # Check data counts
    print("\n[Data Counts]")
    for table in tables:
        tname = table[0]
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM {tname}").fetchone()[0]
            print(f"  {tname}: {count} rows")
        except:
            print(f"  {tname}: query failed")
    
    # Check stock_daily data
    print("\n[Stock Daily - Latest 5 rows for sh600519]")
    try:
        df = conn.execute("""
            SELECT trade_date, open, high, low, close, volume 
            FROM stock_daily 
            WHERE stock_code = 'sh600519' 
            ORDER BY trade_date DESC LIMIT 5
        """).fetchdf()
        print(df.to_string())
    except Exception as e:
        print(f"  Error: {e}")
    
    # Check technical_indicators
    print("\n[Technical Indicators - Latest 5 rows for sh600519]")
    try:
        df = conn.execute("""
            SELECT trade_date, ma5, ma10, ma20, ma60 
            FROM technical_indicators 
            WHERE stock_code = 'sh600519' 
            ORDER BY trade_date DESC LIMIT 5
        """).fetchdf()
        print(df.to_string())
    except Exception as e:
        print(f"  Error: {e}")
    
    # Check valuation_indicators
    print("\n[Valuation Indicators - Latest 5 rows for sh600519]")
    try:
        df = conn.execute("""
            SELECT trade_date, pe_ttm, pb, ps_ttm, dividend_yield, roe 
            FROM valuation_indicators 
            WHERE stock_code = 'sh600519' 
            ORDER BY trade_date DESC LIMIT 5
        """).fetchdf()
        print(df.to_string())
    except Exception as e:
        print(f"  Error: {e}")
    
    # Check financial_intermediate
    print("\n[Financial Intermediate - Latest 5 rows for sh600519]")
    try:
        df = conn.execute("""
            SELECT report_date, report_type, eps, bvps, revenue_per_share, equity 
            FROM financial_intermediate 
            WHERE stock_code = 'sh600519' 
            ORDER BY report_date DESC LIMIT 5
        """).fetchdf()
        print(df.to_string())
    except Exception as e:
        print(f"  Error: {e}")
    
    # Check stock_capital
    print("\n[Stock Capital]")
    try:
        df = conn.execute("SELECT * FROM stock_capital").fetchdf()
        print(df.to_string())
    except Exception as e:
        print(f"  Error: {e}")
    
    # Check dividends
    print("\n[Dividends - Latest 5 rows]")
    try:
        df = conn.execute("SELECT * FROM dividends ORDER BY dividend_date DESC LIMIT 5").fetchdf()
        print(df.to_string())
    except Exception as e:
        print(f"  Error: {e}")
    
    conn.close()
    print("\n" + "="*70)
    print("Query completed")
    
except Exception as e:
    print(f"\nError: {e}")
    print("\nTip: Close DBeaver or other programs using stock_data.duckdb first")