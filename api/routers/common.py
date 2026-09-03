"""公共接口: 健康检查 / 股票列表 / 最新数据日期 / 批量基础信息(含价格)"""
from typing import List, Optional

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import DB_PATH, get_db, rows_to_dicts

router = APIRouter()

# stock_daily 表的权威字段名(与 src/database/models.py 保持一致)
# 注意: 成交额 = amount; 涨跌幅 = change_pct; 换手率 = turnover
_STOCK_DAILY_COLS = {
    "stock_code", "trade_date", "open", "high", "low", "close",
    "volume", "amount", "adjust_factor", "prev_close", "change_pct",
    "amplitude", "body_size", "upper_shadow", "lower_shadow",
    "high_20", "low_20", "high_60", "low_60", "turnover", "outstanding_share",
}


def _safe_price_sql(code_filter: List[str]):
    """构造「每只股票最新一条日线关联主数据与估值」的 SQL。

    - 通过查询 duckdb system catalog 获取 stock_daily/stock_master/valuation_indicators
      的实际列集合,对缺失列返回 NULL,避免因单表 schema 不完整导致 500。
    - 返回 (sql, params)。
    """
    import re as _re
    placeholders = ",".join("?" for _ in code_filter)

    # 函数内部延迟到 DB 依赖里获取连接最稳妥,这里采用「列名统一用 NULL 兜底」策略:
    # 先基于 _STOCK_DAILY_COLS 等常量写一组 COALESCE 列,再封装查询。
    # 为了兼容各种数据库版本(表列缺失),直接在 SQL 里先查系统表获取列集合再动态
    # 构造比较重,改用「异常捕获 + 二次回退查询(少字段)」的策略,由调用方处理。

    # 成交额字段历史上叫 amount,某些版本可能也有 turnover(是换手率)。为避免歧义,
    # 两者都映射出来(turnover_amount 对应成交额,turnover_rate 对应换手率)。
    daily_select = ", ".join([
        "sd.stock_code",
        "sd.trade_date                AS price_date",
        "sd.open                      AS open_price",
        "sd.close                     AS close_price",
        "sd.high                      AS high_price",
        "sd.low                       AS low_price",
        "sd.volume                    AS volume",
        "sd.amount                    AS turnover_amount",   # 成交额
        "sd.change_pct                AS pct_chg",           # 涨跌幅
        "sd.turnover                  AS turnover_rate",     # 换手率
        "sd.prev_close                AS prev_close",
        "sd.outstanding_share         AS outstanding_share",
    ])

    daily_where = (
        f"WHERE stock_code IN ({placeholders})" if code_filter else ""
    )
    main_where = (
        f"WHERE d.stock_code IN ({placeholders})" if code_filter else ""
    )
    params = tuple(code_filter * 2) if code_filter else tuple()

    sql = f"""
        SELECT
            d.stock_code,
            COALESCE(m.stock_name, '')    AS stock_name,
            COALESCE(m.stock_name_cn, '') AS stock_name_cn,
            COALESCE(m.market, '')        AS market,
            COALESCE(m.board, '')         AS board,
            m.listing_date                AS listing_date,
            COALESCE(m.status, '正常')     AS status,
            COALESCE(m.is_etf, false)     AS is_etf,
            d.price_date,
            d.open_price,
            d.close_price,
            d.high_price,
            d.low_price,
            d.volume,
            d.turnover_amount,
            d.pct_chg,
            d.turnover_rate,
            d.prev_close,
            d.outstanding_share,
            v.pe_ttm,
            v.pb,
            v.ps_ttm,
            v.dividend_yield
        FROM (
            SELECT stock_code, MAX(trade_date) AS max_date
            FROM stock_daily
            {daily_where}
            GROUP BY stock_code
        ) latest
        JOIN (
            SELECT
                {daily_select}
            FROM stock_daily sd
        ) d
            ON d.stock_code = latest.stock_code AND d.price_date = latest.max_date
        LEFT JOIN stock_master m
            ON m.stock_code = d.stock_code
        LEFT JOIN (
            SELECT stock_code, trade_date, pe_ttm, pb, ps_ttm, dividend_yield
            FROM valuation_indicators
        ) v
            ON v.stock_code = d.stock_code AND v.trade_date = d.price_date
        {main_where}
        ORDER BY d.stock_code
    """
    return sql, params


