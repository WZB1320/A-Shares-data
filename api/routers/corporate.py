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
    """风险面 / 治理事件汇总: 质押 + 增减持 + 回购 + 限售解禁 + 股东户数

    - pledge: 周频质押比例快照(降序), `pledge_ratio` 为占总股本百分比
    - holder_change: 股东增减持记录(公告日降序), `change_type` ∈ {增持, 减持}
    - buyback: 回购方案(公告日降序), 数量单位=股, 金额单位=元
    - unlock: 限售解禁批次(解禁日降序), 含未来计划; `ratio_to_float` 为占解禁前
      流通市值**小数**(×100 得百分比), 未来解禁的 actual_* 为 NULL 属正常
    - holder_num: 股东户数(统计截止日降序), 持续减少 = 筹码集中
    - summary.risk_level: 零质押/低(<5%)/中(5~20%)/高(>20%), 按最新一期快照判定
    - 无质押记录的股票 pledge.count=0 属正常(零质押即健康); ETF 三张新表 count=0
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
    buyback = fetch_as_dicts(
        conn,
        "SELECT * FROM risk_buyback WHERE stock_code = ? "
        "ORDER BY announcement_date DESC",
        (stock_code,),
    )
    unlock = fetch_as_dicts(
        conn,
        "SELECT * FROM risk_unlock WHERE stock_code = ? ORDER BY free_date DESC",
        (stock_code,),
    )
    holder_num = fetch_as_dicts(
        conn,
        "SELECT * FROM risk_holder_num WHERE stock_code = ? ORDER BY stat_date DESC",
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

    today_str = date.today().isoformat()
    cutoff_90d = (date.today() - timedelta(days=90)).isoformat()
    future_90d = (date.today() + timedelta(days=90)).isoformat()

    # 未来 90 天解禁压力(按实际可流通数, 无则用解禁数量)
    upcoming_unlock = [
        r for r in unlock
        if r["free_date"] is not None and today_str <= str(r["free_date"]) <= future_90d
    ]

    # 回购: 是否有进行中的方案(实施中/董事会预案)
    ongoing_buyback = [
        r for r in buyback
        if r["progress"] in ("实施中", "董事会预案", "股东大会通过")
    ]

    resp = {
        "stock_code": stock_code,
        "pledge": {"count": len(pledge), "data": pledge},
        "holder_change": {"count": len(holder), "data": holder},
        "buyback": {"count": len(buyback), "data": buyback},
        "unlock": {"count": len(unlock), "data": unlock},
        "holder_num": {"count": len(holder_num), "data": holder_num},
        "summary": {
            "latest_pledge_ratio": latest_ratio,
            "latest_pledge_date": pledge[0]["trade_date"] if pledge else None,
            "risk_level": risk_level,
            "reduce_count_recent_90d": sum(
                1 for r in holder
                if r["change_type"] == "减持"
                and r["announcement_date"] is not None
                and str(r["announcement_date"]) >= cutoff_90d
            ),
            "ongoing_buyback_count": len(ongoing_buyback),
            "ongoing_buyback_amount": sum(
                float(r["done_amount"] or 0) for r in ongoing_buyback),
            "unlock_upcoming_90d_count": len(upcoming_unlock),
            "unlock_upcoming_90d_shares": sum(
                float(r["actual_free_shares"] or r["free_shares"] or 0)
                for r in upcoming_unlock),
            "holder_num_latest": holder_num[0]["holder_num"] if holder_num else None,
            "holder_num_trend": [
                {"stat_date": r["stat_date"], "holder_num": r["holder_num"],
                 "change_ratio": r["change_ratio"]}
                for r in holder_num[:4]
            ],
        },
    }
    if not any([pledge, holder, buyback, unlock, holder_num]):
        resp["note"] = ("该股票暂无风险面数据(可能采集尚未覆盖, 或确实零质押/无相关公告; "
                        "ETF 无风险面属正常)")
    return resp
