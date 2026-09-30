"""主力资金流向首次全量采集 / 缺口补齐。

背景:
    capital_flow 是新建表, 需要对现有全部股票做一次历史回填;
    数据源(东方财富 push2his)单次只返回最近 121 个交易日, 之后靠每日增量累积。

⚠️ 必须串行执行(不要并发):
    push2his 的连接有约 1/3 概率被瞬时丢弃(RemoteDisconnected, 0.1~0.3s RST),
    与请求内容无关, 成功会成簇出现(2026-09-30 定量实测)。
    采集器内置全局节流(MIN_INTERVAL=1.5s) + 12 次短间隔重试, 单只成功率 >98%;
    本脚本再加一轮"停顿后重试失败项"兜底。并发会显著拉长冷却期, 务必串行。

用法:
    python scripts/backfill_capital_flow.py                    # 用 config.STOCK_CODES
    python scripts/backfill_capital_flow.py sh600519 sz002272  # 指定股票(只补这些)
"""
import logging
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

from src.config import DB_PATH, START_DATE, STOCK_CODES  # noqa: E402
from src.database.operations import DatabaseOperations  # noqa: E402
from src.collector.fundflow_collector import FundFlowCollector  # noqa: E402

# 批量补采两轮之间的停顿(秒): 让可能的冷却窗口过去
PASS_PAUSE = 15.0


def _collect_pass(collector, codes, label):
    ok, failed = 0, []
    for i, code in enumerate(codes, 1):
        try:
            collector.collect_capital_flow(code)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            failed.append(code)
            print(f"  [{label}] {code} 失败: {type(exc).__name__}: {str(exc)[:90]}")
        print(f"  [{label}] 进度 {i}/{len(codes)}")
    return ok, failed


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    DatabaseOperations.init_connection_manager(DB_PATH, read_pool_size=3)
    db = DatabaseOperations()

    if args:
        codes = args
    else:
        codes = list(STOCK_CODES)

    print(f"目标 {len(codes)} 只股票 (串行采集):")
    print("  " + " ".join(codes))

    collector = FundFlowCollector(db, START_DATE)
    started = datetime.now()

    print(f"\n=== 第一轮 ===")
    ok, failed = _collect_pass(collector, codes, "1")

    # 失败的多为限流, 停顿后重试一轮
    if failed:
        print(f"\n{len(failed)} 只失败, 等待 {PASS_PAUSE:.0f}s 后重试: {' '.join(failed)}")
        time.sleep(PASS_PAUSE)
        print(f"\n=== 第二轮(重试失败项) ===")
        ok2, failed = _collect_pass(collector, failed, "2")
        ok += ok2

    with db.cm.acquire_reader() as reader:
        summary = reader.execute(
            "SELECT COUNT(DISTINCT stock_code), COUNT(*), MIN(trade_date), MAX(trade_date) "
            "FROM capital_flow"
        ).fetchone()
        per_code = reader.execute(
            "SELECT stock_code, COUNT(*), MAX(trade_date) FROM capital_flow "
            "GROUP BY 1 ORDER BY 1"
        ).fetchall()

    elapsed = (datetime.now() - started).total_seconds()
    print(f"\n{'=' * 62}")
    print(f"完成: 成功 {ok}/{len(codes)}, 失败 {len(failed)}, 用时 {elapsed:.0f}s")
    print(f"capital_flow 全表: {summary[0]} 只 / {summary[1]} 行 / {summary[2]} ~ {summary[3]}")
    print(f"\n逐只覆盖:")
    for code, n, mx in per_code:
        print(f"  {code:<10} {n:>5} 行  止于 {mx}")
    if failed:
        print(f"\n仍失败(可再跑一次 --retry-failed): {' '.join(failed)}")

    DatabaseOperations.close_all()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
