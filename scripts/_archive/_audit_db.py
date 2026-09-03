# -*- coding: utf-8 -*-
import duckdb
import pandas as pd

DB = 'data/stock_data.duckdb'
conn = duckdb.connect(DB)

STOCKS = [
    "sh600519", "sz002843", "sz002272", "sh600346", "sh600309",
    "sh600887", "sh600276", "sz002714", "sz000725", "sh600089",
    "sz002648", "sh513700",
]

def header(s):
    print("\n" + "=" * 80)
    print(f"  {s}")
    print("=" * 80)

def check_table(table, key_cols=None, null_checks=None, range_checks=None, sample=3):
    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    cols = conn.execute(f"DESCRIBE {table}").fetchall()
    col_names = [c[0] for c in cols]
    print(f"\n[{table}]  {count} rows, {len(cols)} columns")
    print(f"  Columns: {col_names}")

    if count == 0:
        print("  [EMPTY]")
        return

    # Date range if trade_date or report_date exists
    for dc in ['trade_date', 'report_date', 'record_date', 'dividend_date', 'announcement_date']:
        if dc in col_names:
            try:
                dmin, dmax = conn.execute(f"SELECT MIN({dc}), MAX({dc}) FROM {table}").fetchone()
                print(f"  Date range [{dc}]: {dmin} ~ {dmax}")
            except:
                pass
            break

    # Per-stock counts
    if 'stock_code' in col_names:
        by_stock = conn.execute(f"""
            SELECT stock_code, COUNT(*) cnt FROM {table} 
            GROUP BY stock_code ORDER BY stock_code
        """).fetchall()
        print(f"  Per stock: {dict(by_stock)}")

    # NULL checks
    if null_checks:
        for nc in null_checks:
            if nc in col_names:
                nulls = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {nc} IS NULL").fetchone()[0]
                pct = nulls / count * 100 if count > 0 else 0
                if pct > 0:
                    print(f"  NULL [{nc}]: {nulls} ({pct:.1f}%)")

    # Range checks
    if range_checks:
        for rc, (lo, hi) in range_checks.items():
            if rc in col_names:
                bad = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {rc} IS NOT NULL AND ({rc} < {lo} OR {rc} > {hi})").fetchone()[0]
                if bad > 0:
                    print(f"  OUTLIER [{rc}]: {bad} rows outside [{lo}, {hi}]")

    # Sample
    if count > 0:
        df = conn.execute(f"SELECT * FROM {table} LIMIT {sample}").fetchdf()
        print(f"  Sample ({sample} rows):")
        print(df.to_string(max_colwidth=30))

# ==========================================
header("1. 日线数据 (stock_daily)")
# ==========================================
check_table("stock_daily",
    null_checks=['open', 'high', 'low', 'close', 'volume', 'amount', 'change_pct', 'amplitude'],
    range_checks={'close': (0.01, 100000), 'change_pct': (-20, 20), 'amplitude': (0, 30)})

# Check for negative prices
bad_price = conn.execute("SELECT COUNT(*) FROM stock_daily WHERE open <= 0 OR high <= 0 OR low <= 0 OR close <= 0").fetchone()[0]
print(f"  Negative prices: {bad_price}")

# Check consecutive dates gap
for sc in STOCKS[:3]:
    gaps = conn.execute(f"""
        WITH d AS (
            SELECT trade_date, 
                   LAG(trade_date) OVER (ORDER BY trade_date) as prev_date
            FROM stock_daily WHERE stock_code = '{sc}' ORDER BY trade_date
        )
        SELECT trade_date, prev_date, DATEDIFF('day', prev_date, trade_date) as gap
        FROM d WHERE DATEDIFF('day', prev_date, trade_date) > 7
        LIMIT 5
    """).fetchall()
    if gaps:
        print(f"  {sc} gaps > 7 days: {gaps[:3]}")

# ==========================================
header("2. 财务数据 (financial_statements)")
# ==========================================
check_table("financial_statements",
    null_checks=['total_revenue', 'net_profit', 'total_assets', 'equity_parent'])

