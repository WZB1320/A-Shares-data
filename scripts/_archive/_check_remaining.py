# -*- coding utf-8 -*-
"""Quick check on remaining issues"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.config import DB_PATH
from src.database.operations import DatabaseOperations

db = DatabaseOperations(DB_PATH)

# BVPS null rows
print("=== BVPS NULL rows ===")
result = db.conn.execute("""
    SELECT stock_code, report_date, report_type, equity_parent, total_shares 
    FROM financial_intermediate 
    WHERE bvps IS NULL
""").fetchall()
for r in result:
    print(f"  {r}")

# PB null rows
print("\n=== PB NULL sample ===")
result = db.conn.execute("""
    SELECT stock_code, COUNT(*) as cnt 
    FROM valuation_indicators 
    WHERE pb IS NULL 
    GROUP BY stock_code
""").fetchall()
for r in result:
    print(f"  {r}")

# daily null checks  
print("\n=== Daily change_pct null stocks ===")
result = db.conn.execute("""
    SELECT stock_code, trade_date, change_pct, amplitude 
    FROM stock_daily 
    WHERE change_pct IS NULL
    ORDER BY stock_code, trade_date
""").fetchall()
for r in result:
    print(f"  {r}")

# stock_announcements table issue
print("\n=== stock_announcements ===")
try:
    cnt = db.conn.execute("SELECT COUNT(*) FROM stock_announcements").fetchone()[0]
    print(f"  Rows: {cnt}")
except Exception as e:
    print(f"  ERROR: {e}")
    print(f"  Table likely doesn't exist - need to check schema")

db.close()