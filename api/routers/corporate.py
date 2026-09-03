"""公司基础信息接口: 分红 / 公告 / 股本 / 行业

注意: industry 路由顺序敏感, 静态路径必须在 {path_param} 之前声明,
否则 FastAPI 会把 "list" 误匹配为 {stock_code}
"""
import duckdb
from fastapi import APIRouter, Depends, Query

from api.deps import get_db, fetch_as_dicts

router = APIRouter()


@router.get("/api/dividends/{stock_code}")
def get_dividends(
    stock_code: str,
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    data = fetch_as_dicts(
        conn,
        "SELECT * FROM dividends WHERE stock_code = ? ORDER BY dividend_date DESC",
        (stock_code,),
    )
    return {"stock_code": stock_code, "count": len(data), "data": data}


@router.get("/api/announcements/{stock_code}")
def get_announcements(
    stock_code: str,
    limit: int = Query(100, ge=1, le=1000),
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    data = fetch_as_dicts(
        conn,
        "SELECT * FROM announcements WHERE stock_code = ? "
        "ORDER BY announcement_date DESC LIMIT ?",
        (stock_code, limit),
    )
    return {"stock_code": stock_code, "count": len(data), "data": data}


@router.get("/api/capital/{stock_code}")
def get_capital(
    stock_code: str,
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    data = fetch_as_dicts(
        conn,
        "SELECT * FROM stock_capital WHERE stock_code = ? ORDER BY record_date DESC",
        (stock_code,),
    )
    return {"stock_code": stock_code, "count": len(data), "data": data}


# === 行业接口(顺序敏感, 静态路径在前) ===

@router.get("/api/industry/list")
def list_industries(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    data = fetch_as_dicts(conn, """
        SELECT industry_name, COUNT(*) as stock_count,
               MAX(update_date) as latest_update
        FROM stock_industry
        WHERE industry_name IS NOT NULL
        GROUP BY industry_name
        ORDER BY stock_count DESC
    """)
    return {"count": len(data), "data": data}


@router.get("/api/industry/{industry_name}/stocks")
def get_industry_stocks(
    industry_name: str,
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    data = fetch_as_dicts(conn, """
        SELECT s.stock_code, s.industry_name, s.source, s.update_date
        FROM stock_industry s
        WHERE s.industry_name = ?
        ORDER BY s.stock_code
    """, (industry_name,))
    return {"industry_name": industry_name, "count": len(data), "data": data}


@router.get("/api/industry/{stock_code}")
def get_industry(
    stock_code: str,
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    data = fetch_as_dicts(
        conn,
        "SELECT * FROM stock_industry WHERE stock_code = ?",
        (stock_code,),
    )
    return {"stock_code": stock_code, "count": len(data), "data": data}
