"""货币资金(monetary_funds)历史回填 —— 应对原问题 #3 "财务缺资产负债字段"

背景(2026-09-29):
    financial_statements 此前无货币资金字段。东财 EM 资产负债表接口
    (datacenter-web, 未受 push2his 封禁影响)提供 MONETARYFUNDS,
    已扩展 financial_service.update_balance_sheet_fields 一并写入。

本脚本:
    1. 执行 init_tables 幂等迁移(老库自动 ALTER 补 monetary_funds 列);
    2. 对全部自选股(或指定股票)跑 update_balance_sheet_fields;
    3. 复核: 各股 monetary_funds 覆盖数 / 与 current_assets 的占比合理性
       (货币资金/流动资产 应在 0~1 之间, 超出为异常告警)。

⚠️ 与滴灌补采互斥: DuckDB 单写者, 等滴灌结束后再执行。
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

from src.config import DB_PATH, START_DATE, STOCK_CODES  # noqa: E402
from src.database.operations import DatabaseOperations  # noqa: E402
from src.database.models import init_tables  # noqa: E402
from src.collector.financial_service import FinancialService  # noqa: E402


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    DatabaseOperations.init_connection_manager(DB_PATH, read_pool_size=3)
    db = DatabaseOperations()

    # 1. 幂等迁移(老库补列)
    init_tables(db.cm.write_conn)

    with db.cm.acquire_reader() as reader:
        have = {r[0] for r in reader.execute(
            "SELECT DISTINCT stock_code FROM financial_statements").fetchall()}
    codes = args if args else [c for c in STOCK_CODES if c in have]
    etf = [c for c in codes if c in {"sh513700"}]
    codes = [c for c in codes if c not in etf]
    print(f"目标 {len(codes)} 只" + (f", 跳过 ETF: {etf}" if etf else ""))

    # 2. 回填
    svc = FinancialService(db, START_DATE)
    failed = []
    for i, code in enumerate(codes, 1):
        try:
            svc.update_balance_sheet_fields(code)
        except Exception as exc:  # noqa: BLE001
            failed.append(code)
            logging.warning(f"{code} 失败: {type(exc).__name__}: {str(exc)[:80]}")
        print(f"  进度 {i}/{len(codes)}")

    # 3. 复核
    with db.cm.acquire_reader() as reader:
        stats = reader.execute("""
            SELECT stock_code,
                   COUNT(*) AS total_rows,
                   SUM(CASE WHEN monetary_funds IS NOT NULL THEN 1 ELSE 0 END) AS filled,
                   MAX(monetary_funds / NULLIF(current_assets, 0)) AS max_ratio
            FROM financial_statements
            WHERE stock_code IN ({})
            GROUP BY 1 ORDER BY 1
        """.format(",".join("?" * len(codes))), codes).fetchall()
    print("=" * 60)
    bad = []
    for code, total, filled, max_ratio in stats:
        flag = ""
        if max_ratio is not None and max_ratio > 1.0:
            flag = f" ⚠️ 货币资金/流动资产 最大 {max_ratio:.2f} > 1, 需人工核查"
            bad.append(code)
        print(f"  {code:<10} {filled}/{total} 行有值{flag}")
    if failed:
        print(f"采集失败: {' '.join(failed)}")
    if bad:
        print(f"占比异常: {' '.join(bad)}")

    DatabaseOperations.close_all()
    return 1 if (failed or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
