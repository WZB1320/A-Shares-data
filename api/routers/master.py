"""主数据与综合摘要接口: stock_master 主数据 / 单股综合摘要

合并自原 api/main.py 的 master 区块和 summary 区块,因为二者都是聚合查询,
共享 stock_master / 多表关联的设计模式。
"""
import duckdb
from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_db, fetch_as_dicts

router = APIRouter()


@router.get("/api/master")
def get_stock_master(conn: duckdb.DuckDBPyConnection = Depends(get_db)):
    """获取所有股票的主数据"""
    try:
        data = fetch_as_dicts(conn, "SELECT * FROM stock_master ORDER BY stock_code")
        return {"count": len(data), "data": data}
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="stock_master 表不存在,请先运行初始化脚本",
        )


@router.get("/api/master/{stock_code}")
def get_stock_detail(
    stock_code: str,
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    """获取单只股票的主数据及关联统计,并附带最新收盘价字段供调用方使用。"""
    master = conn.execute(
        "SELECT * FROM stock_master WHERE stock_code = ?", (stock_code,)
    ).fetchone()
    if not master:
        raise HTTPException(status_code=404, detail=f"股票 '{stock_code}' 未找到")

    desc = conn.execute("SELECT * FROM stock_master LIMIT 0").description
    columns = [d[0] for d in desc]
    result = dict(zip(columns, [
        str(v) if v is not None else None for v in master
    ]))

    # 关联统计
    stats = conn.execute("""
        SELECT
            (SELECT MAX(trade_date) FROM stock_daily WHERE stock_code = ?) AS latest_daily,
            (SELECT COUNT(*) FROM stock_daily WHERE stock_code = ?) AS daily_count,
            (SELECT MAX(report_date) FROM financial_statements WHERE stock_code = ?) AS latest_financial,
            (SELECT COUNT(*) FROM announcements WHERE stock_code = ?) AS announcement_count
    """, (stock_code,) * 4).fetchone()

    result["latest_daily"] = str(stats[0]) if stats[0] else None
    result["daily_count"] = stats[1]
    result["latest_financial"] = str(stats[2]) if stats[2] else None
    result["announcement_count"] = stats[3]

    # ---- 附带最新收盘价(补齐调用方 price_at_analysis 来源) ----
    # 注意 stock_daily 字段: 成交额=amount, 涨跌幅=change_pct, 换手率=turnover
    try:
        price_row = conn.execute(
            "SELECT trade_date, open, close, high, low, volume, amount, change_pct, turnover, prev_close "
            "FROM stock_daily WHERE stock_code=? ORDER BY trade_date DESC LIMIT 1",
            (stock_code,)
        ).fetchone()
        if price_row:
            result["price_date"]  = price_row[0].isoformat() if price_row[0] else None
            result["open_price"]  = float(price_row[1]) if price_row[1] is not None else None
            result["close_price"] = float(price_row[2]) if price_row[2] is not None else None
            result["high_price"]  = float(price_row[3]) if price_row[3] is not None else None
            result["low_price"]   = float(price_row[4]) if price_row[4] is not None else None
            result["volume"]              = price_row[5]
            result["turnover_amount"]     = float(price_row[6]) if price_row[6] is not None else None  # 成交额
            result["pct_chg"]             = float(price_row[7]) if price_row[7] is not None else None  # 涨跌幅
            result["turnover_rate"]       = float(price_row[8]) if price_row[8] is not None else None  # 换手率
            result["prev_close"]          = float(price_row[9]) if price_row[9] is not None else None
        else:
            result["price_date"]  = None
            result["close_price"] = None
            result["_warning"]    = "无日线数据,close_price 为空"
    except duckdb.BinderException:
        # 老版本数据库可能没有 change_pct/turnover/prev_close 列,退化到最小集合
        price_row = conn.execute(
            "SELECT trade_date, open, close, high, low, volume, amount "
            "FROM stock_daily WHERE stock_code=? ORDER BY trade_date DESC LIMIT 1",
            (stock_code,)
        ).fetchone()
        if price_row:
            result["price_date"]  = price_row[0].isoformat() if price_row[0] else None
            result["open_price"]  = float(price_row[1]) if price_row[1] is not None else None
            result["close_price"] = float(price_row[2]) if price_row[2] is not None else None
            result["high_price"]  = float(price_row[3]) if price_row[3] is not None else None
            result["low_price"]   = float(price_row[4]) if price_row[4] is not None else None
            result["volume"]              = price_row[5]
            result["turnover_amount"]     = float(price_row[6]) if price_row[6] is not None else None
            result["pct_chg"]             = None
            result["turnover_rate"]       = None
            result["prev_close"]          = None
        else:
            result["price_date"]  = None
            result["close_price"] = None
            result["_warning"]    = "无日线数据,close_price 为空"
    except Exception as e:
        result["close_price"] = None
        result["_warning"]    = f"查询最新收盘价失败: {type(e).__name__}: {e}"

    return result


@router.get("/api/summary/{stock_code}")
def get_stock_summary(
    stock_code: str,
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    """获取单只股票的综合数据摘要,聚合各表关键字段,并对 None 值标注原因

    返回:
      - price: 最新股价(来自 stock_daily)
      - valuation: 最新估值指标(来自 valuation_indicators, 含股价)
      - financial: 最新财务指标(来自 financial_intermediate)
      - northbound: 北向资金概况
      - unavailable_fields: 不可用字段及原因说明
    """
    unavailable = []

    # ---- 1. 最新股价 ----
    price_row = conn.execute(
        "SELECT trade_date, open, close, high, low, volume, turnover, outstanding_share "
        "FROM stock_daily WHERE stock_code=? ORDER BY trade_date DESC LIMIT 1",
        (stock_code,)
    ).fetchone()
    price = None
    if price_row:
        price = {
            "trade_date": price_row[0].isoformat() if price_row[0] else None,
            "open": float(price_row[1]) if price_row[1] is not None else None,
            "close": float(price_row[2]) if price_row[2] is not None else None,
            "high": float(price_row[3]) if price_row[3] is not None else None,
            "low": float(price_row[4]) if price_row[4] is not None else None,
            "volume": price_row[5],
            "turnover": float(price_row[6]) if price_row[6] is not None else None,
            "outstanding_share": price_row[7],
        }
        if price_row[2] is None or float(price_row[2]) == 0:
            unavailable.append({"field": "close", "reason": "该股票可能停牌/退市,无收盘价数据"})
    else:
        unavailable.append({"field": "close", "reason": "无日线数据"})

    # ---- 2. 估值指标(含股价) ----
    val_row = conn.execute(
        "SELECT trade_date, pe_ttm, pb, ps_ttm, dividend_yield, roe, "
        "pe_annual, ps_annual, roe_ttm, roe_annual "
        "FROM valuation_indicators WHERE stock_code=? ORDER BY trade_date DESC LIMIT 1",
        (stock_code,)
    ).fetchone()
    valuation = None
    if val_row:
        valuation = {
            "trade_date": val_row[0].isoformat() if val_row[0] else None,
            "pe_ttm": float(val_row[1]) if val_row[1] is not None else None,
            "pb": float(val_row[2]) if val_row[2] is not None else None,
            "ps_ttm": float(val_row[3]) if val_row[3] is not None else None,
            "dividend_yield": float(val_row[4]) if val_row[4] is not None else None,
            "roe": float(val_row[5]) if val_row[5] is not None else None,
            "pe_annual": float(val_row[6]) if val_row[6] is not None else None,
            "ps_annual": float(val_row[7]) if val_row[7] is not None else None,
            "roe_ttm": float(val_row[8]) if val_row[8] is not None else None,
            "roe_annual": float(val_row[9]) if val_row[9] is not None else None,
        }
        if price and price.get("close"):
            valuation["stock_price"] = price["close"]

    # ---- 3. 财务指标 ----
    fin_row = conn.execute(
        "SELECT report_date, eps, roe_ttm, gross_margin_ttm, net_margin_parent_ttm, "
        "current_ratio, quick_ratio, roa_ttm, revenue_yoy_ttm, net_profit_yoy_ttm "
        "FROM financial_intermediate WHERE stock_code=? ORDER BY report_date DESC LIMIT 1",
        (stock_code,)
    ).fetchone()
    financial = None
    if fin_row:
        financial = {
            "report_date": fin_row[0].isoformat() if fin_row[0] else None,
            "eps": float(fin_row[1]) if fin_row[1] is not None else None,
            "roe_ttm": float(fin_row[2]) if fin_row[2] is not None else None,
            "gross_margin_ttm": float(fin_row[3]) if fin_row[3] is not None else None,
            "net_margin_ttm": float(fin_row[4]) if fin_row[4] is not None else None,
            "current_ratio": float(fin_row[5]) if fin_row[5] is not None else None,
            "quick_ratio": float(fin_row[6]) if fin_row[6] is not None else None,
            "roa_ttm": float(fin_row[7]) if fin_row[7] is not None else None,
            "revenue_yoy_ttm": float(fin_row[8]) if fin_row[8] is not None else None,
            "net_profit_yoy_ttm": float(fin_row[9]) if fin_row[9] is not None else None,
        }
        if fin_row[2] is None:
            unavailable.append({"field": "roe_ttm", "reason": "净资产为负或数据缺失,ROE不可计算"})
        if fin_row[3] is None:
            unavailable.append({"field": "gross_margin_ttm", "reason": "营业成本数据缺失,毛利率不可计算"})
        if fin_row[5] is None:
            unavailable.append({"field": "current_ratio", "reason": "流动资产/负债数据缺失"})
        if fin_row[6] is None:
            unavailable.append({"field": "quick_ratio", "reason": "流动资产/负债数据缺失"})

    # ---- 4. 北向资金 ----
    nb_count = conn.execute(
        "SELECT COUNT(*) FROM northbound_flow WHERE stock_code=?",
        (stock_code,)
    ).fetchone()[0]
    northbound = {"count": nb_count, "north_available": nb_count > 0}
    if nb_count == 0:
        northbound["note"] = "该股票非沪深港通标的,无北向资金数据"
        unavailable.append({"field": "northbound", "reason": "非沪深港通标的,数据源不提供"})

    return {
        "stock_code": stock_code,
        "price": price,
        "valuation": valuation,
        "financial": financial,
        "northbound": northbound,
        "unavailable_fields": unavailable,
    }
