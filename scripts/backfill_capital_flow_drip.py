"""主力资金历史慢速滴灌补采 —— 应对东财 push2his IP 封禁

背景(2026-09-29 实测):
    东财 push2his 对本机 IP 的封禁极其敏感:
    - 闲置约一天后, 单发请求可成功;
    - 成功后仅隔 5 分钟的第 2 笔请求即被重新封禁;
    - 封禁期间该主机唯一 IPv4 全部 RemoteDisconnected, IPv6 本机不可达。
    推测为"解封观察期"机制: 请求间隔必须拉到几十分钟量级。

策略:
    逐只补采缺失股票, 每成功一只后等待 SUCCESS_INTERVAL 再试下一只;
    失败则指数退避(不推进游标, 原股重试)。进度通过 capital_flow 表自然持久化,
    中断后重跑本脚本会自动跳过已有数据的股票。

用法:
    python scripts/backfill_capital_flow_drip.py            # 滴灌补采全部缺失股票
    python scripts/backfill_capital_flow_drip.py sh600519   # 只滴灌指定股票
"""
import logging
import os
import random
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import DB_PATH, START_DATE, STOCK_CODES  # noqa: E402
from src.database.operations import DatabaseOperations  # noqa: E402
from src.collector.fundflow_collector import FundFlowCollector  # noqa: E402

# --- 节奏参数(分钟) ---
FIRST_WAIT = 30.0        # 启动后首次请求前的冷却时间
SUCCESS_INTERVAL = 22.0  # 成功一只后到下一只的间隔
FAIL_WAITS = [30.0, 60.0, 90.0]  # 失败退避序列(封顶 90 分钟)
MAX_RUNTIME_HOURS = 14.0  # 总时长保险丝, 超过则带进度退出

LOG_FILE = os.path.join("logs", "drip_backfill.log")


def setup_logging():
    os.makedirs("logs", exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_FILE, encoding="utf-8")):
        h.setFormatter(fmt)
        root.addHandler(h)


def remaining_codes(db, codes):
    with db.cm.acquire_reader() as reader:
        have = set(r[0] for r in reader.execute(
            "SELECT DISTINCT stock_code FROM capital_flow").fetchall())
    return [c for c in codes if c not in have]


def main():
    setup_logging()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    DatabaseOperations.init_connection_manager(DB_PATH, read_pool_size=3)
    db = DatabaseOperations()

    codes = args if args else list(STOCK_CODES)
    pending = remaining_codes(db, codes)
    logging.info(f"滴灌补采启动: 目标 {len(codes)} 只, 缺失 {len(pending)} 只")
    logging.info(f"节奏: 首等 {FIRST_WAIT:.0f}min, 成功间隔 {SUCCESS_INTERVAL:.0f}min, "
                 f"失败退避 {FAIL_WAITS}min")
    if not pending:
        logging.info("无缺失, 直接退出")
        return 0

    deadline = datetime.now() + timedelta(hours=MAX_RUNTIME_HOURS)
    collector = FundFlowCollector(db, START_DATE)
    done, fail_streak = 0, 0

    def sleep_minutes(minutes, why):
        logging.info(f"等待 {minutes:.0f}min ({why})")
        time.sleep(minutes * 60 + random.uniform(0, 30))

    sleep_minutes(FIRST_WAIT, "启动冷却")

    i = 0
    while i < len(pending):
        if datetime.now() > deadline:
            logging.warning(f"达到 {MAX_RUNTIME_HOURS}h 时长保险丝, 提前退出")
            break
        code = pending[i]
        try:
            collector.collect_capital_flow(code)
            done += 1
            fail_streak = 0
            i += 1
            if i < len(pending):
                sleep_minutes(SUCCESS_INTERVAL, f"已完成 {done}/{len(pending)}")
        except Exception as exc:  # noqa: BLE001
            fail_streak += 1
            wait = FAIL_WAITS[min(fail_streak - 1, len(FAIL_WAITS) - 1)]
            logging.warning(f"{code} 失败(连续第 {fail_streak} 次): "
                            f"{type(exc).__name__}: {str(exc)[:80]}")
            sleep_minutes(wait, "失败退避")

    # 收尾汇总
    with db.cm.acquire_reader() as reader:
        summary = reader.execute(
            "SELECT COUNT(DISTINCT stock_code), COUNT(*), MIN(trade_date), MAX(trade_date) "
            "FROM capital_flow").fetchone()
        per_code = reader.execute(
            "SELECT stock_code, COUNT(*), MAX(trade_date) FROM capital_flow "
            "GROUP BY 1 ORDER BY 1").fetchall()

    still_missing = remaining_codes(db, codes)
    logging.info("=" * 60)
    logging.info(f"滴灌结束: 本轮完成 {done} 只, 仍缺 {len(still_missing)} 只")
    logging.info(f"capital_flow 全表: {summary[0]} 只 / {summary[1]} 行 / "
                 f"{summary[2]} ~ {summary[3]}")
    for code, n, mx in per_code:
        logging.info(f"  {code:<10} {n:>5} 行  止于 {mx}")
    if still_missing:
        logging.info(f"未完成(可重跑本脚本续传): {' '.join(still_missing)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
