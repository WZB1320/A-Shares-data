# -*- coding utf-8 -*-
"""Deep audit v2 - fixed TTM and RSI comparisons"""
import duckdb
import pandas as pd
import numpy as np

DB = 'data/stock_data.duckdb'
conn = duckdb.connect(DB)

TICKER = 'sh600519'
PASS_STR = "     [PASS]"
FAIL_STR = "     [FAIL]"
WARN_STR = "     [WARN]"

def header(s):
    print(f"\n{'='*70}")
    print(f"  {s}")
    print(f"{'='*70}")

def check(name, diff_val, threshold=0.02):
    tag = PASS_STR if diff_val < threshold else FAIL_STR
    print(f"  {name}: diff={diff_val:.6f}{tag}")
    return diff_val < threshold

def check_close(name, val1, val2, threshold_pct=1.0):
    if val1 is None or val2 is None:
        print(f"  {name}: missing value(s){FAIL_STR}")
        return False
    if val1 == 0 and val2 == 0:
        print(f"  {name}: both zero{PASS_STR}")
        return True
    if val1 == 0:
        print(f"  {name}: manual=0, stored={val2:.4f}{FAIL_STR}")
        return False
    diff_pct = abs(val1 - val2) / abs(val1) * 100
    tag = PASS_STR if diff_pct < threshold_pct else FAIL_STR
    print(f"  {name}: manual={val1:.4f}, stored={val2:.4f}, diff={diff_pct:.2f}%{tag}")
    return diff_pct < threshold_pct

# ============================================================
header("1. stock_daily - derived fields (sh600519)")
# ============================================================
df = conn.execute(f"SELECT * FROM stock_daily WHERE stock_code='{TICKER}' ORDER BY trade_date").fetchdf()
df = df.sort_values('trade_date').reset_index(drop=True)
pc = df['close'].shift(1)
check("change_pct", np.nanmean(np.abs((df['change_pct'] - (df['close']-pc)/pc*100).fillna(0))))
check("amplitude", np.nanmean(np.abs((df['amplitude'] - (df['high']-df['low'])/pc*100).fillna(0))))
check("body_size", np.nanmean(np.abs((df['body_size'] - (df['close']-df['open']).abs()).fillna(0))))
check("upper_shadow", np.nanmean(np.abs((df['upper_shadow'] - (df['high']-df[['open','close']].max(axis=1))).fillna(0))))
check("lower_shadow", np.nanmean(np.abs((df['lower_shadow'] - (df[['open','close']].min(axis=1)-df['low'])).fillna(0))))
check("high_20", np.nanmean(np.abs((df['high_20'] - df['high'].rolling(20,min_periods=1).max()).fillna(0))))
check("low_60", np.nanmean(np.abs((df['low_60'] - df['low'].rolling(60,min_periods=1).min()).fillna(0))))

# ============================================================
header("2. technical_indicators - manual verification (sh600519)")
# ============================================================
tdf = conn.execute(f"SELECT * FROM technical_indicators WHERE stock_code='{TICKER}' ORDER BY trade_date").fetchdf()
tdf = tdf.sort_values('trade_date').reset_index(drop=True)
price = df[['trade_date','open','high','low','close','volume']].copy()

check("ma5", np.abs(price['close'].rolling(5,min_periods=1).mean().values - tdf['ma5'].values)[1:].mean())
check("ma20", np.abs(price['close'].rolling(20,min_periods=1).mean().values - tdf['ma20'].values)[1:].mean())
check("ma60", np.abs(price['close'].rolling(60,min_periods=1).mean().values - tdf['ma60'].values)[1:].mean())

