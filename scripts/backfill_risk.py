"""风险面数据首次回填 / 日常补采 —— 质押 + 股东增减持

用法:
    python scripts/backfill_risk.py               # 全部自选股
    python scripts/backfill_risk.py sz002272      # 指定股票(过滤范围)

说明:
    质押快照与增减持都是"全市场接口拉一次、过滤自选股"的模式,
    与股票数量无关, 整体耗时约 4~5 分钟(增减持接口约 3 分钟)。
    调度器(scheduler)每轮更新已自动执行本采集, 此脚本用于首次回填/手动补采。
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

from src.config import DB_PATH, START_DATE, STOCK_CODES  # noqa: E402
from src.database.operations import DatabaseOperations  # noqa: E402
from src.database.models import init_tables  # noqa: E402
from src.collector.risk_collector import RiskCollector  # noqa: E402


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    DatabaseOperations.init_connection_manager(DB_PATH, read_pool_size=3)
    db = DatabaseOperations()
    init_tables(db.cm.write_conn)

    codes = args if args else list(STOCK_CODES)
    collector = RiskCollector(db, START_DATE)
    print(f"风险面采集目标 {len(codes)} 只: {' '.join(codes)}")
    collector.collect_risk_all(codes)

    with db.cm.acquire_reader() as reader:
        pledge = reader.execute(
            "SELECT stock_code, COUNT(*), MAX(trade_date) FROM risk_pledge "
            "GROUP BY 1 ORDER BY 1").fetchall()
        holder = reader.execute(
            "SELECT stock_code, COUNT(*), MAX(announcement_date) FROM risk_holder_change "
            "GROUP BY 1 ORDER BY 1").fetchall()
    print("=" * 60)
    print(f"risk_pledge 覆盖 {len(pledge)} 只:")
    for c, n, mx in pledge:
        print(f"  {c:<10} {n:>3} 期  止于 {mx}")
    print(f"risk_holder_change 覆盖 {len(holder)} 只:")
    for c, n, mx in holder:
        print(f"  {c:<10} {n:>3} 条  最新公告 {mx}")
    no_pledge = set(codes) - {r[0] for r in pledge} - {"sh513700"}
    print(f"无质押记录(视为零质押): {' '.join(sorted(no_pledge)) or '无'}")

    DatabaseOperations.close_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
