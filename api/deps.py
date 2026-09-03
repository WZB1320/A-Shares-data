"""API 共享依赖与工具函数

提供:
  - DB_PATH: 数据库路径常量(替代原 main.py 内本地定义)
  - get_db(): FastAPI 依赖注入,获取 DuckDB 只读连接
  - rows_to_dicts() / fetch_as_dicts(): 行数据转 dict,统一处理 Decimal/date 类型
"""
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterator, Optional

import duckdb

# 项目根目录(api/ 的上一级)
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "stock_data.duckdb"


class DecimalEncoder(json.JSONEncoder):
    """JSON 编码器,处理 Decimal / date / datetime"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return super().default(obj)


def get_db() -> Iterator[duckdb.DuckDBPyConnection]:
    """FastAPI 依赖: 获取 DuckDB 只读连接, 请求结束自动关闭"""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        yield conn
    finally:
        conn.close()


def rows_to_dicts(rows, columns) -> list:
    """将 duckdb 行数据转为 dict 列表, 处理 Decimal/date 类型"""
    result = []
    for row in rows:
        d = {}
        for i, col in enumerate(columns):
            val = row[i]
            if isinstance(val, Decimal):
                val = float(val)
            elif isinstance(val, (date, datetime)):
                val = val.isoformat()
            d[col] = val
        result.append(d)
    return result


def fetch_as_dicts(conn: duckdb.DuckDBPyConnection, sql: str, params: Optional[tuple] = None) -> list:
    """执行 SQL 并返回 dict 列表(自动提取列名)"""
    result = conn.execute(sql, params or [])
    columns = [desc[0] for desc in result.description]
    rows = result.fetchall()
    return rows_to_dicts(rows, columns)


def build_query(
    base_sql: str,
    stock_code: Optional[str] = None,
    date_column: str = "trade_date",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    extra_conditions: Optional[list] = None,
    extra_params: Optional[list] = None,
    order_by: str = "ORDER BY trade_date DESC",
    limit: Optional[int] = None,
) -> tuple:
    """构造 SQL: WHERE 条件拼接 + ORDER BY + LIMIT

    Returns:
        (sql, params)
    """
    conditions = []
    params = []

    if stock_code:
        conditions.append("stock_code = ?")
        params.append(stock_code)
    if start_date:
        conditions.append(f"{date_column} >= ?")
        params.append(start_date)
    if end_date:
        conditions.append(f"{date_column} <= ?")
        params.append(end_date)
    if extra_conditions:
        conditions.extend(extra_conditions)
    if extra_params:
        params.extend(extra_params)

    where = " AND ".join(conditions) if conditions else "1=1"
    sql = f"{base_sql} WHERE {where}"
    if order_by:
        sql += f" {order_by}"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    return sql, params
