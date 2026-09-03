"""验证 PE/PB 修复结果"""
import duckdb

conn = duckdb.connect('data/stock_data.duckdb', read_only=True)

# 检查所有股票最新 PE/PB
print("=== 最新估值指标 (PE/PB/PS/股息率) ===")
print(f"{'股票代码':<12s} {'日期':<12s} {'收盘价':>8s} {'PE(TTM)':>8s} {'PB':>8s} {'PS(TTM)':>8s} {'股息率':>8s}")
print("-" * 85)

r = conn.execute("""
SELECT v.stock_code, v.trade_date, d.close, v.pe_ttm, v.pb, v.ps_ttm, v.dividend_yield
FROM valuation_indicators v
JOIN stock_daily d ON v.stock_code = d.stock_code AND v.trade_date = d.trade_date
WHERE (v.stock_code, v.trade_date) IN (
    SELECT stock_code, MAX(trade_date)
    FROM valuation_indicators
    GROUP BY stock_code
)
ORDER BY v.stock_code
""").fetchall()

for row in r:
    code, date, close, pe, pb, ps, div_yield = row
    pe_str = f"{pe:.2f}" if pe is not None else "N/A"
    pb_str = f"{pb:.2f}" if pb is not None else "N/A"
    ps_str = f"{ps:.2f}" if ps is not None else "N/A"
    div_str = f"{div_yield:.2f}%" if div_yield is not None else "N/A"
    close_str = f"{close:.2f}" if close is not None else "N/A"
    print(f"{code:<12s} {str(date):<12s} {close_str:>8s} {pe_str:>8s} {pb_str:>8s} {ps_str:>8s} {div_str:>8s}")

# 检查整体数据量
n = conn.execute("SELECT COUNT(*) FROM valuation_indicators").fetchone()[0]
n_stocks = conn.execute("SELECT COUNT(DISTINCT stock_code) FROM valuation_indicators").fetchone()[0]
print(f"\n估值指标总行数: {n:,}, 覆盖股票: {n_stocks}")

# 检查 PE 分布
print("\n=== PE 分布检查 ===")
pe_dist = conn.execute("""
    WITH latest AS (
        SELECT stock_code, pe_ttm
        FROM valuation_indicators
        WHERE (stock_code, trade_date) IN (
            SELECT stock_code, MAX(trade_date) FROM valuation_indicators GROUP BY stock_code
        )
    )
    SELECT 
        COUNT(*) as total,
        SUM(CASE WHEN pe_ttm > 0 AND pe_ttm < 30 THEN 1 ELSE 0 END) as normal,
        SUM(CASE WHEN pe_ttm >= 30 AND pe_ttm < 100 THEN 1 ELSE 0 END) as high,
        SUM(CASE WHEN pe_ttm >= 100 OR pe_ttm IS NULL THEN 1 ELSE 0 END) as very_high,
        SUM(CASE WHEN pe_ttm < 1 THEN 1 ELSE 0 END) as abnormal
    FROM latest
""").fetchone()
print(f"  正常(0-30): {pe_dist[1]}, 偏高(30-100): {pe_dist[2]}, 极高(100+): {pe_dist[3]}, 异常(<1): {pe_dist[4]}")

conn.close()
print("\n验证完成")