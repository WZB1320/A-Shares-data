"""对融资融券数据存在历史缺口的股票执行一次补齐采集。

背景:
    旧实现里 update_log 的 margin 水位写的是"采集执行日期"而非"数据实际
    覆盖到的日期", 水位因而超前于数据本身(当天明细尚未发布时水位已推进),
    造成 margin_trading 永久缺口 —— 例如 13 只老股票的数据停在 2026-09-02
    而水位却记到 09-03。

    修复水位语义后, 采集会从"水位+1 天"重新开始, 所以直接重跑即可补齐。

缺口判定: 水位日期 > margin_trading 中该股票的最大交易日。

用法:
    python scripts/backfill_margin.py                    # 自动检测所有缺口股票
    python scripts/backfill_margin.py sh600519 sz002272  # 指定股票
"""
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

from src.config import DB_PATH, START_DATE  # noqa: E402
from src.database.operations import DatabaseOperations  # noqa: E402
from src.collector.eastmoney import EastmoneyCollector  # noqa: E402

MAX_WORKERS = 4
GAP_SQL = """
SELECT u.stock_code, u.last_update_date, m.md
FROM update_log u
LEFT JOIN (
    SELECT stock_code, MAX(trade_date) AS md FROM margin_trading GROUP BY 1
) m ON m.stock_code = u.stock_code
WHERE u.data_type = 'margin'
  AND (m.md IS NULL OR u.last_update_date > m.md)
ORDER BY u.stock_code
"""


def main():
    DatabaseOperations.init_connection_manager(DB_PATH, read_pool_size=MAX_WORKERS + 2)
    db = DatabaseOperations()

    codes = sys.argv[1:]
    if not codes:
        with db.cm.acquire_reader() as reader:
            rows = reader.execute(GAP_SQL).fetchall()
        codes = [r[0] for r in rows]
        print(f"自动检测到 {len(codes)} 只股票存在融资融券缺口:")
        for code, wm, md in rows:
            print(f"  {code}  水位={wm}  数据止于={md}")
    else:
        print(f"指定补齐 {len(codes)} 只股票")

    if not codes:
        print("无缺口, 无需补齐")
        DatabaseOperations.close_all()
        return 0

    collector = EastmoneyCollector(db, START_DATE)
    started = datetime.now()
    ok = failed = 0

    print(f"\n开始补齐 (并发 {MAX_WORKERS})...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(collector.collect_margin_trading, c): c for c in codes}
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                fut.result()
                ok += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"  {code} 补齐失败: {type(exc).__name__}: {exc}")

    with db.cm.acquire_reader() as reader:
        remaining = reader.execute(GAP_SQL).fetchall()
        summary = reader.execute(
            "SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM margin_trading"
        ).fetchone()

    elapsed = (datetime.now() - started).total_seconds()
    print(f"\n{'=' * 60}")
    print(f"完成: 成功 {ok}, 失败 {failed}, 用时 {elapsed:.0f}s")
    print(f"margin_trading 全表: {summary[0]} 行, {summary[1]} ~ {summary[2]}")
    if remaining:
        print(f"仍存在缺口 {len(remaining)} 只:")
        for code, wm, md in remaining:
            print(f"  {code}  水位={wm}  数据止于={md}")
    else:
        print("缺口已全部补齐")

    DatabaseOperations.close_all()
    return 1 if (failed or remaining) else 0


if __name__ == "__main__":
    sys.exit(main())
