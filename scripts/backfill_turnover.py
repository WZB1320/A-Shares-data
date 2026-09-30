"""换手率口径归一 + 缺口回填 —— 以 baostock turn 为权威口径

两个真实问题(2026-09-30 审计):
    1) **单位错乱**: 库内 turnover 并存两种口径 ——
       - 百分数: 0.545 表示 0.545%   ← mootdx/腾讯口径
       - 小数  : 0.003925 表示 0.3925% ← 新浪 ak.stock_zh_a_daily 的原始口径
       实测多只股票整段历史都是小数值(恰好差 100 倍), 成因是走新浪回退路径时
       未把新浪的小数口径 ×100。
    2) **口径本身不够准**: 库内百分数是用"总股本"近似"流通股本"算出来的
       (见 mootdx_collector._fill_turnover_and_shares), 对存在限售股的股票会
       系统性低估换手率, 与 baostock 的流通口径相差 2%~5%。

    因此本脚本的工作不是"补几个空", 而是**统一口径**: 以 baostock turn
    (= 成交量/流通股本 × 100, 百分数, 流通口径) 为准, 重写非 ETF 的全部历史。

判定与写入:
    1. 逐股取双方都有值的行, 计算比值中位数 r = median(库/baostock):
       r ∈ [0.8, 1.25] → 库内已是百分数;  r ∈ [0.008, 0.0125] → 库内是小数;
       其它 → 单位未知, **整只跳过**(不拿不确定的基准覆盖历史)。
    2. 写入: 凡 baostock 有值(>0)的行, turnover 一律写 baostock 值;
       分类统计 修正单位 / 覆盖(口径差) / 已一致 / 回填(原为空)。
       baostock 无值的行(停牌等)保持原样。

用法:
    python scripts/backfill_turnover.py --dry-run     # 只统计不写入
    python scripts/backfill_turnover.py               # 全量归一
    python scripts/backfill_turnover.py sh600519      # 指定股票
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

from src.config import DB_PATH  # noqa: E402
from src.database.operations import DatabaseOperations  # noqa: E402

ETF_CODES = {"sh513700"}          # ETF 无换手率, 属正常, 跳过
PERCENT_RANGE = (0.8, 1.25)       # 库/baostock 比值落在该区间 → 库内为百分数
FRACTION_RANGE = (0.008, 0.0125)  # 落在该区间 → 库内为小数(差 100 倍)
TOL = 0.005                       # "已一致"判定容差(个点) + 2% 相对


def fetch_baostock_turn(bs_code: str, start: str, end: str) -> dict:
    """拉取 baostock 日线 turn, 返回 {date: turn(百分数)}"""
    import baostock as bs
    rs = bs.query_history_k_data_plus(
        bs_code, "date,turn,volume,tradestatus",
        start_date=start, end_date=end, frequency="d", adjustflag="3",
    )
    out = {}
    while rs.error_code == "0" and rs.next():
        d, turn, vol, status = rs.get_row_data()
        if not d:
            continue
        try:
            out[d] = float(turn) if turn not in ("", None) else None
        except ValueError:
            out[d] = None
    if rs.error_code != "0":
        raise RuntimeError(f"baostock 查询失败: {rs.error_code} {rs.error_msg}")
    return out


def detect_unit(rows, turn_map):
    """按比值中位数判定库内单位。返回 ('percent'|'fraction'|'vacant'|'unknown', 比值中位数, 样本数)

    'vacant' = 库内该股换手率整列为空 —— 没有可比样本, 也就没有"覆盖错数据"的风险,
    写入全部是"回填空值", 直接按百分数写入即可。
    """
    ratios = []
    for d, tv in rows:
        bv = turn_map.get(str(d))
        if tv and bv and float(tv) > 0 and bv > 0:
            ratios.append(float(tv) / bv)
    if not ratios:
        if all(tv is None or float(tv) == 0 for _, tv in rows):
            return "vacant", None, 0
        return "unknown", None, 0
    if len(ratios) < 20:
        return "unknown", None, len(ratios)
    ratios.sort()
    med = ratios[len(ratios) // 2]
    if PERCENT_RANGE[0] <= med <= PERCENT_RANGE[1]:
        return "percent", med, len(ratios)
    if FRACTION_RANGE[0] <= med <= FRACTION_RANGE[1]:
        return "fraction", med, len(ratios)
    return "unknown", med, len(ratios)


def main():
    import baostock as bs
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv

    DatabaseOperations.init_connection_manager(DB_PATH, read_pool_size=3)
    db = DatabaseOperations()

    with db.cm.acquire_reader() as reader:
        all_codes = [r[0] for r in reader.execute(
            "SELECT DISTINCT stock_code FROM stock_daily ORDER BY 1").fetchall()]
    codes = [c for c in (args if args else all_codes) if c not in ETF_CODES]
    print(f"目标 {len(codes)} 只 (排除 ETF: {sorted(ETF_CODES)})"
          f"{' [DRY-RUN 不写入]' if dry_run else ''}")

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock 登录失败: {lg.error_code} {lg.error_msg}")

    tot = {"fix_unit": 0, "overwrite": 0, "same": 0, "fill": 0, "no_ref": 0}
    skipped, unit_summary = [], []
    try:
        for code in codes:
            bs_code = f"{code[:2]}.{code[2:]}"
            try:
                turn_map = fetch_baostock_turn(bs_code, "2016-01-01", "2099-12-31")
            except Exception as exc:  # noqa: BLE001
                logging.warning(f"{code} baostock 拉取失败: {exc}")
                skipped.append(code)
                continue
            if not turn_map:
                logging.warning(f"{code} baostock 无数据")
                skipped.append(code)
                continue

            with db.cm.acquire_reader() as reader:
                rows = reader.execute(
                    "SELECT trade_date, turnover FROM stock_daily "
                    "WHERE stock_code = ? ORDER BY trade_date", (code,)).fetchall()

            unit, med, n_ratio = detect_unit(rows, turn_map)
            if unit == "unknown":
                logging.error(f"{code} 单位无法判定(比值中位={med}, 样本={n_ratio}), 整只跳过")
                skipped.append(code)
                continue
            unit_summary.append(
                f"{code}:{unit}(r={med:.4f})" if med is not None else f"{code}:{unit}")

            stats = {"fix_unit": 0, "overwrite": 0, "same": 0, "fill": 0, "no_ref": 0}
            updates = []
            for d, tv in rows:
                bv = turn_map.get(str(d))
                if bv is None or bv <= 0:
                    stats["no_ref"] += 1
                    continue
                if tv is None or float(tv) == 0:
                    stats["fill"] += 1
                elif abs(float(tv) - bv) <= max(TOL, bv * 0.02):
                    stats["same"] += 1
                    continue          # 已一致, 不必写
                elif unit == "fraction":
                    stats["fix_unit"] += 1
                else:
                    stats["overwrite"] += 1
                updates.append((bv, code, str(d)))

            if updates and not dry_run:
                with db.transaction():
                    db.cm.write_conn.executemany(
                        "UPDATE stock_daily SET turnover = ? "
                        "WHERE stock_code = ? AND trade_date = ?", updates)
            logging.info(
                f"{code} [{unit}] 修正单位 {stats['fix_unit']}, 覆盖口径差 "
                f"{stats['overwrite']}, 已一致 {stats['same']}, 回填空值 {stats['fill']}, "
                f"无参照 {stats['no_ref']}{' (dry-run)' if dry_run else ''}")
            for k in stats:
                tot[k] += stats[k]
    finally:
        bs.logout()

    print("=" * 62)
    print(f"合计: 修正单位 {tot['fix_unit']} 行, 覆盖(口径差) {tot['overwrite']} 行, "
          f"已一致 {tot['same']} 行, 回填空值 {tot['fill']} 行, 无参照 {tot['no_ref']} 行")
    frac = [s for s in unit_summary if ":fraction" in s]
    print(f"单位判定: {len(unit_summary)} 只完成"
          f"{', 其中 ' + str(len(frac)) + ' 只为小数口径(已修正)' if frac else ''}")
    if frac:
        print("  小数口径(原为 100 倍偏小): " + " ".join(frac))
    if skipped:
        print(f"跳过 {len(skipped)} 只: {' '.join(skipped)}")

    with db.cm.acquire_reader() as reader:
        left = reader.execute(
            "SELECT stock_code, COUNT(*) FROM stock_daily "
            "WHERE turnover IS NULL OR turnover = 0 GROUP BY 1 ORDER BY 2 DESC").fetchall()
    print(f"\n剩余空值(ETF 属正常): {left}")
    DatabaseOperations.close_all()
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.exit(main())
