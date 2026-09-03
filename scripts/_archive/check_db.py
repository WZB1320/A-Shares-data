"""检查数据库状态"""
import duckdb, os

db_path = 'data/stock_data.duckdb'
if not os.path.exists(db_path):
    print(f'WARNING: 数据库文件不存在: {db_path}')
    exit(1)

size_mb = os.path.getsize(db_path) / 1024 / 1024
print(f'数据库文件: {db_path} ({size_mb:.1f} MB)')

conn = duckdb.connect(db_path, read_only=True)
tables = conn.execute(
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema='main' AND table_type='BASE TABLE' ORDER BY table_name"
).fetchall()
print(f'表数量: {len(tables)}')
print()

total_rows = 0
for (t,) in tables:
    try:
        cnt = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        total_rows += cnt
        cols = conn.execute(
            f"SELECT COUNT(*) FROM information_schema.columns WHERE table_name='{t}'"
        ).fetchone()[0]

        # 查找日期字段
        date_col = conn.execute(f"""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name='{t}' 
              AND column_name IN ('trade_date','report_date','update_date',
                                  'announcement_date','dividend_date','record_date','start_date')
            LIMIT 1
        """).fetchone()

        date_info = ''
        if date_col and cnt > 0:
            dc = date_col[0]
            mn = conn.execute(f'SELECT MIN("{dc}") FROM "{t}"').fetchone()[0]
            mx = conn.execute(f'SELECT MAX("{dc}") FROM "{t}"').fetchone()[0]
            date_info = f' | {mn} ~ {mx}'

        stock_info = ''
        if cnt > 0:
            try:
                sc = conn.execute(f'SELECT COUNT(DISTINCT stock_code) FROM "{t}" WHERE stock_code IS NOT NULL').fetchone()[0]
                stock_info = f' | {sc} stocks'
            except:
                pass

        print(f'  {t:30s} | {cnt:>8,d} rows | {cols:>2} cols{date_info}{stock_info}')
    except Exception as e:
        print(f'  {t:30s} | ERROR: {e}')

print(f'\n总行数: {total_rows:,}')

# 检查最新数据日期
print('\n=== 最新数据日期 ===')
date_tables = ['stock_daily', 'technical_indicators', 'valuation_indicators',
               'northbound_flow', 'margin_trading', 'financial_statements',
               'financial_intermediate']
for t in date_tables:
    try:
        for dc in ['trade_date', 'report_date']:
            try:
                mx = conn.execute(f'SELECT MAX({dc}) FROM "{t}"').fetchone()[0]
                if mx:
                    print(f'  {t}: {mx}')
                    break
            except:
                continue
    except:
        pass

conn.close()
print('\n数据库检查完成')