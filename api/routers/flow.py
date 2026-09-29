"""资金与交易接口: 北向资金 / 融资融券 / 主力资金流向 / 龙虎榜

注意: /api/northbound/market 必须在 /api/northbound/{stock_code} 之前声明,
否则 "market" 会被当作 stock_code
"""
from typing import Optional

import duckdb
from fastapi import APIRouter, Depends, Query

from api.deps import get_db, fetch_as_dicts, build_query

router = APIRouter()


# === 北向资金(静态路径在前) ===

@router.get("/api/northbound/market")
def get_northbound_market(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(10000, ge=1, le=100000),
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    """获取北向资金整体流向(沪深港通合计)

    注: 个股级北向资金接口(stock_hsgt_individual_em)数据只到 2024-08-16,
    此接口提供整体资金流向作为替代。
    """
    sql, params = build_query(
        "SELECT * FROM northbound_market_flow",
        stock_code=None, start_date=start_date, end_date=end_date,
        limit=limit,
    )
    data = fetch_as_dicts(conn, sql, params)
    return {"count": len(data), "data": data}


@router.get("/api/northbound/{stock_code}")
def get_northbound(
    stock_code: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(10000, ge=1, le=100000),
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    sql, params = build_query(
        "SELECT * FROM northbound_flow",
        stock_code=stock_code, start_date=start_date, end_date=end_date,
        limit=limit,
    )
    data = fetch_as_dicts(conn, sql, params)
    resp = {"stock_code": stock_code, "count": len(data), "data": data}
    if not data:
        resp["note"] = "该股票可能非沪深港通标的,无北向资金数据"
    return resp


@router.get("/api/margin/{stock_code}")
def get_margin_trading(
    stock_code: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(10000, ge=1, le=100000),
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    sql, params = build_query(
        "SELECT * FROM margin_trading",
        stock_code=stock_code, start_date=start_date, end_date=end_date,
        limit=limit,
    )
    data = fetch_as_dicts(conn, sql, params)
    return {"stock_code": stock_code, "count": len(data), "data": data}


@router.get("/api/flow/{stock_code}")
def get_capital_flow(
    stock_code: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(10000, ge=1, le=100000),
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    """获取个股主力资金流向(超大单/大单/中单/小单 净额与净占比)

    单位: 净额=元, 净占比=百分比(%)。
    口径: `main_net_amount` = `large_net_amount` + `xlarge_net_amount`(主力净额
    等于大单与超大单之和), 四类单净额合计约等于 0。

    注: 数据源单次只提供最近约 121 个交易日(约半年), 表内数据由每日采集逐步累积。
    """
    sql, params = build_query(
        "SELECT * FROM capital_flow",
        stock_code=stock_code, start_date=start_date, end_date=end_date,
        limit=limit,
    )
    data = fetch_as_dicts(conn, sql, params)
    resp = {"stock_code": stock_code, "count": len(data), "data": data}
    if not data:
        resp["note"] = "该股票无主力资金数据(可能采集尚未覆盖, 或为非股票标的)"
    return resp


@router.get("/api/dragon/{stock_code}")
def get_dragon_tiger(
    stock_code: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    sql, params = build_query(
        "SELECT * FROM dragon_tiger",
        stock_code=stock_code, start_date=start_date, end_date=end_date,
        limit=limit,
    )
    data = fetch_as_dicts(conn, sql, params)
    return {"stock_code": stock_code, "count": len(data), "data": data}