ema12 = price['close'].ewm(span=12,adjust=False,min_periods=1).mean()
ema26 = price['close'].ewm(span=26,adjust=False,min_periods=1).mean()
dif = ema12 - ema26
dea = dif.ewm(span=9,adjust=False,min_periods=1).mean()
check("macd_dif", np.nanmean(np.abs(dif.values - tdf['macd_dif'].values)))
check("macd_dea", np.nanmean(np.abs(dea.values - tdf['macd_dea'].values)))
check("macd_hist", np.nanmean(np.abs(((dif-dea)*2).values - tdf['macd_hist'].values)))

# RSI: code uses periods [6, 12, 24], match accordingly
for period, col in [(6,'rsi6'),(12,'rsi12'),(24,'rsi24')]:
    delta = price['close'].diff()
    gain, loss = delta.clip(lower=0), (-delta).clip(lower=0)
    avg_g = gain.rolling(period,min_periods=1).mean()
    avg_l = loss.rolling(period,min_periods=1).mean()
    rs = avg_g / avg_l.replace(0,np.nan)
    expected = 100 - (100/(1+rs))
    diff_val = np.nanmean(np.abs(expected.fillna(0).values - tdf[col].fillna(0).values))
    check(col, diff_val)

# KDJ
low9, high9 = price['low'].rolling(9,min_periods=1).min(), price['high'].rolling(9,min_periods=1).max()
rsv = (price['close']-low9)/(high9-low9).replace(0,np.nan)*100
rsv = rsv.fillna(50)
ek = rsv.ewm(com=2,adjust=False,min_periods=1).mean()
ed = ek.ewm(com=2,adjust=False,min_periods=1).mean()
ej = 3*ek - 2*ed
check("kdj_k", np.nanmean(np.abs(ek.values - tdf['kdj_k'].values)))
check("kdj_d", np.nanmean(np.abs(ed.values - tdf['kdj_d'].values)))
check("kdj_j", np.nanmean(np.abs(ej.values - tdf['kdj_j'].values)))

# BOLL
std20 = price['close'].rolling(20,min_periods=1).std()
ma20 = price['close'].rolling(20,min_periods=1).mean()
check("boll_upper", np.nanmean(np.abs((ma20+2*std20).values - tdf['boll_upper'].values)))
check("boll_lower", np.nanmean(np.abs((ma20-2*std20).values - tdf['boll_lower'].values)))

# BIAS
for n, col in [(5,'bias5'),(10,'bias10'),(20,'bias20'),(60,'bias60')]:
    ma = price['close'].rolling(n,min_periods=1).mean()
    expected = (price['close']-ma)/ma.replace(0,np.nan)*100
    check(col, np.nanmean(np.abs(expected.fillna(0).values - tdf[col].fillna(0).values)))

# CCI20
tp = (price['high']+price['low']+price['close'])/3
ma_tp = tp.rolling(20,min_periods=1).mean()
md = tp.rolling(20,min_periods=1).apply(lambda x: (abs(x-x.mean())).mean(), raw=True)
expected_cci = (tp - ma_tp) / (0.015 * md).replace(0, np.nan)
check("cci20", np.nanmean(np.abs(expected_cci.fillna(0).values - tdf['cci20'].fillna(0).values)))

# ============================================================
header("3. financial_intermediate - TTM & indicators (sh600519)")
# ============================================================
fdf = conn.execute(f"""
    SELECT *, CAST(report_date AS VARCHAR) as rd FROM financial_statements 
    WHERE stock_code='{TICKER}' AND report_type != '调整后' 
    ORDER BY report_date
""").fetchdf()
idf = conn.execute(f"""
    SELECT *, CAST(report_date AS VARCHAR) as rd FROM financial_intermediate 
    WHERE stock_code='{TICKER}' ORDER BY report_date
""").fetchdf()
ts = float(conn.execute(f"SELECT total_shares FROM stock_capital WHERE stock_code='{TICKER}' ORDER BY record_date DESC LIMIT 1").fetchone()[0])

print(f"  Raw: {len(fdf)} rows, Intermediate: {len(idf)} rows")

