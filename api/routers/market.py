"""行情与基本面接口: 日线 / 财务报表 / 三类指标"""
from typing import Optional

import duckdb
from fastapi import APIRouter, Depends, Query

from api.deps import get_db, fetch_as_dicts, build_query

router = APIRouter()


@router.get("/api/daily/{stock_code}")
def get_daily(
    stock_code: str,
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    limit: int = Query(10000, ge=1, le=100000, description="最大返回条数"),
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    sql, params = build_query(
        "SELECT * FROM stock_daily",
        stock_code=stock_code, start_date=start_date, end_date=end_date,
        limit=limit,
    )
    data = fetch_as_dicts(conn, sql, params)
    return {"stock_code": stock_code, "count": len(data), "data": data}


@router.get("/api/financial/{stock_code}")
def get_financial(
    stock_code: str,
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    report_type: Optional[str] = Query(None, description="报告类型: 年报/中报/季报"),
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    extra_conditions = []
    extra_params = []
    if report_type:
        extra_conditions.append("report_type = ?")
        extra_params.append(report_type)

    sql, params = build_query(
        "SELECT * FROM financial_statements",
        stock_code=stock_code,
        date_column="report_date", start_date=start_date, end_date=end_date,
        extra_conditions=extra_conditions, extra_params=extra_params,
        order_by="ORDER BY report_date DESC",
        limit=None,
    )
    data = fetch_as_dicts(conn, sql, params)
    return {"stock_code": stock_code, "count": len(data), "data": data}


@router.get("/api/indicators/technical/{stock_code}")
def get_technical_indicators(
    stock_code: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(10000, ge=1, le=100000),
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    sql, params = build_query(
        "SELECT * FROM technical_indicators",
        stock_code=stock_code, start_date=start_date, end_date=end_date,
        limit=limit,
    )
    data = fetch_as_dicts(conn, sql, params)
    return {"stock_code": stock_code, "count": len(data), "data": data}


@router.get("/api/indicators/valuation/{stock_code}")
def get_valuation_indicators(
    stock_code: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(10000, ge=1, le=100000),
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    sql, params = build_query(
        "SELECT * FROM valuation_indicators",
        stock_code=stock_code, start_date=start_date, end_date=end_date,
        limit=limit,
    )
    data = fetch_as_dicts(conn, sql, params)
    return {"stock_code": stock_code, "count": len(data), "data": data}


@router.get("/api/indicators/financial/{stock_code}")
def get_financial_indicators(
    stock_code: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    sql, params = build_query(
        "SELECT * FROM financial_intermediate",
        stock_code=stock_code,
        date_column="report_date", start_date=start_date, end_date=end_date,
        order_by="ORDER BY report_date DESC",
        limit=None,
    )
    data = fetch_as_dicts(conn, sql, params)
    return {"stock_code": stock_code, "count": len(data), "data": data}
