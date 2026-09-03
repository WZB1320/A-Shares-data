# -*- coding utf-8 -*-
"""
Fix 3 issues:
1. Re-run technical indicators for sz000725
2. Re-run financial_intermediate for all stocks (BVPS was null because stock_capital was empty)
3. Re-run valuation_indicators after financial_intermediate is fixed
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
from src.config import DB_PATH, STOCK_CODES
from src.database.operations import DatabaseOperations
from src.indicators.technical import TechnicalIndicatorCalculator
from src.indicators.financial import FinancialIndicatorCalculator
from src.indicators.valuation import ValuationIndicatorCalculator

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

db_ops = DatabaseOperations(DB_PATH)

# Step 1: Re-run technical indicators for sz000725
print("\n=== Step 1: sz000725 technical indicators ===")
tech_calc = TechnicalIndicatorCalculator(db_ops)
try:
    tech_calc.calculate_for_stock('sz000725')
    cnt = db_ops.conn.execute("SELECT COUNT(*) FROM technical_indicators WHERE stock_code='sz000725'").fetchone()[0]
    print(f"  sz000725 tech count: {cnt}")
except Exception as e:
    print(f"  ERROR: {e}")

# Step 2: Re-run financial indicators for ALL stocks (BVPS was null)
print("\n=== Step 2: Re-run financial indicators (BVPS fix) ===")
fin_calc = FinancialIndicatorCalculator(db_ops)
for sc in STOCK_CODES:
    try:
        fin_calc.calculate_for_stock(sc)
        cnt = db_ops.conn.execute("SELECT COUNT(*) FROM financial_intermediate WHERE stock_code=? AND bvps IS NOT NULL", (sc,)).fetchone()[0]
        total = db_ops.conn.execute("SELECT COUNT(*) FROM financial_intermediate WHERE stock_code=?", (sc,)).fetchone()[0]
        print(f"  {sc}: intermediate={total}, BVPS filled={cnt}")
    except Exception as e:
        print(f"  {sc} ERROR: {e}")

# Step 3: Re-run valuation indicators for ALL stocks
print("\n=== Step 3: Re-run valuation indicators ===")
val_calc = ValuationIndicatorCalculator(db_ops)
for sc in STOCK_CODES:
    try:
        val_calc.calculate_for_stock(sc)
        cnt = db_ops.conn.execute("SELECT COUNT(*) FROM valuation_indicators WHERE stock_code=?", (sc,)).fetchone()[0]
        print(f"  {sc}: valuation={cnt}")
    except Exception as e:
        print(f"  {sc} ERROR: {e}")

db_ops.close()
print("\n=== Fix complete ===")