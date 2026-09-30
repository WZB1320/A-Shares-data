"""stock_daily 历史缺口补采(水位语义导致的历史空洞无法自愈)。

背景:
    日线采集是"水位增量"语义 —— 只取 trade_date >= 水位 的数据。水位一旦推进到
    最新交易日, 历史中间的空洞(某几天数据源缺失/采集中断)就再也补不回来了。
    实测: 2026-07-07 / 07-15 各缺 12 只, 2026-09-29 缺 10 只, 而水位早已到 09-29。

本脚本:
    1. 用"交易日全集"(某日 >= 8 只股票有数据) 减去各股实际日期, 找出缺口;
    2. 对每只缺口股拉一次前复权日线(akshare stock_zh_a_daily, 与项目主源同口径),
       只 INSERT 缺失的 (stock_code, trade_date), 已存在的行不动(DO NOTHING),
       绝不用另一数据源覆盖既有数据;
    3. 价格跳变护栏: 补入行的收盘价与前一个已知交易日收盘价相比, 日涨跌幅超过
       SANITY_PCT 则拒绝该行并报警(防止复权基准不一致污染序列);
    4. 复核: 重新计算缺口, 打印剩余缺口。

用法:
    python scripts/backfill_daily_gaps.py                 # 近 400 天窗口
    python scripts/backfill_daily_gaps.py --window-days 800
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

import pandas as pd  # noqa: E402

from src.config import DB_PATH, STOCK_CODES  # noqa: E402
from src.database.operations import DatabaseOperations  # noqa: E402

logger = logging.getLogger(__name__)

SANITY_PCT = 30.0     # 补入行相对前一交易日收盘价的允许涨跌幅(%)
MIN_STOCKS_PER_DAY = 8  # 判定"交易日"的门槛(该日至少这么多只有数据)


def find_gaps(reader, window_days: int):
    """返回 {stock_code: [缺失日期...]} 与交易日全集"""
    reader.execute(f"""
        CREATE OR REPLACE TEMP TABLE _td AS
        SELECT trade_date FROM stock_daily
        WHERE trade_date >= DATE '2026-01-01' - INTERVAL '{window_days}' DAY
        GROUP BY 1 HAVING COUNT(DISTINCT stock_code) >= {MIN_STOCKS_PER_DAY}
    """)
    total = reader.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM _td").fetchone()
    rows = reader.execute("""
        SELECT d.stock_code, t.trade_date
        FROM (SELECT DISTINCT stock_code FROM stock_daily) d
        CROSS JOIN _td t
        WHERE NOT EXISTS (
            SELECT 1 FROM stock_daily s
            WHERE s.stock_code = d.stock_code AND s.trade_date = t.trade_date)
        ORDER BY d.stock_code, t.trade_date
    """).fetchall()
    gaps = {}
    for code, d in rows:
        gaps.setdefault(code, []).append(d)
    return gaps, total


def fetch_daily(code: str, start, end):
    """拉前复权日线(与项目主源同口径); ETF 单独走 ETF 接口"""
    import akshare as ak

    _, num = code[:2], code[2:]
    is_etf = (code.startswith('sh') and num.startswith('5')) or \
             (code.startswith('sz') and num.startswith('1'))
    if is_etf:
        df = ak.fund_etf_hist_em(symbol=num, period='daily',
                                 start_date=start, end_date=end, adjust='qfq')
        df = df.rename(columns={"日期": "trade_date", "开盘": "open", "收盘": "close",
                                "最高": "high", "最低": "low", "成交量": "volume",
                                "成交额": "amount"})
    else:
        df = ak.stock_zh_a_daily(symbol=code, start_date=start, end_date=end, adjust="qfq")
        df = df.rename(columns={"date": "trade_date"})
    if df is None or df.empty:
        return None
    df = df.loc[:, ~df.columns.duplicated(keep='last')]
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-days", type=int, default=400)
    args = ap.parse_args()

    DatabaseOperations.init_connection_manager(DB_PATH, read_pool_size=3)
    db = DatabaseOperations()

    with db.cm.acquire_reader() as reader:
        gaps, td = find_gaps(reader, args.window_days)

    print(f"交易日全集: {td[0]} 天 ({td[1]} ~ {td[2]})")
    if not gaps:
        print("无缺口, 无需补采")
        DatabaseOperations.close_all()
        return 0
    print(f"缺口股票 {len(gaps)} 只, 共 {sum(len(v) for v in gaps.values())} 个 (股票, 日期):")
    for code, ds in sorted(gaps.items()):
        print(f"  {code:<10} 缺 {len(ds):>3} 天: "
              f"{','.join(str(d) for d in ds[:6])}{' ...' if len(ds) > 6 else ''}")

    start = min(min(v) for v in gaps.values()).strftime("%Y%m%d")
    end = max(max(v) for v in gaps.values()).strftime("%Y%m%d")

    total_inserted, total_rejected, failed = 0, 0, []
    for code in sorted(gaps):
        try:
            df = fetch_daily(code, start, end)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"{code} 日线拉取失败: {type(e).__name__}: {str(e)[:80]}")
            failed.append(code)
            continue
        if df is None or df.empty:
            logger.warning(f"{code} 未取到日线数据")
            failed.append(code)
            continue

        # 只取缺口日期, 且列与 stock_daily 对齐
        want = set(gaps[code])
        add = df[df["trade_date"].isin(want)].copy()
        if add.empty:
            logger.warning(f"{code} 缺口日期在数据源中也不存在(疑似停牌)")
            failed.append(code)
            continue

        # 价格跳变护栏: 与"最近的既有交易日"收盘价比较
        with db.cm.acquire_reader() as reader:
            hist = reader.execute(
                "SELECT trade_date, close FROM stock_daily WHERE stock_code = ? "
                "ORDER BY trade_date DESC LIMIT 1", [code]).fetchone()
        if hist and hist[1]:
            ref_date, ref_close = hist[0], float(hist[1])
            # 找缺口日前最近的既有收盘价作为参照(用数据源自身的相邻行更稳)
            src = df.sort_values("trade_date").reset_index(drop=True)
            idx = src.index[src["trade_date"].isin(want)]
            refs = []
            for i in idx:
                if i > 0:
                    refs.append((src.at[i, "trade_date"], float(src.at[i, "close"]),
                                 float(src.at[i - 1, "close"])))
            bad = [(d, c, p) for d, c, p in refs if p and abs(c / p - 1) * 100 > SANITY_PCT]
            if bad:
                logger.error(f"{code} 检测到 {len(bad)} 天价格跳变 >{SANITY_PCT}% "
                             f"(复权基准可能不一致), 已拒绝写入: {bad[:3]}")
                total_rejected += len(bad)
                add = add[~add["trade_date"].isin([b[0] for b in bad])]
            if add.empty:
                failed.append(code)
                continue

        cols = [c for c in ("trade_date", "open", "high", "low", "close",
                            "volume", "amount") if c in add.columns]
        add = add[cols].copy()
        add["stock_code"] = code
        # 换手率走 baostock 回填(backfill_turnover.py), 此处不填以免口径不一
        add["turnover"] = None

        with db.cm.acquire_reader() as reader:
            before = reader.execute(
                "SELECT COUNT(*) FROM stock_daily WHERE stock_code = ?", [code]).fetchone()[0]
        with db.transaction():
            db.cm.write_conn.executemany(
                "INSERT INTO stock_daily (stock_code, trade_date, open, high, low, close, "
                "volume, amount, turnover) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (stock_code, trade_date) DO NOTHING",
                [(code, r["trade_date"], r["open"], r["high"], r["low"], r["close"],
                  r["volume"], r["amount"], r["turnover"]) for _, r in add.iterrows()],
            )
        with db.cm.acquire_reader() as reader:
            after = reader.execute(
                "SELECT COUNT(*) FROM stock_daily WHERE stock_code = ?", [code]).fetchone()[0]
        logger.info(f"{code} 补入 {after - before} 行 (候选 {len(add)})")
        total_inserted += after - before

    print(f"\n补入 {total_inserted} 行, 拒绝 {total_rejected} 行, 失败 {len(failed)} 只"
          f"{': ' + ' '.join(failed) if failed else ''}")

    # 复核
    with db.cm.acquire_reader() as reader:
        left, td2 = find_gaps(reader, args.window_days)
    if left:
        print(f"\n剩余缺口 {len(left)} 只 / {sum(len(v) for v in left.values())} 个:")
        for code, ds in sorted(left.items()):
            print(f"  {code:<10} 缺 {len(ds):>3} 天: "
                  f"{','.join(str(d) for d in ds[:6])}{' ...' if len(ds) > 6 else ''}")
    else:
        print("\nPASS: 窗口内日线缺口已清零")

    DatabaseOperations.close_all()
    return 0 if not left else 1


if __name__ == "__main__":
    sys.exit(main())