def _fallback_price_sql(code_filter: List[str]):
    """回退 SQL: 只查 stock_daily 必须列 (stock_code/trade_date/open/close/high/low/volume/amount),
    避免 schema 演进导致字段 BinderException。"""
    placeholders = ",".join("?" for _ in code_filter)
    daily_where = f"WHERE stock_code IN ({placeholders})" if code_filter else ""
    main_where  = f"WHERE d.stock_code IN ({placeholders})" if code_filter else ""
    params = tuple(code_filter * 2) if code_filter else tuple()
    sql = f"""
        SELECT
            d.stock_code,
            ''                AS stock_name,
            ''                AS stock_name_cn,
            ''                AS market,
            ''                AS board,
            NULL              AS listing_date,
            '正常'             AS status,
            false             AS is_etf,
            d.trade_date      AS price_date,
            d.open            AS open_price,
            d.close           AS close_price,
            d.high            AS high_price,
            d.low             AS low_price,
            d.volume          AS volume,
            d.amount          AS turnover_amount,
            NULL              AS pct_chg,
            NULL              AS turnover_rate,
            NULL              AS prev_close,
            NULL              AS outstanding_share,
            NULL              AS pe_ttm,
            NULL              AS pb,
            NULL              AS ps_ttm,
            NULL              AS dividend_yield
        FROM (
            SELECT stock_code, MAX(trade_date) AS max_date
            FROM stock_daily
            {daily_where}
            GROUP BY stock_code
        ) latest
        JOIN stock_daily d
            ON d.stock_code = latest.stock_code AND d.trade_date = latest.max_date
        {main_where}
        ORDER BY d.stock_code
    """
    return sql, params


@router.get("/api/health")
def health():
    return {"status": "ok", "db_path": str(DB_PATH)}


@router.get("/api/stocks")
def get_stocks(
    with_price: bool = Query(
        False,
        description="是否返回最新收盘价等基础信息(兼容默认仅返回代码数组)"
    ),
    stock_codes: Optional[str] = Query(
        None,
        description="可选: 按股票代码过滤,多个用英文逗号分隔(如 sh600519,sh600089)"
    ),
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    """获取股票列表。默认只返回代码数组; with_price=true 时返回含最新收盘价/名称的完整列表。

    调用方如需用 price_at_analysis 回测闭环,请传 with_price=true 或改用 /api/basic_info。
    """
    code_filter: List[str] = (
        [c.strip() for c in stock_codes.split(",") if c.strip()]
        if stock_codes else []
    )
    placeholders = ",".join("?" for _ in code_filter)

    if with_price:
        # 三级降级: 1) full SQL -> 2) fallback SQL -> 3) 表缺失时返回空数组
        try:
            sql, params = _safe_price_sql(code_filter)
            result = conn.execute(sql, params)
        except duckdb.BinderException:
            # 列缺失回退
            sql, params = _fallback_price_sql(code_filter)
            result = conn.execute(sql, params)
        except Exception as e:
            # 任何数据库级异常(表不存在/DB 未初始化等)都返回空集合 + 提示,不抛 500
            empty: list = []
            return {
                "stocks": empty,
                "count": 0,
                "note": (
                    "close_price 即最新收盘价,可直接用于回测 price_at_analysis 字段。"
                    f"当前查询未返回数据: {type(e).__name__}: {e}"
                ),
                "_warning": f"stock_daily/stock_master 等表未就绪: {type(e).__name__}: {e}",
            }
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
        data = rows_to_dicts(rows, columns)
        for row in data:
            if row.get("close_price") is None:
                row["_warning"] = "close_price 为空,请确认该股票是否已采集最新日线数据"
        return {
            "stocks": data,
            "count": len(data),
            "note": "close_price 即最新收盘价,可直接用于回测 price_at_analysis 字段(turnover_amount=成交额, pct_chg=涨跌幅, turnover_rate=换手率)"
        }

    # 默认模式: 仅返回股票代码数组(保持向后兼容,表缺失时返回空数组而非 500)
    try:
        if code_filter:
            sql = (
                f"SELECT DISTINCT stock_code FROM stock_daily "
                f"WHERE stock_code IN ({placeholders}) ORDER BY stock_code"
            )
            rows = conn.execute(sql, tuple(code_filter)).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT stock_code FROM stock_daily ORDER BY stock_code"
            ).fetchall()
        return {"stocks": [r[0] for r in rows], "count": len(rows)}
    except Exception:
        return {"stocks": [], "count": 0}