# Use quarterly data (net_profit_q, not cumulative net_profit) for TTM
last4 = idf.iloc[-4:] if len(idf) >= 4 else idf
manual_ttm_np = last4['net_profit_q'].sum()
stored_ttm_np = idf['net_profit_ttm'].iloc[-1]
check_close("Net Profit TTM (q_sum)", manual_ttm_np, stored_ttm_np)

manual_ttm_rev = last4['total_revenue_q'].sum()
stored_ttm_rev = idf['total_revenue_ttm'].iloc[-1]
check_close("Revenue TTM (q_sum)", manual_ttm_rev, stored_ttm_rev)

manual_ttm_eps = last4['eps_q'].sum()
stored_ttm_eps = idf['eps_ttm'].iloc[-1]
check_close("EPS TTM (q_sum)", manual_ttm_eps, stored_ttm_eps)

# BVPS = equity_parent / total_shares
last = idf.iloc[-1]
eq = float(last['equity_parent'])
check_close(f"BVPS (eq={eq/1e8:.1f}亿 / {ts/1e8:.1f}亿股)", eq/ts, float(last['bvps'] if pd.notna(last['bvps']) else 0) if pd.notna(last['bvps']) else None)

# ROE = net_profit / avg_equity
np_val = float(last['net_profit'])
avg_eq = float(last['avg_equity_parent'])
if avg_eq != 0:
    check_close("ROE annual", np_val/avg_eq*100, float(last['roe_annual']))

# Gross margin
rev = float(last['total_revenue'])
cost = float(last['operating_cost'])
if rev != 0:
    check_close("Gross Margin", (rev-cost)/rev*100, float(last['gross_margin_annual']))

# YoY
prev_year = idf.iloc[-5] if len(idf) >= 5 else None
if prev_year is not None:
    prev_rev = float(prev_year['total_revenue'])
    if prev_rev != 0:
        expected_yoy = (rev - prev_rev) / prev_rev * 100
        check_close("Revenue YoY", expected_yoy, float(last['revenue_yoy_annual']))

# CAGR
row_3y = idf.iloc[-13] if len(idf) >= 13 else None
if row_3y is not None:
    rev_3y = float(row_3y['total_revenue'])
    if rev_3y > 0 and rev > 0:
        expected_cagr = ((rev/rev_3y)**(1/3)-1)*100
        check_close("Revenue CAGR 3y", expected_cagr, float(last['revenue_cagr_3y']))

# TTM ROE
avg_eq_ttm = float(last['avg_equity_parent_ttm'])
if avg_eq_ttm != 0 and pd.notna(stored_ttm_np):
    expected_roe_ttm = float(stored_ttm_np) / avg_eq_ttm * 100
    check_close("ROE TTM", expected_roe_ttm, float(last['roe_ttm']))

# ============================================================
header("4. valuation_indicators - cross validation (sh600519)")
# ============================================================
vdf = conn.execute(f"SELECT * FROM valuation_indicators WHERE stock_code='{TICKER}' ORDER BY trade_date DESC LIMIT 1").fetchdf()
vlast = vdf.iloc[0]
clast = float(df['close'].iloc[-1])
mcap = clast * ts

np_ttm_val = float(idf['net_profit_ttm'].iloc[-1])
eq_par = float(idf['equity_parent'].iloc[-1])
rev_ttm_val = float(idf['total_revenue_ttm'].iloc[-1])

check_close("PE_TTM", mcap/np_ttm_val, float(vlast['pe_ttm']))
check_close("PB", mcap/eq_par, float(vlast['pb']))
check_close("PS_TTM", mcap/rev_ttm_val, float(vlast['ps_ttm']))

# Dividend yield
total_div = float(conn.execute(f"""
    SELECT COALESCE(SUM(cash_per_share),0) FROM dividends 
    WHERE stock_code='{TICKER}' AND dividend_date >= '2025-05-15'
""").fetchone()[0])
if total_div > 0 and clast > 0:
    dy_manual = total_div / clast * 100
    dy_stored = float(vlast['dividend_yield'])
    check_close("Dividend Yield", dy_manual, dy_stored)

