"""
初始化 stock_master 主数据表

手工维护,一次性执行:
    python scripts/init_master.py

该脚本会:
    1. 创建 stock_master 表(如不存在)
    2. 插入所有已采集股票的基础信息
    3. 打印对比: stock_daily 中有但 stock_master 中没有的股票

注意: 此脚本中的股票信息需手工确认和更新。
"""
import duckdb
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DB_PATH = Path(__file__).parent.parent / "data" / "stock_data.duckdb"


# ============================================================
# 手工维护的股票主数据
# 格式: (stock_code, stock_name, stock_name_cn, market, board,
#        listing_date, status, is_etf)
# 日期格式: YYYY-MM-DD
# ============================================================
STOCKS = [
    # -- 主板 --
    ("sh600089", "特变电工", "特变电工股份有限公司", "SH", "主板",
     "1997-06-18", "正常", False),
    ("sh600346", "恒力石化", "恒力石化股份有限公司", "SH", "主板",
     "2001-06-28", "正常", False),
    ("sh600585", "海螺水泥", "安徽海螺水泥股份有限公司", "SH", "主板",
     "2002-02-07", "正常", False),
    ("sh600900", "长江电力", "中国长江电力股份有限公司", "SH", "主板",
     "2003-11-18", "正常", False),
    ("sh601318", "中国平安", "中国平安保险(集团)股份有限公司", "SH", "主板",
     "2007-03-01", "正常", False),
    ("sh601857", "中国石油", "中国石油天然气股份有限公司", "SH", "主板",
     "2007-11-05", "正常", False),
    ("sz000651", "格力电器", "珠海格力电器股份有限公司", "SZ", "主板",
     "1996-11-18", "正常", False),
    ("sz000858", "五粮液", "宜宾五粮液股份有限公司", "SZ", "主板",
     "1998-04-27", "正常", False),
    ("sz002415", "海康威视", "杭州海康威视数字技术股份有限公司", "SZ", "主板",
     "2010-05-28", "正常", False),
    ("sz002594", "比亚迪", "比亚迪股份有限公司", "SZ", "主板",
     "2011-06-30", "正常", False),
    ("sz300750", "宁德时代", "宁德时代新能源科技股份有限公司", "SZ", "创业板",
     "2018-06-11", "正常", False),
    # -- ETF --
    ("sh513700", "香港医药ETF", "香港医药ETF", "SH", "ETF",
     None, "正常", True),
    # -- 补充的股票 (上市日期待确认) --
    ("sh600276", "恒瑞医药", "江苏恒瑞医药股份有限公司", "SH", "主板",
     "2000-10-18", "正常", False),
    ("sh600309", "万华化学", "万华化学集团股份有限公司", "SH", "主板",
     "2001-01-05", "正常", False),
    ("sh600519", "贵州茅台", "贵州茅台酒股份有限公司", "SH", "主板",
     "2001-08-27", "正常", False),
    ("sh600887", "伊利股份", "内蒙古伊利实业集团股份有限公司", "SH", "主板",
     "1996-03-12", "正常", False),
    ("sz000725", "京东方A", "京东方科技集团股份有限公司", "SZ", "主板",
     "2001-01-12", "正常", False),
    ("sz002272", "川润股份", "四川川润股份有限公司", "SZ", "主板",
     "2008-09-19", "正常", False),
    ("sz002648", "卫星化学", "卫星化学股份有限公司", "SZ", "主板",
     "2011-12-28", "正常", False),
    ("sz002714", "牧原股份", "牧原食品股份有限公司", "SZ", "主板",
     "2014-01-28", "正常", False),
    ("sz002843", "华懋新材", "华懋(厦门)新材料科技股份有限公司", "SZ", "主板",
     "2017-05-16", "正常", False),
    # -- 2026-09-29 补齐: 有日线数据但漏登记主数据的股票 --
    ("sh600552", "凯盛科技", "凯盛科技股份有限公司", "SH", "主板",
     "2002-11-08", "正常", False),
]


def main():
    conn = duckdb.connect(str(DB_PATH))

    # 确保表存在
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_master (
            stock_code VARCHAR(10) PRIMARY KEY,
            stock_name VARCHAR(50) NOT NULL,
            stock_name_cn VARCHAR(100),
            market VARCHAR(10),
            board VARCHAR(20),
            listing_date DATE,
            delisting_date DATE,
            status VARCHAR(10) DEFAULT '正常',
            is_etf BOOLEAN DEFAULT FALSE,
            is_index BOOLEAN DEFAULT FALSE,
            added_date DATE DEFAULT CURRENT_DATE,
            notes VARCHAR(255)
        )
    """)

    # 插入或更新
    # 注意: 用 ON CONFLICT DO UPDATE 而非 INSERT OR REPLACE —— 后者会删除整行再插入,
    # 导致 added_date 被重置为 CURRENT_DATE, 丢失首次登记时间。
    inserted = 0
    for row in STOCKS:
        conn.execute("""
            INSERT INTO stock_master
            (stock_code, stock_name, stock_name_cn, market, board,
             listing_date, status, is_etf)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (stock_code) DO UPDATE SET
                stock_name    = EXCLUDED.stock_name,
                stock_name_cn = EXCLUDED.stock_name_cn,
                market        = EXCLUDED.market,
                board         = EXCLUDED.board,
                listing_date  = EXCLUDED.listing_date,
                status        = EXCLUDED.status,
                is_etf        = EXCLUDED.is_etf
        """, row)
        inserted += 1

    print(f"已录入 {inserted} 只股票")

    # 检查 stock_daily 中有但 stock_master 中没有的
    missing = conn.execute("""
        SELECT DISTINCT d.stock_code
        FROM stock_daily d
        LEFT JOIN stock_master m ON d.stock_code = m.stock_code
        WHERE m.stock_code IS NULL
        ORDER BY d.stock_code
    """).fetchall()

    if missing:
        print(f"\n警告: stock_daily 中有 {len(missing)} 只股票未录入主数据表:")
        for (code,) in missing:
            print(f"  - {code}")
        print("请手动补充这些股票的信息到 STOCKS 列表后重新运行此脚本。")
    else:
        print("所有 stock_daily 中的股票已全部录入主数据表。")

    # 打印汇总
    print(f"\n{'code':<12} {'name':<10} {'market':<6} {'board':<6} {'listing':<12} {'status':<6}")
    print("-" * 55)
    for row in conn.execute("SELECT * FROM stock_master ORDER BY stock_code").fetchall():
        print(f"{row[0]:<12} {row[1]:<10} {row[3] or '-':<6} {row[4] or '-':<6} {str(row[5]) or '-':<12} {row[7] or '-':<6}")

    conn.close()


if __name__ == "__main__":
    main()