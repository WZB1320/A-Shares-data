"""只重算指标, 不联网采集

用途:
  采集流程中断(如数据源超时/挂起)后, 原始数据已落库但指标阶段未执行,
  此时用本脚本补算, 无需重新联网采集。

会重算:
  - 日线基础字段(prev_close/change_pct/振幅/影线/20-60日高低)
  - 技术指标 technical_indicators
  - 财务指标 financial_intermediate
  - 估值指标 valuation_indicators (先 DELETE 再重写, 可一并清除孤儿行)
  - 北向资金累计流入

用法:
    python scripts/recalc_indicators.py                      # 全部配置股票
    python scripts/recalc_indicators.py --stocks sh600585 sz002594
"""
import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import STOCK_CODES
from src.scheduler import Scheduler


def main():
    parser = argparse.ArgumentParser(description="只重算指标(不联网采集)")
    parser.add_argument("--stocks", nargs="+", help="指定股票代码; 缺省使用 config.STOCK_CODES")
    args = parser.parse_args()

    codes = args.stocks or STOCK_CODES

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    print("=" * 60)
    print(f"只重算指标(不联网), 股票数: {len(codes)}")
    print(f"时间: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 60)

    scheduler = Scheduler()
    t0 = time.time()
    try:
        scheduler._calculate_indicators(codes)
    finally:
        scheduler.close()

    print(f"\n完成, 耗时 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