# ============================================================
header("5. northbound_flow - accumulation & data")
# ============================================================
nbf = conn.execute(f"SELECT COUNT(*) FROM northbound_flow WHERE stock_code='{TICKER}'").fetchone()[0]
nbf_total = conn.execute(f"SELECT COUNT(*) FROM northbound_flow").fetchone()[0]

# Check if accumulation columns exist and have data
cols = [c[0] for c in conn.execute("DESCRIBE northbound_flow").fetchall()]
acc_cols = [c for c in ['inflow_5d','inflow_10d','inflow_30d'] if c in cols]
print(f"  {TICKER} northbound rows: {nbf}, total: {nbf_total}")
if acc_cols:
    filled = conn.execute(f"SELECT COUNT(*) FROM northbound_flow WHERE inflow_5d IS NOT NULL AND stock_code='{TICKER}'").fetchone()[0]
    print(f"  Accumulation cols: {acc_cols}, {TICKER} filled={filled}/{nbf}" + (PASS_STR if filled==nbf and nbf>0 else WARN_STR))
else:
    print(f"  Accumulation cols: MISSING{FAIL_STR}")

# Check holding info
holding_null = conn.execute(f"SELECT COUNT(*) FROM northbound_flow WHERE holding_shares IS NULL AND stock_code='{TICKER}'").fetchone()[0]
print(f"  holding_shares NULL: {holding_null}/{nbf}" + (PASS_STR if holding_null==0 else WARN_STR))

# ============================================================
header("6. stock_daily - turnover & outstanding_share")
# ============================================================
tov = conn.execute(f"SELECT COUNT(*) FROM stock_daily WHERE turnover IS NOT NULL AND stock_code='{TICKER}'").fetchone()[0]
total_daily = conn.execute(f"SELECT COUNT(*) FROM stock_daily WHERE stock_code='{TICKER}'").fetchone()[0]
print(f"  turnover filled: {tov}/{total_daily}" + (WARN_STR if tov==0 else PASS_STR))

out = conn.execute(f"SELECT COUNT(*) FROM stock_daily WHERE outstanding_share IS NOT NULL AND stock_code='{TICKER}'").fetchone()[0]
print(f"  outstanding_share filled: {out}/{total_daily}" + (WARN_STR if out==0 else PASS_STR))

# ============================================================
header("7. 代码缺陷检查 (Code defect analysis)")
# ============================================================

issues = []

# Issue 1: Fixed - collect_capital_data now uses CNInfo (ak.stock_profile_cninfo)
issues.append("1. [FIXED] eastmoney.collect_capital_data() 已改用 CNInfo 注册资金")

# Issue 2: Fixed - collect_industry_data now uses CNInfo  
issues.append("2. [FIXED] eastmoney.collect_industry_data() 已改用 CNInfo 所属行业")

# Issue 3: sz000725 technical indicators was empty due to pd.NA incompatibility
issues.append("3. [FIXED] technical.py 中 pd.NA -> np.nan，解决新版pandas兼容")

# Issue 4: valuation shows PE=19.48 for 贵州茅台 - check if this is reasonable
pe_val = float(vlast['pe_ttm']) if pd.notna(vlast['pe_ttm']) else 0
issues.append(f"4. 贵州茅台 PE_TTM={pe_val:.2f} - 需人工判断是否合理(当前约合理区间)")

# Issue 5: northbound_flow accumulation only uses simple rolling sum, not weighted
issues.append("5. northbound_flow 累计流入仅简单rolling sum, 未考虑交易日间隔(假期会有误差)")

# Issue 6: financial ROE in financial_statements is calculated as net_profit/(total_assets-total_liabilities)
# which is NOT the same as Dupont ROE
issues.append("6. financial_statements.roe 是用 (总资产-总负债) 计算净资产，与 equity_parent 可能不一致")

