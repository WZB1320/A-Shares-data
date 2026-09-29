"""公司基础信息接口: 分红 / 公告 / 股本 / 行业

注意: industry 路由顺序敏感, 静态路径必须在 {path_param} 之前声明,
否则 FastAPI 会把 "list" 误匹配为 {stock_code}
"""
import duckdb
from datetime import date, timedelta
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


@router.get("/api/risk/{stock_code}")
def get_risk(
    stock_code: str,
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    """风险面汇总: 股权质押 + 股东增减持

    - pledge: 周频质押比例快照(降序), `pledge_ratio` 为占总股本百分比
    - holder_change: 股东增减持记录(公告日降序), `change_type` ∈ {增持, 减持}
    - summary.risk_level: 零质押/低(<5%)/中(5~20%)/高(>20%), 按最新一期快照判定
    - 无质押记录的股票 pledge.count=0 属正常(零质押即健康)
    """
    pledge = fetch_as_dicts(
        conn,
        "SELECT * FROM risk_pledge WHERE stock_code = ? ORDER BY trade_date DESC",
        (stock_code,),
    )
    holder = fetch_as_dicts(
        conn,
        "SELECT * FROM risk_holder_change WHERE stock_code = ? "
        "ORDER BY announcement_date DESC, holder_name",
        (stock_code,),
    )

    latest_ratio = pledge[0]["pledge_ratio"] if pledge else None
    if latest_ratio is None:
        risk_level = "无质押记录(视为零质押)"
    elif float(latest_ratio) <= 5:
        risk_level = "低"
    elif float(latest_ratio) <= 20:
        risk_level = "中"
    else:
        risk_level = "高"

    resp = {
        "stock_code": stock_code,
        "pledge": {"count": len(pledge), "data": pledge},
        "holder_change": {"count": len(holder), "data": holder},
        "summary": {
            "latest_pledge_ratio": latest_ratio,
            "latest_pledge_date": pledge[0]["trade_date"] if pledge else None,
            "risk_level": risk_level,
            "reduce_count_recent_90d": sum(
                1 for r in holder
                if r["change_type"] == "减持"
                and r["announcement_date"] is not None
                and str(r["announcement_date"]) >= (date.today() - timedelta(days=90)).isoformat()
            ),
        },
    }
    if not pledge and not holder:
        resp["note"] = "该股票暂无风险面数据(可能采集尚未覆盖, 或确实零质押且无增减持公告)"
    return resp
