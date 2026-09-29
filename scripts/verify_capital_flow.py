"""capital_flow(主力资金流向) 数据质量自证。

不依赖任何外部文档, 只用三组可独立验证的约束:

  [内部恒等式]
    1. main_net_amount = large_net_amount + xlarge_net_amount
       (主力净额 = 大单净额 + 超大单净额)
    2. main_net_ratio  = large_net_ratio  + xlarge_net_ratio
    3. |超大+大+中+小| ≈ 0  (四类单净额守恒: 净流入与净流出相互抵消)

  [跨源恒等式] —— 最强的一条, 数据来自两个互不相干的口径
    4. main_net_amount / stock_daily.amount × 100 ≈ main_net_ratio
       (净占比 = 净额 / 成交额) 其中成交额取自 stock_daily, 与资金流源无关。
       成立即证明: 净额单位是元、占比语义正确、且两表的金额口径一致。

  [结构一致性]
    5. 不存在孤行: capital_flow 的每个 (code, date) 都必须在 stock_daily 中存在
       (资金流不应出现在非交易日)
    6. 主键无重复

用法:
    python scripts/verify_capital_flow.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb  # noqa: E402

from src.config import DB_PATH  # noqa: E402

TOL_AMOUNT = 1.0      # 金额恒等式容差(元)
TOL_RATIO = 0.05      # 占比闭合容差(百分点)


def main():
    con = duckdb.connect(str(DB_PATH), read_only=True)

    total = con.execute("SELECT COUNT(*) FROM capital_flow").fetchone()[0]
    codes = con.execute("SELECT COUNT(DISTINCT stock_code) FROM capital_flow").fetchone()[0]
    span = con.execute("SELECT MIN(trade_date), MAX(trade_date) FROM capital_flow").fetchone()
    print(f"capital_flow: {codes} 只 / {total} 行 / {span[0]} ~ {span[1]}\n")

    if total == 0:
        print("表内无数据, 请先运行 scripts/backfill_capital_flow.py")
        return 1

    checks = []

    # 1. 主力 = 大单 + 超大单
    bad = con.execute(
        "SELECT COUNT(*) FROM capital_flow "
        "WHERE ABS(main_net_amount - (large_net_amount + xlarge_net_amount)) > ?",
        [TOL_AMOUNT],
    ).fetchone()[0]
    checks.append(("主力净额 = 大单 + 超大单", bad, total))

    # 2. 占比闭合
    bad = con.execute(
        "SELECT COUNT(*) FROM capital_flow "
        "WHERE ABS(main_net_ratio - (large_net_ratio + xlarge_net_ratio)) > ?",
        [TOL_RATIO],
    ).fetchone()[0]
    checks.append(("主力占比 = 大单占比 + 超大单占比", bad, total))

    # 3. 四类单守恒
    bad = con.execute(
        """
        SELECT COUNT(*) FROM capital_flow
        WHERE ABS(xlarge_net_amount + large_net_amount
                  + medium_net_amount + small_net_amount)
              > GREATEST(ABS(xlarge_net_amount) + ABS(large_net_amount)
                         + ABS(medium_net_amount) + ABS(small_net_amount), 1.0) * 1e-3
        """
    ).fetchone()[0]
    checks.append(("四类单净额守恒(合计≈0)", bad, total))

    # 4. 跨源: 净额/成交额 == 净占比
    cross = con.execute(
        """
        SELECT COUNT(*),
               SUM(CASE WHEN ABS(c.main_net_amount / d.amount * 100 - c.main_net_ratio)
                             > GREATEST(0.05, ABS(c.main_net_ratio) * 0.01)
                        THEN 1 ELSE 0 END)
        FROM capital_flow c
        JOIN stock_daily d
          ON d.stock_code = c.stock_code AND d.trade_date = c.trade_date
        WHERE d.amount IS NOT NULL AND d.amount <> 0
        """
    ).fetchone()
    checks.append((f"跨源: 净额/成交额 == 净占比 (可比 {cross[0]} 行)", cross[1], cross[0]))

    # 5. 孤行
    bad = con.execute(
        """
        SELECT COUNT(*) FROM capital_flow c
        WHERE NOT EXISTS (
            SELECT 1 FROM stock_daily d
            WHERE d.stock_code = c.stock_code AND d.trade_date = c.trade_date
        )
        """
    ).fetchone()[0]
    checks.append(("无孤行(资金流日期均存在于日线)", bad, total))

    # 6. 主键唯一
    dup = con.execute(
        "SELECT COUNT(*) FROM ("
        "  SELECT stock_code, trade_date FROM capital_flow "
        "  GROUP BY 1, 2 HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    checks.append(("主键 (code, date) 无重复", dup, total))

    print(f"{'检查项':<44}{'违约':>8}{'样本':>8}  结果")
    print("-" * 72)
    failed = 0
    for name, bad, sample in checks:
        ok = (bad == 0)
        if not ok:
            failed += 1
        print(f"{name:<44}{bad:>8}{sample:>8}  {'PASS' if ok else 'FAIL'}")

    print("-" * 72)
    print(f"合计 {len(checks)} 项, 通过 {len(checks) - failed}, 失败 {failed}")

    # 抽样展示
    print("\n最近 5 行抽样:")
    for r in con.execute(
        "SELECT stock_code, trade_date, main_net_amount, main_net_ratio "
        "FROM capital_flow ORDER BY trade_date DESC, stock_code LIMIT 5"
    ).fetchall():
        print(f"  {r[0]:<10} {r[1]}  主力净额={float(r[2]):>16,.0f}  占比={float(r[3]):>7.2f}%")

    con.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
