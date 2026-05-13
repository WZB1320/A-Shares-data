import duckdb

conn = duckdb.connect('stock_data.duckdb')

def to_f(v):
    if v is None: return None
    try: return float(v)
    except: return None

print("=" * 70)
print("1. 各表数据量统计")
print("=" * 70)
tables = ['stock_daily', 'financial_statements', 'technical_indicators', 
          'valuation_indicators', 'financial_intermediate', 'stock_capital', 'dividends']
for t in tables:
    cnt = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    stocks = conn.execute(f"SELECT COUNT(DISTINCT stock_code) FROM {t}").fetchone()[0]
    print(f"  {t}: {cnt} 条, {stocks} 只股票")

print("\n" + "=" * 70)
print("2. 技术指标验证 (sh600519 贵州茅台)")
print("=" * 70)
df = conn.execute("""
    SELECT d.trade_date, d.close, t.ma5, t.ma10, t.ma20, t.ma60
    FROM stock_daily d
    JOIN technical_indicators t ON d.stock_code = t.stock_code AND d.trade_date = t.trade_date
    WHERE d.stock_code = 'sh600519'
    ORDER BY d.trade_date DESC LIMIT 5
""").fetchdf()
print(df.to_string(index=False))

df_close = conn.execute("""
    SELECT trade_date, close FROM stock_daily 
    WHERE stock_code = 'sh600519' ORDER BY trade_date DESC LIMIT 5
""").fetchdf()
ma5_calc = df_close['close'].mean()
print(f"\n  手动验证 MA5: 计算={ma5_calc:.2f}, 数据库={df.iloc[0]['ma5']}, "
      f"{'OK' if abs(ma5_calc - df.iloc[0]['ma5']) < 0.1 else 'MISMATCH!'}")

print("\n" + "=" * 70)
print("3. 估值指标验证 (sh600519 贵州茅台)")
print("=" * 70)

# 最新收盘价
close_price = to_f(conn.execute("""
    SELECT close FROM stock_daily WHERE stock_code = 'sh600519' ORDER BY trade_date DESC LIMIT 1
""").fetchone()[0])

# 估值指标
df_val = conn.execute("""
    SELECT trade_date, pe_ttm, pb, ps_ttm, dividend_yield, roe
    FROM valuation_indicators WHERE stock_code = 'sh600519' ORDER BY trade_date DESC LIMIT 1
""").fetchdf()
print(f"  最新收盘价: {close_price}")
print(f"  数据库估值: PE={df_val.iloc[0]['pe_ttm']}, PB={df_val.iloc[0]['pb']}, "
      f"PS={df_val.iloc[0]['ps_ttm']}, 股息率={df_val.iloc[0]['dividend_yield']}%, ROE={df_val.iloc[0]['roe']}%")

# 财务数据（累计值）
print("\n  最近5期财报（累计值）:")
r = conn.execute("""
    SELECT report_date, eps, revenue_per_share, net_profit, equity, bvps
    FROM financial_intermediate WHERE stock_code = 'sh600519' ORDER BY report_date DESC LIMIT 5
""").fetchall()
for x in r:
    print(f"    {x[0]}: EPS={x[1]}, 每股营收={x[2]}, 净利润={x[3]}, 权益={x[4]}, BVPS={x[5]}")

# 手动计算单季度 TTM
print("\n  手动计算单季度 EPS:")
eps_data = []
for x in r:
    eps_data.append({'date': str(x[0]), 'eps_cum': to_f(x[1]), 'rev_cum': to_f(x[2])})
eps_data.reverse()

for i, d in enumerate(eps_data):
    month = int(d['date'][5:7])
    if month == 3:
        d['eps_q'] = d['eps_cum']
        d['rev_q'] = d['rev_cum']
    else:
        prev = eps_data[i-1]
        d['eps_q'] = d['eps_cum'] - prev['eps_cum'] if d['eps_cum'] and prev['eps_cum'] else None
        d['rev_q'] = d['rev_cum'] - prev['rev_cum'] if d['rev_cum'] and prev['rev_cum'] else None
    print(f"    {d['date']}: 累计EPS={d['eps_cum']}, 单季EPS={d['eps_q']:.4f}" if d['eps_q'] else f"    {d['date']}: 累计EPS={d['eps_cum']}, 单季EPS=None")