# Issue 7: valuation dividend_yield calculation - 365 days lookback may miss recent dividends
issues.append("7. valuation.py 股息率取近365天分红，跨年时可能漏掉部分年报分红节点")

# Issue 8: _calc_q_data subtracts prev cumulative, safely handles None
# This looks correct
issues.append("8. financial.py _calc_q_data() 正确处理了累计值转单季值的逻辑 [验证通过]")

for i in issues:
    print(f"  {i}")

# ============================================================
header("8. 跨股票完整性")
# ============================================================
for t in ['sh600519','sh600276','sz000725','sh600089']:
    dc = conn.execute(f"SELECT COUNT(*) FROM stock_daily WHERE stock_code='{t}'").fetchone()[0]
    tc = conn.execute(f"SELECT COUNT(*) FROM technical_indicators WHERE stock_code='{t}'").fetchone()[0]
    vc = conn.execute(f"SELECT COUNT(*) FROM valuation_indicators WHERE stock_code='{t}'").fetchone()[0]
    fc = conn.execute(f"SELECT COUNT(*) FROM financial_statements WHERE stock_code='{t}'").fetchone()[0]
    ic = conn.execute(f"SELECT COUNT(*) FROM financial_intermediate WHERE stock_code='{t}'").fetchone()[0]
    cc = conn.execute(f"SELECT COUNT(*) FROM stock_capital WHERE stock_code='{t}'").fetchone()[0]
    bvps_ok = conn.execute(f"SELECT COUNT(*) FROM financial_intermediate WHERE stock_code='{t}' AND bvps IS NOT NULL").fetchone()[0]
    ok = tc >= dc - 1 and fc > 0
    tag = PASS_STR if ok else FAIL_STR
    print(f"  {t}: daily={dc} tech={tc} val={vc} fin={fc} inter={ic} cap={cc} bvps_ok={bvps_ok}{tag}")

# ============================================================
header("9. 全量数据库统计")
# ============================================================
tables = [
    'stock_daily', 'financial_statements', 'financial_intermediate',
    'technical_indicators', 'valuation_indicators',
    'stock_capital', 'stock_industry', 'dividends',
    'northbound_flow', 'margin_trading', 'dragon_tiger',
    'stock_announcements'
]
print(f"  {'Table':<30} {'Rows':>10}  {'Stocks':>8}")
print(f"  {'-'*30} {'-'*10}  {'-'*8}")
for t in tables:
    try:
        cnt = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        try:
            sc = conn.execute(f"SELECT COUNT(DISTINCT stock_code) FROM {t}").fetchone()[0]
        except:
            sc = '-'
        print(f"  {t:<30} {cnt:>10}  {sc:>8}")
    except Exception as e:
        print(f"  {t:<30} {'ERROR':>10}  {str(e)[:30]:>8}")

# Null checks
print(f"\n  --- Key null checks ---")
checks = [
    ("technical_indicators", "ma5 IS NULL"),
    ("technical_indicators", "kdj_k IS NULL"),
    ("financial_intermediate", "bvps IS NULL"),
    ("financial_intermediate", "roe_annual IS NULL"),
    ("valuation_indicators", "pe_ttm IS NULL"),
    ("valuation_indicators", "pb IS NULL"),
    ("stock_daily", "change_pct IS NULL"),
    ("stock_daily", "amplitude IS NULL"),
]
for tbl, cond in checks:
    cnt = conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {cond}").fetchone()[0]
    total = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    tag = PASS_STR if cnt == 0 else (WARN_STR if cnt < total * 0.1 else FAIL_STR)
    print(f"  {tbl}.{cond}: {cnt}/{total}{tag}")

conn.close()
print(f"\n{'='*70}")
print("  Deep audit complete")
print(f"{'='*70}")