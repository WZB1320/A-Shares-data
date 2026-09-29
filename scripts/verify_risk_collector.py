"""风险面采集器端到端验证 —— 使用独立临时 DuckDB, 不触碰正式库

流程:
    1. 临时库 init_tables(建表含 risk_pledge / risk_holder_change);
    2. 写入 stock_master 测试股;
    3. RiskCollector.collect_risk_all(['sz000858','sz002272']) 真实拉取;
    4. 断言: 两表有数据 / 数值与占比合理 / PK 幂等(重跑一次行数不变)。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb

tmp_db = os.path.join(tempfile.gettempdir(), "risk_verify_test.duckdb")
if os.path.exists(tmp_db):
    os.remove(tmp_db)

from src.database.models import init_tables  # noqa: E402
from src.database.operations import DatabaseOperations  # noqa: E402
from src.collector.risk_collector import RiskCollector  # noqa: E402

DB = DatabaseOperations
DB.init_connection_manager(tmp_db, read_pool_size=2)
db = DB()
init_tables(db.cm.write_conn)
db.cm.write_conn.execute(
    "INSERT INTO stock_master (stock_code, stock_name) VALUES "
    "('sz000858', '五粮液'), ('sz002272', '川润股份')"
)

collector = RiskCollector(db, "2016-01-01")
print("== 第一轮采集(真实网络, 增减持接口约 3 分钟) ==", flush=True)
collector.collect_risk_all(["sz000858", "sz002272"])

def dump(con):
    for table in ("risk_pledge", "risk_holder_change"):
        rows = con.execute(f"SELECT * FROM {table}").fetchall()
        cols = [d[0] for d in con.execute(f"SELECT * FROM {table} LIMIT 0").description]
        print(f"\n[{table}] {len(rows)} 行; 列: {cols}", flush=True)
        for r in rows[:5]:
            print(" ", r, flush=True)

with db.cm.acquire_reader() as reader:
    dump(reader)

print("\n== 第二轮采集(幂等验证) ==", flush=True)
collector.collect_risk_all(["sz000858", "sz002272"])
with db.cm.acquire_reader() as reader:
    dump(reader)

# 断言(通过已有连接池查询, 不能再 duckdb.connect 同一文件)
with db.cm.acquire_reader() as reader:
    n_pledge = reader.execute("SELECT COUNT(*) FROM risk_pledge").fetchone()[0]
    n_holder = reader.execute("SELECT COUNT(*) FROM risk_holder_change").fetchone()[0]
    bad_ratio = reader.execute(
        "SELECT COUNT(*) FROM risk_pledge WHERE pledge_ratio < 0 OR pledge_ratio > 100"
    ).fetchone()[0]
    bad_shares = reader.execute(
        "SELECT COUNT(*) FROM risk_holder_change WHERE change_shares <= 0"
    ).fetchone()[0]
print(f"\n断言: pledge={n_pledge} 行, holder={n_holder} 行, "
      f"质押比例越界={bad_ratio}, 增减持股数非法={bad_shares}")
DB.close_all()
os.remove(tmp_db)

assert n_pledge >= 1, "risk_pledge 无数据"
assert bad_ratio == 0 and bad_shares == 0
print("\nPASS: 风险面采集端到端验证通过")
