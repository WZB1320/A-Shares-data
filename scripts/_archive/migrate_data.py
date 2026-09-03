
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import duckdb
import pandas as pd
from src.config import DB_PATH, PARQUET_DIR
from src.database.parquet_store import ParquetStore
from src.database.models import init_tables


def main():
    print("迁移旧数据到新架构")

    old_db = Path(DB_PATH)
    if not old_db.exists():
        print("未找到旧数据库，跳过迁移")
        return

    conn = duckdb.connect(str(old_db))
    init_tables(conn)

    parquet = ParquetStore(PARQUET_DIR)

    try:
        df_daily = conn.execute("SELECT * FROM stock_daily").fetchdf()
        parquet.write_daily(df_daily)
    except Exception as e:
        print(f"迁移日线失败: {e}")

    try:
        df_fin = conn.execute("SELECT * FROM financial_statements").fetchdf()
        parquet.write_financial(df_fin)
    except Exception as e:
        print(f"迁移财务失败: {e}")

    try:
        df_ind = conn.execute("SELECT * FROM technical_indicators").fetchdf()
        parquet.write_indicators(df_ind)
    except Exception as e:
        print(f"迁移指标失败: {e}")

    conn.close()
    print("迁移完成")


if __name__ == "__main__":
    main()
