"""换手率历史回填 —— 用 baostock turn 字段补 stock_daily 缺口

背景(2026-09-29 审计):
    stock_daily.turnover 约有 2331 行 NULL/0, 分布:
    - sh513700(ETF): 约 1231 行, ETF 无换手率属正常, **不回填**;
    - sh600552: 约 800 行 + 其余各票 2026-05-18 后 16~71 行, 为采集源(腾讯)
      字段错位期间的缺口, 需要回填。

数据源:
    baostock query_history_k_data_plus 的 turn 字段(成交量/流通股本*100),
    与腾讯实时口径一致。baostock 日线更新到当日, 无限流问题。

⚠️ 与滴灌补采互斥:
    DuckDB 单写者, 滴灌进程(backfill_capital_flow_drip.py)运行期间本脚本无法
    连库。请等滴灌结束后再执行。

流程(先验证后写入):
    1. 查询缺口分布(排除 ETF);
    2. 对每只股票先做口径交叉验证: 取库内已有非零 turnover 的日期,
       与 baostock turn 对比, 一致率 < 95% 则该股跳过并告警;
    3. 验证通过才 UPDATE 缺失行。

用法:
    python scripts/backfill_turnover.py            # 全量回填
    python scripts/backfill_turnover.py sh600552   # 指定股票
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

from src.config import DB_PATH  # noqa: E402
from src.database.operations import DatabaseOperations  # noqa: E402

ETF_CODES = {"sh513700"}          # ETF 无换手率, 属正常, 跳过
CONSISTENCY_THRESHOLD = 0.95      # 口径交叉验证最低一致率


def fetch_baostock_turn(bs_code: str, start: str, end: str) -> dict:
    """拉取 baostock 日线 turn, 返回 {date: float}; 空串/停牌日(0)也返回"""
    import baostock as bs
    rs = bs.query_history_k_data_plus(
        bs_code, "date,turn,tradestatus",
        start_date=start, end_date=end, frequency="d", adjustflag="3",
    )
    out = {}
    while rs.error_code == "0" and rs.next():
        d, turn, status = rs.get_row_data()
        if not d:
            continue
        try:
            out[d] = float(turn) if turn not in ("", None) else None
        except ValueError:
            out[d] = None
    if rs.error_code != "0":
        raise RuntimeError(f"baostock 查询失败: {rs.error_code} {rs.error_msg}")
    return out


def main():
    import baostock as bs
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    DatabaseOperations.init_connection_manager(DB_PATH, read_pool_size=3)
    db = DatabaseOperations()

    with db.cm.acquire_reader() as reader:
        gaps = reader.execute(
            "SELECT stock_code, COUNT(*), MIN(trade_date), MAX(trade_date) "
            "FROM stock_daily WHERE turnover IS NULL OR turnover = 0 "
            "GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()

    targets = [(c, n, mn, mx) for c, n, mn, mx in gaps if c not in ETF_CODES]
    skipped_etf = [(c, n) for c, n, _, _ in gaps if c in ETF_CODES]
    if args:
        want = set(args)
        targets = [t for t in targets if t[0] in want]
        # 指定的股票即使当前无缺口也允许跑(幂等)
        with db.cm.acquire_reader() as reader:
            have = set(r[0] for r in reader.execute(
                "SELECT DISTINCT stock_code FROM stock_daily").fetchall())
        for c in want - {t[0] for t in targets}:
            if c in have:
                targets.append((c, 0, None, None))
    targets.sort(key=lambda t: -t[1])

    print(f"缺口分布(排除 ETF): {sum(t[1] for t in targets)} 行 / {len(targets)} 只")
    for c, n, mn, mx in targets:
        print(f"  {c}: {n} 行 ({mn} ~ {mx})")
    if skipped_etf:
        print(f"跳过 ETF: {skipped_etf}")
    if not targets:
        print("无缺口, 直接退出")
        return 0

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock 登录失败: {lg.error_code} {lg.error_msg}")

    total_fixed = total_checked = total_mismatch = 0
    failed = []
    try:
        for code, n_gap, min_d, max_d in targets:
            bs_code = f"{code[:2]}.{code[2:]}"
            try:
                # 拉全历史(覆盖口径验证样本 + 缺口区间)
                turn_map = fetch_baostock_turn(bs_code, "2016-01-01", "2099-12-31")
            except Exception as exc:  # noqa: BLE001
                logging.warning(f"{code} baostock 拉取失败: {exc}")
                failed.append(code)
                continue
            if not turn_map:
                logging.warning(f"{code} baostock 无数据")
                failed.append(code)
                continue

            # --- 口径交叉验证: 库内已有非零换手率 vs baostock ---
            with db.cm.acquire_reader() as reader:
                sample = reader.execute(
                    "SELECT trade_date, turnover FROM stock_daily "
                    "WHERE stock_code = ? AND turnover IS NOT NULL AND turnover > 0 "
                    "ORDER BY trade_date DESC LIMIT 200",
                    (code,),
                ).fetchall()
            checked = mismatch = 0
            for d, tv in sample:
                bt = turn_map.get(str(d))
                if bt is None:
                    continue
                checked += 1
                if abs(bt - float(tv)) > max(0.005, float(tv) * 0.02):  # 2% 或 0.005 个点
                    mismatch += 1
                    if mismatch <= 3:
                        logging.warning(f"  口径差异 {code} {d}: 库={tv} baostock={bt}")
            rate = (checked - mismatch) / checked if checked else 0.0
            if checked < 20:
                logging.warning(f"{code} 口径验证样本不足({checked}), 跳过写入")
                failed.append(code)
                continue
            if rate < CONSISTENCY_THRESHOLD:
                logging.warning(f"{code} 口径一致率 {rate:.1%} < {CONSISTENCY_THRESHOLD:.0%}, 跳过写入")
                failed.append(code)
                continue
            logging.info(f"{code} 口径验证: {checked - mismatch}/{checked} 一致 ({rate:.1%})")
            total_checked += checked
            total_mismatch += mismatch

            # --- 写入缺失行 ---
            with db.cm.acquire_reader() as reader:
                rows = reader.execute(
                    "SELECT trade_date FROM stock_daily "
                    "WHERE stock_code = ? AND (turnover IS NULL OR turnover = 0)",
                    (code,),
                ).fetchall()
            updates = []
            for (d,) in rows:
                bt = turn_map.get(str(d))
                if bt is not None and bt > 0:
                    updates.append((bt, code, str(d)))
            if updates:
                with db.transaction():
                    db.cm.write_conn.executemany(
                        "UPDATE stock_daily SET turnover = ? "
                        "WHERE stock_code = ? AND trade_date = ?",
                        updates,
                    )
                logging.info(f"{code} 回填 {len(updates)}/{len(rows)} 行")
                total_fixed += len(updates)
            else:
                logging.info(f"{code} 缺口 {len(rows)} 行均无对应 baostock 值(可能全为停牌), 不写入")
    finally:
        bs.logout()

    # --- 收尾复核 ---
    with db.cm.acquire_reader() as reader:
        remain = reader.execute(
            "SELECT stock_code, COUNT(*) FROM stock_daily "
            "WHERE turnover IS NULL OR turnover = 0 GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
    print("=" * 60)
    print(f"回填完成: 共写入 {total_fixed} 行; 口径验证样本 {total_checked}, 差异 {total_mismatch}")
    print(f"失败: {failed if failed else '无'}")
    print("剩余缺口:")
    for c, n in remain:
        print(f"  {c}: {n} 行")

    DatabaseOperations.close_all()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
