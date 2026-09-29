"""验证估值写入的"孤儿行防护"行为。

背景:
    valuation_indicators 通过 (stock_code, trade_date) 与 stock_daily 关联。
    若估值先于日线写入"今天"的行, 调用方按日期 JOIN 日线时取不到 close,
    表现为 close=null/0 —— 这正是调用方反馈 #1 的产生机制。
    因此在 TencentCollector 写入前增加了"当日日线必须已入库"的判定。

本脚本验证 3 件事(全部只读或带回滚, 不改变库的最终状态):
    场景一  真实运行时(today 尚无日线) —— 必须跳过写入
    场景二  日线已存在的日期          —— 必须写入生效(改后立即恢复原值)
    场景三  全库孤儿行                 —— 必须为 0

用法: python scripts/verify_valuation_guard.py [stock_code]
"""
import logging
import os
import sys
from datetime import datetime as real_datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, format="      %(message)s")

from src.config import DB_PATH  # noqa: E402
from src.database.operations import DatabaseOperations  # noqa: E402
import src.collector.tencent_collector as tc  # noqa: E402


def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "sz000858"

    DatabaseOperations.init_connection_manager(DB_PATH, read_pool_size=3)
    db = DatabaseOperations()
    conn = db.conn

    def scalar(sql, params=None):
        row = conn.execute(sql, params or []).fetchone()
        return row[0] if row else None

    today = real_datetime.now().strftime("%Y-%m-%d")
    print(f"股票={code}  today={today}")
    print(f"  该股日线最新日期      : {scalar('SELECT MAX(trade_date) FROM stock_daily WHERE stock_code=?', [code])}")
    print(f"  该股今日日线是否已入库: {scalar('SELECT COUNT(*) FROM stock_daily WHERE stock_code=? AND trade_date=?', [code, today])}")

    collector = tc.TencentCollector(db, "2015-01-01")
    failures = []

    # ---------- 场景一: 真实运行 ----------
    print("\n### 场景一: today 尚无日线 —— 期望跳过写入")
    before = scalar("SELECT COUNT(*) FROM valuation_indicators")
    collector.collect_valuation_data(code)
    after = scalar("SELECT COUNT(*) FROM valuation_indicators")
    ok1 = before == after
    print(f"     估值表行数 {before} -> {after}   {'PASS 未写入' if ok1 else 'FAIL 竟被写入'}")
    if not ok1:
        failures.append("场景一: today 无日线却写入了估值")

    # ---------- 场景二: 日线已存在的日期 ----------
    target = scalar("SELECT MAX(trade_date) FROM stock_daily WHERE stock_code=?", [code])
    print(f"\n### 场景二: 改用日线已存在的日期 {target} —— 期望写入生效")
    orig = conn.execute(
        "SELECT pe_ttm, pb FROM valuation_indicators WHERE stock_code=? AND trade_date=?",
        [code, target],
    ).fetchone()
    print(f"     写入前 pe_ttm/pb = {orig}")

    class _FrozenDatetime:
        @staticmethod
        def now():
            return real_datetime(target.year, target.month, target.day, 15, 0, 0)

    tc.datetime = _FrozenDatetime
    try:
        collector.collect_valuation_data(code)
    finally:
        tc.datetime = real_datetime

    new = conn.execute(
        "SELECT pe_ttm, pb FROM valuation_indicators WHERE stock_code=? AND trade_date=?",
        [code, target],
    ).fetchone()
    print(f"     写入后 pe_ttm/pb = {new}")
    ok2 = new is not None and orig is not None and new != orig
    print(f"     {'PASS 写入生效' if ok2 else 'FAIL 未发生变化'}")
    if not ok2:
        failures.append("场景二: 日线存在时写入未生效")

    # 恢复原值, 保证脚本不改变库的最终状态
    if orig is not None:
        conn.execute(
            "UPDATE valuation_indicators SET pe_ttm=?, pb=? WHERE stock_code=? AND trade_date=?",
            [orig[0], orig[1], code, target],
        )
        restored = conn.execute(
            "SELECT pe_ttm, pb FROM valuation_indicators WHERE stock_code=? AND trade_date=?",
            [code, target],
        ).fetchone()
        print(f"     已恢复原值 -> {restored}  {'PASS' if restored == orig else 'FAIL 未复原'}")

    # ---------- 场景三: 全库孤儿行 ----------
    print("\n### 场景三: 全库孤儿行(估值日期在日线中不存在)")
    orphans = scalar(
        """SELECT COUNT(*) FROM valuation_indicators v
           WHERE NOT EXISTS (SELECT 1 FROM stock_daily d
                             WHERE d.stock_code = v.stock_code AND d.trade_date = v.trade_date)"""
    )
    total = scalar("SELECT COUNT(*) FROM valuation_indicators")
    ok3 = orphans == 0
    print(f"     孤儿行 {orphans} / 总行数 {total}   {'PASS' if ok3 else 'FAIL'}")
    if not ok3:
        failures.append(f"场景三: 存在 {orphans} 条孤儿行")

    DatabaseOperations.close_all()

    print("\n" + "=" * 56)
    if failures:
        print(f"结果: FAIL ({len(failures)} 项)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("结果: 3/3 全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