# Check report_type distribution
rtypes = conn.execute("SELECT report_type, COUNT(*) FROM financial_statements GROUP BY report_type").fetchall()
print(f"  Report types: {rtypes}")

# ==========================================
header("3. 财务中间数据 (financial_intermediate)")
# ==========================================
check_table("financial_intermediate",
    null_checks=['net_profit_ttm', 'total_revenue_ttm', 'equity_parent', 'roe_ttm', 'roe_annual'])

# ==========================================
header("4. 技术指标 (technical_indicators)")
# ==========================================
check_table("technical_indicators",
    null_checks=['ma5', 'ma20', 'ma60', 'macd_dif', 'macd_dea', 'macd_bar', 'rsi14', 'kdj_k', 'kdj_d', 'kdj_j'],
    range_checks={'rsi14': (0, 100), 'kdj_k': (0, 100), 'kdj_d': (0, 100)})

# Check kdj_j range (can be negative or >100, but should be normal)
kdj_stats = conn.execute("""
    SELECT MIN(kdj_j), MAX(kdj_j), AVG(kdj_j) 
    FROM technical_indicators WHERE kdj_j IS NOT NULL
""").fetchone()
print(f"  KDJ_J stats: min={kdj_stats[0]:.2f}, max={kdj_stats[1]:.2f}, avg={kdj_stats[2]:.2f}")

# ==========================================
header("5. 估值指标 (valuation_indicators)")
# ==========================================
check_table("valuation_indicators",
    null_checks=['pe_ttm', 'pb', 'ps_ttm', 'market_cap'],
    range_checks={'pe_ttm': (0, 1000), 'pb': (0, 100), 'ps_ttm': (0, 500)})

# Check PE distribution
pe_stats = conn.execute("""
    SELECT 
        COUNT(CASE WHEN pe_ttm <= 0 THEN 1 END) as neg_pe,
        COUNT(CASE WHEN pe_ttm > 0 AND pe_ttm < 15 THEN 1 END) as low_pe,
        COUNT(CASE WHEN pe_ttm >= 15 AND pe_ttm < 30 THEN 1 END) as mid_pe,
        COUNT(CASE WHEN pe_ttm >= 30 AND pe_ttm < 60 THEN 1 END) as high_pe,
        COUNT(CASE WHEN pe_ttm >= 60 THEN 1 END) as very_high_pe,
        COUNT(CASE WHEN pe_ttm IS NULL THEN 1 END) as null_pe
    FROM valuation_indicators
""").fetchone()
print(f"  PE distribution: neg={pe_stats[0]}, 0-15={pe_stats[1]}, 15-30={pe_stats[2]}, 30-60={pe_stats[3]}, 60+={pe_stats[4]}, NULL={pe_stats[5]}")

# ==========================================
header("6. 北向资金 (northbound_flow)")
# ==========================================
check_table("northbound_flow",
    null_checks=['net_inflow', 'holding_shares', 'holding_value'])

# ==========================================
header("7. 融资融券 (margin_trading)")
# ==========================================
check_table("margin_trading")

# ==========================================
header("8. 分红 (dividends)")
# ==========================================
check_table("dividends",
    null_checks=['cash_per_share'])

# ==========================================
header("9. 公告 (announcements)")
# ==========================================
check_table("announcements",
    null_checks=['title', 'announcement_date'])

# ==========================================
header("10. 股本 (stock_capital)")
# ==========================================
check_table("stock_capital")

# ==========================================
header("11. 行业 (stock_industry)")
# ==========================================
check_table("stock_industry")

# ==========================================
header("12. 更新日志 (update_log)")
# ==========================================
check_table("update_log")

# ==========================================
header("13. 元数据 (column_metadata)")
# ==========================================
check_table("column_metadata")

conn.close()
print("\n" + "=" * 80)
print("  数据库检查完成")
print("=" * 80)