@router.get("/api/basic_info")
def get_basic_info(
    stock_codes: Optional[str] = Query(
        None,
        description="可选: 按股票代码过滤,多个用英文逗号分隔;为空则返回全部股票"
    ),
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    """批量获取股票基础信息(含最新收盘价),专供外部回测/研究项目取 price_at_analysis 等字段。

    返回关键字段:
      - stock_code / stock_name / stock_name_cn / market / board / status / is_etf
      - price_date / open_price / close_price / high_price / low_price
      - volume / turnover_amount(成交额) / pct_chg(涨跌幅) / turnover_rate(换手率) / prev_close
      - pe_ttm / pb / ps_ttm / dividend_yield

    说明: 若某只股票返回 close_price=null,通常是停牌/退市或尚未同步日线数据。
    若数据库未初始化(表缺失),接口会返回 200 + count=0 并附带 _warning,不会抛 500。
    """
    code_list: List[str] = (
        [c.strip() for c in stock_codes.split(",") if c.strip()]
        if stock_codes else []
    )

    try:
        sql, params = _safe_price_sql(code_list)
        result = conn.execute(sql, params)
    except duckdb.BinderException:
        sql, params = _fallback_price_sql(code_list)
        result = conn.execute(sql, params)
    except Exception as e:
        return {
            "count": 0,
            "data": [],
            "price_field_hint": "字段 close_price 即最新收盘价,外部项目可直接映射到 price_at_analysis",
            "_warning": f"数据库未就绪: {type(e).__name__}: {e}。请先运行采集项目(https://github.com/WZB1320/A-Shares-data.git)初始化 stock_daily 等表后重试。",
        }

    columns = [desc[0] for desc in result.description]
    rows = result.fetchall()
    data = rows_to_dicts(rows, columns)
    # 标注缺失的价格行,便于调用方直接定位问题
    for row in data:
        if row.get("close_price") is None:
            row["_warning"] = "close_price 为空,请确认该股票是否已采集最新日线数据"
    return {
        "count": len(data),
        "data": data,
        "price_field_hint": "字段 close_price 即最新收盘价,外部项目可直接映射到 price_at_analysis (成交额=turnover_amount, 涨跌幅=pct_chg, 换手率=turnover_rate)"
    }


@router.get("/api/latest")
def get_latest(
    stock_code: Optional[str] = Query(None),
    conn: duckdb.DuckDBPyConnection = Depends(get_db),
):
    if stock_code:
        rows = conn.execute(
            "SELECT stock_code, MAX(trade_date) FROM stock_daily "
            "WHERE stock_code = ? GROUP BY stock_code",
            (stock_code,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT stock_code, MAX(trade_date) FROM stock_daily "
            "GROUP BY stock_code ORDER BY stock_code"
        ).fetchall()
    return {
        "latest": [
            {"stock_code": r[0], "latest_date": r[1].isoformat() if r[1] else None}
            for r in rows
        ]
    }
