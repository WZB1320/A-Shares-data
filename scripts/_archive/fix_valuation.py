"""修复 PE/PB 异常：重新采集股本数据后重新计算估值指标"""
import logging
import sys
sys.path.insert(0, '.')

from src.config import DB_PATH, PARQUET_DIR, START_DATE, STOCK_CODES
from src.database.operations import DatabaseOperations
from src.database.parquet_store import ParquetStore
from src.collector.baostock_collector import BaostockCollector
from src.indicators.valuation import ValuationIndicatorCalculator

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

db_ops = DatabaseOperations(DB_PATH)
parquet_store = ParquetStore(PARQUET_DIR)
baostock = BaostockCollector(db_ops, parquet_store, START_DATE)
val_calc = ValuationIndicatorCalculator(db_ops)

for stock_code in STOCK_CODES:
    print(f"\n{'='*50}")
    print(f"修复 {stock_code}")
    print(f"{'='*50}")

    # 1. 重新采集股本数据
    try:
        baostock.collect_capital_data(stock_code)
        print(f"  [OK] 股本数据重新采集完成")
    except Exception as e:
        print(f"  [FAIL] 股本数据采集失败: {e}")
        continue

    # 2. 重新计算估值指标
    try:
        val_calc.calculate_for_stock(stock_code)
        print(f"  [OK] 估值指标重新计算完成")
    except Exception as e:
        print(f"  [FAIL] 估值计算失败: {e}")

baostock.close()
db_ops.close()

print(f"\n{'='*50}")
print("修复完成")
print(f"{'='*50}")