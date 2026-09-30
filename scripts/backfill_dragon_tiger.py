"""龙虎榜(dragon_tiger)历史回填 —— 修复"执行日单日窗口"造成的历史空洞。

背景(2026-09-30 审计):
    原 collect_dragon_tiger 只查 `ak.stock_lhb_detail_em(today, today)` —— 用**采集
    执行日**作窗口。龙虎榜在 T 日收盘后才发布, 而本项目采集多为手工/低频触发,
    因此几乎必然错过上榜日, 且历史空洞永不回补。实测: 表内 0 行, 但自选股
    2026-06/07 确有多次上榜记录(京东方A / 川润股份 / 凯盛科技 …)。
    已把采集器改为滚动窗口自愈(LHB_WINDOW_DAYS=20), 本脚本负责补齐历史。

做法:
    逐月拉取全市场龙虎榜(数据源一次返回当月全部上榜标的), 按自选股过滤后 UPSERT。
    按月分块是为了控制单次响应体积, 也便于断点重跑(写入幂等)。

用法:
    python scripts/backfill_dragon_tiger.py                      # START_DATE ~ 今天
    python scripts/backfill_dragon_tiger.py --months 24          # 最近 24 个月
    python scripts/backfill_dragon_tiger.py --start 2025-01-01 --end 2026-09-30
"""
import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

import akshare as ak  # noqa: E402
import pandas as pd  # noqa: E402

from src.config import DB_PATH, START_DATE, STOCK_CODES  # noqa: E402
from src.database.operations import DatabaseOperations  # noqa: E402
from src.database.models import init_tables  # noqa: E402

logger = logging.getLogger(__name__)


def _month_chunks(start: date, end: date):
    """按自然月切分 [start, end]。"""
    cur = start.replace(day=1)
    while cur <= end:
        nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        yield max(cur, start), min(nxt - timedelta(days=1), end)
        cur = nxt


def _safe(row, col):
    v = row.get(col)
    if v is None:
        return None
    try:
        f = float(v)
        return None if pd.isna(f) else f
    except (ValueError, TypeError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD(缺省用 config.START_DATE)")
    ap.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD(缺省今天)")
    ap.add_argument("--months", type=int, default=None, help="只回填最近 N 个月(覆盖 --start)")
    args = ap.parse_args()

    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today()
    if args.months:
        start = (end - timedelta(days=30 * args.months)).replace(day=1)
    elif args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
    else:
        start = datetime.strptime(START_DATE, "%Y%m%d").date()

    DatabaseOperations.init_connection_manager(DB_PATH, read_pool_size=2)
    db = DatabaseOperations()
    init_tables(db.cm.write_conn)

    code_map = {c[2:]: c for c in STOCK_CODES}      # '600519' -> 'sh600519'
    codes = list(code_map.values())

    with db.cm.acquire_reader() as reader:
        before = reader.execute("SELECT COUNT(*) FROM dragon_tiger").fetchone()[0]

    print(f"回填区间 {start} ~ {end}, 自选股 {len(codes)} 只; 现有 {before} 行")

    total_rows, per_stock = 0, {}
    for (a, b) in _month_chunks(start, end):
        try:
            df = ak.stock_lhb_detail_em(start_date=a.strftime("%Y%m%d"),
                                        end_date=b.strftime("%Y%m%d"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"{a}~{b} 拉取失败: {type(e).__name__}: {str(e)[:80]}")
            continue
        if df is None or df.empty:
            continue
        sub = df[df['代码'].astype(str).isin(code_map)]
        if sub.empty:
            continue
        rows = []
        for _, r in sub.iterrows():
            sc = code_map.get(str(r['代码']))
            if not sc:
                continue
            reason = str(r.get('上榜原因') or '').strip() or '上榜'
            rows.append((
                sc, pd.to_datetime(r['上榜日']).date(), reason[:50], reason,
                _safe(r, '龙虎榜买入额'), _safe(r, '龙虎榜卖出额'), _safe(r, '龙虎榜净买额'),
            ))
        if not rows:
            continue
        # 批内 PK 重复检测: 截断后的 list_type 若碰撞会被 ON CONFLICT 静默覆盖
        seen = set()
        for tup in rows:
            if (tup[0], tup[1], tup[2]) in seen:
                logger.warning(f"  {tup[0]} {tup[1]} list_type 截断碰撞('{tup[2]}'), 仅保留一条")
            seen.add((tup[0], tup[1], tup[2]))
        with db.transaction():
            for tup in rows:
                db.cm.write_conn.execute("""
                    INSERT INTO dragon_tiger
                    (stock_code, trade_date, list_type, reason,
                     buy_amount, sell_amount, net_amount)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (stock_code, trade_date, list_type) DO UPDATE SET
                        reason = EXCLUDED.reason,
                        buy_amount = EXCLUDED.buy_amount,
                        sell_amount = EXCLUDED.sell_amount,
                        net_amount = EXCLUDED.net_amount
                """, list(tup))
        total_rows += len(rows)
        for tup in rows:
            per_stock[tup[0]] = per_stock.get(tup[0], 0) + 1
        print(f"  {a}~{b}: 匹配 {len(rows)} 行")

    with db.cm.acquire_reader() as reader:
        after = reader.execute("SELECT COUNT(*) FROM dragon_tiger").fetchone()[0]
        stats = reader.execute("""
            SELECT stock_code, COUNT(*) n, MIN(trade_date), MAX(trade_date)
            FROM dragon_tiger GROUP BY 1 ORDER BY n DESC
        """).fetchall()

    print("=" * 60)
    print(f"写入 {total_rows} 行(去重后); 表内 {before} -> {after} 行")
    for c, n, mn, mx in stats:
        print(f"  {c:<10} {n:>3} 行  {mn} ~ {mx}")

    DatabaseOperations.close_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