# TTM = 最近4个单季度之和
eps_q_vals = [d['eps_q'] for d in eps_data if d['eps_q'] is not None]
eps_ttm_calc = sum(eps_q_vals[-4:]) if len(eps_q_vals) >= 4 else sum(eps_q_vals)
pe_calc = close_price / eps_ttm_calc if eps_ttm_calc and eps_ttm_calc > 0 else None
print(f"\n  TTM EPS = {eps_ttm_calc:.4f}" if eps_ttm_calc else "\n  TTM EPS = N/A")
print(f"  手动计算 PE TTM = {close_price} / {eps_ttm_calc:.4f} = {pe_calc:.4f}" if pe_calc else "  PE TTM: N/A")
print(f"  数据库 PE TTM = {df_val.iloc[0]['pe_ttm']}")
if pe_calc and df_val.iloc[0]['pe_ttm']:
    match = abs(pe_calc - df_val.iloc[0]['pe_ttm']) < 0.1
    print(f"  匹配: {'OK' if match else 'MISMATCH!'}")

# 手动计算 PB
bvps_val = to_f(r[0][5])
if bvps_val and bvps_val > 0:
    pb_calc = close_price / bvps_val
    print(f"\n  手动计算 PB = {close_price} / {bvps_val:.4f} = {pb_calc:.4f}")
    print(f"  数据库 PB = {df_val.iloc[0]['pb']}")
    if df_val.iloc[0]['pb']:
        print(f"  匹配: {'OK' if abs(pb_calc - df_val.iloc[0]['pb']) < 0.1 else 'MISMATCH!'}")
    else:
        print(f"  数据库 PB 为 None (缺少总股本)")

# 手动计算 ROE
net_profit_val = to_f(r[0][3])
equity_val = to_f(r[0][4])
if net_profit_val and equity_val and equity_val > 0:
    roe_calc = net_profit_val / equity_val * 100
    print(f"\n  手动计算 ROE = {net_profit_val:.2f} / {equity_val:.2f} * 100 = {roe_calc:.4f}%")
    print(f"  数据库 ROE = {df_val.iloc[0]['roe']}%")
    if df_val.iloc[0]['roe']:
        print(f"  匹配: {'OK' if abs(roe_calc - df_val.iloc[0]['roe']) < 0.1 else 'MISMATCH!'}")

# 股息率
div_result = to_f(conn.execute("""
    SELECT COALESCE(SUM(cash_per_share), 0) FROM dividends 
    WHERE stock_code = 'sh600519' AND dividend_date >= '2025-05-11'
""").fetchone()[0])
if div_result and div_result > 0:
    div_yield_calc = div_result / close_price * 100
    print(f"\n  手动计算 股息率 = {div_result:.4f} / {close_price} * 100 = {div_yield_calc:.4f}%")
    print(f"  数据库 股息率 = {df_val.iloc[0]['dividend_yield']}%")
    if df_val.iloc[0]['dividend_yield']:
        print(f"  匹配: {'OK' if abs(div_yield_calc - df_val.iloc[0]['dividend_yield']) < 0.01 else 'MISMATCH!'}")

print("\n" + "=" * 70)
print("4. 各股票估值指标覆盖率")
print("=" * 70)
r = conn.execute("""
    SELECT stock_code, COUNT(*) as cnt,
           COUNT(pe_ttm) as pe, COUNT(pb) as pb, 
           COUNT(ps_ttm) as ps, COUNT(dividend_yield) as div, COUNT(roe) as roe
    FROM valuation_indicators GROUP BY stock_code ORDER BY stock_code
""").fetchall()
for x in r:
    print(f"  {x[0]}: 总{x[1]} PE:{x[2]} PB:{x[3]} PS:{x[4]} 股息率:{x[5]} ROE:{x[6]}")

print("\n" + "=" * 70)
print("5. 总股本数据")
print("=" * 70)
r = conn.execute("SELECT stock_code, record_date, total_shares FROM stock_capital ORDER BY stock_code").fetchall()
for x in r:
    print(f"  {x[0]}: {to_f(x[2]):,.0f} 股 ({x[1]})")

print("\n" + "=" * 70)
print("6. 分红数据 (sh600519 最近3条)")
print("=" * 70)
df_div = conn.execute("""
    SELECT dividend_date, cash_per_share FROM dividends
    WHERE stock_code = 'sh600519' ORDER BY dividend_date DESC LIMIT 3
""").fetchdf()
print(df_div.to_string(index=False))

conn.close()