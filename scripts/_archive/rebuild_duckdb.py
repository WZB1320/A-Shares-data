
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import duckdb
import pandas as pd
from src.config import DB_PATH, PARQUET_DIR
from src.database.models import init_tables


def main():
    print("从 Parquet 重建 DuckDB")

    db_path = Path(DB_PATH)
    if db_path.exists():
        backup = db_path.parent / f"{db_path.stem}.backup_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.duckdb"
        db_path.rename(backup)
        print(f"已备份旧数据库到 {backup}")

    conn = duckdb.connect(str(db_path))
    init_tables(conn)

    daily_path = PARQUET_DIR / "daily" / "data.parquet"
    if daily_path.exists():
        df_daily = pd.read_parquet(daily_path)
        conn.register("df", df_daily)
        conn.execute("INSERT INTO stock_daily SELECT * FROM df")
        conn.unregister("df")
        print(f"已恢复日线数据 {len(df_daily)} 条")

    financial_path = PARQUET_DIR / "financial" / "data.parquet"
    if financial_path.exists():
        df_fin = pd.read_parquet(financial_path)
        conn.register("df", df_fin)
        conn.execute("INSERT INTO financial_statements SELECT * FROM df")
        conn.unregister("df")
        print(f"已恢复财务数据 {len(df_fin)} 条")

    indicators_path = PARQUET_DIR / "indicators" / "data.parquet"
    if indicators_path.exists():
        df_ind = pd.read_parquet(indicators_path)
        conn.register("df", df_ind)
        conn.execute("INSERT INTO technical_indicators SELECT * FROM df")
        conn.unregister("df")
        print(f"已恢复技术指标 {len(df_ind)} 条")

    conn.close()
    print("重建完成")


if __name__ == "__main__":
    main()
