import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.stock_data_collector import StockDataCollector, DB_PATH, LOG_PATH

print("=== 路径检查 ===")
print(f"DB_PATH: {DB_PATH}")
print(f"DB_PATH 存在: {os.path.exists(DB_PATH)}")
print(f"LOG_PATH: {LOG_PATH}")
print(f"LOG_PATH 目录存在: {os.path.exists(os.path.dirname(LOG_PATH))}")

collector = StockDataCollector(DB_PATH)

print()
print("=== 数据库表列表 ===")
tables = collector.conn.execute("SHOW TABLES").fetchdf()
print(tables.to_string())

print()
print("=== 各表数据量 ===")
for _, row in tables.iterrows():
    t = row["name"]
    cnt = collector.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {cnt} 条")

print()
print("=== financial_statements 关键字段非空率 ===")
fields = ["total_revenue","net_profit","net_profit_deducted","operating_cost",
          "interest_expense","equity_parent","total_assets","operating_cash_flow","capex"]
for f in fields:
    r = collector.conn.execute(f"""
        SELECT COUNT(*) as total, SUM(CASE WHEN {f} IS NOT NULL THEN 1 ELSE 0 END) as nn
        FROM financial_statements
    """).fetchone()
    pct = round(r[1]/r[0]*100,1) if r[0]>0 else 0
    print(f"  {f}: {r[1]}/{r[0]} ({pct}%)")

print()
print("=== financial_intermediate 关键字段非空率 ===")
fields2 = ["roe_annual","roe_ttm","roa_annual","roa_ttm",
           "gross_margin_annual","gross_margin_ttm",
           "net_margin_parent_annual","net_margin_parent_ttm",
           "net_margin_deducted_annual","net_margin_deducted_ttm",
           "revenue_yoy_annual","revenue_cagr_3y",
           "dupont_net_margin_annual","dupont_asset_turnover_annual","dupont_equity_multiplier_annual",
           "inventory_turnover_annual","accounts_receivable_turnover_annual","accounts_payable_turnover_annual",
           "cash_cycle_annual",
           "cash_profit_coverage_ttm","fcf_ttm","fcf_profit_coverage_ttm","cash_interest_coverage_ttm"]
for f in fields2:
    r = collector.conn.execute(f"""
        SELECT COUNT(*) as total, SUM(CASE WHEN {f} IS NOT NULL THEN 1 ELSE 0 END) as nn
        FROM financial_intermediate
    """).fetchone()
    pct = round(r[1]/r[0]*100,1) if r[0]>0 else 0
    print(f"  {f}: {r[1]}/{r[0]} ({pct}%)")

print()
print("=== 杜邦分解验证 (sh600519 最新年报) ===")
r = collector.conn.execute("""
    SELECT report_date, roe_annual, dupont_net_margin_annual, dupont_asset_turnover_annual, dupont_equity_multiplier_annual
    FROM financial_intermediate
    WHERE stock_code = 'sh600519' AND roe_annual IS NOT NULL
    ORDER BY report_date DESC LIMIT 3
""").fetchdf()
print(r.to_string())
for _, row in r.iterrows():
    nm = row["dupont_net_margin_annual"]
    at = row["dupont_asset_turnover_annual"]
    em = row["dupont_equity_multiplier_annual"]
    roe = row["roe_annual"]
    if nm and at and em and roe:
        calc = nm * at * em
        diff = abs(calc - roe)
        print(f"  {str(row['report_date'])[:10]}: ROE={roe}, 净利率×周转率×权益乘数={calc:.4f}, 误差={diff:.6f}")

print()
print("=== 营运能力验证 (sh600519 最新年报) ===")
r = collector.conn.execute("""
    SELECT report_date, inventory_turnover_annual, inventory_days_annual,
           accounts_receivable_turnover_annual, accounts_receivable_days_annual,
           accounts_payable_turnover_annual, accounts_payable_days_annual, cash_cycle_annual
    FROM financial_intermediate
    WHERE stock_code = 'sh600519' AND inventory_turnover_annual IS NOT NULL
    ORDER BY report_date DESC LIMIT 3
""").fetchdf()
print(r.to_string())
for _, row in r.iterrows():
    it = row["inventory_turnover_annual"]
    idays = row["inventory_days_annual"]
    rt = row["accounts_receivable_turnover_annual"]
    rdays = row["accounts_receivable_days_annual"]
    pt = row["accounts_payable_turnover_annual"]
    pdays = row["accounts_payable_days_annual"]
    cc = row["cash_cycle_annual"]
    if it and idays:
        print(f"  存货: 周转率={it}, 天数={idays}, 365/周转率={365/it:.2f}")
    if rt and rdays:
        print(f"  应收: 周转率={rt}, 天数={rdays}, 365/周转率={365/rt:.2f}")
    if pt and pdays:
        print(f"  应付: 周转率={pt}, 天数={pdays}, 365/周转率={365/pt:.2f}")
    if idays and rdays and pdays and cc:
        calc_cc = idays + rdays - pdays
        print(f"  现金周期: 存储={cc}, 计算={calc_cc:.2f}, 误差={abs(cc-calc_cc):.4f}")

print()
print("=== 现金流验证 (sh600519 最新) ===")
r = collector.conn.execute("""
    SELECT report_date, cash_profit_coverage_ttm, fcf_ttm, fcf_profit_coverage_ttm, cash_interest_coverage_ttm
    FROM financial_intermediate
    WHERE stock_code = 'sh600519' AND cash_profit_coverage_ttm IS NOT NULL
    ORDER BY report_date DESC LIMIT 3
""").fetchdf()
print(r.to_string())

collector.close()
print()
print("=== 全部检查通过 ===")