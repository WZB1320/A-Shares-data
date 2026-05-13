import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stock_data_collector import StockDataCollector

def print_section(title):
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)

def main():
    print_section("交易数据测试脚本")
    
    collector = StockDataCollector(DB_PATH='stock_data.duckdb')
    
    test_stock = 'sz000725'
    
    try:
        print_section(f"1. 采集 {test_stock} 交易数据")
        
        # 采集北向资金
        print(f"\n采集 {test_stock} 北向资金...")
        collector.collect_northbound_flow(test_stock)
        
        # 采集融资融券
        print(f"\n采集 {test_stock} 融资融券...")
        collector.collect_margin_trading(test_stock)
        
        # 采集龙虎榜
        print(f"\n采集 {test_stock} 龙虎榜...")
        collector.collect_dragon_tiger(test_stock)
        
        # 计算北向资金累计
        print(f"\n计算 {test_stock} 北向资金累计流入...")
        collector.calculate_northbound_accumulation(test_stock)
        
        print_section(f"2. 查询 {test_stock} 北向资金数据")
        
        # 查询北向资金
        result = collector.conn.execute("""
        SELECT trade_date, close, net_inflow, holding_shares, holding_value, holding_ratio,
               inflow_5d, inflow_10d, inflow_30d
        FROM northbound_flow
        WHERE stock_code = ?
        ORDER BY trade_date DESC
        LIMIT 20
        """, (test_stock,)).fetchdf()
        
        if not result.empty:
            print(result.to_string(index=False))
        else:
            print("暂无北向资金数据")
        
        print_section(f"3. 查询 {test_stock} 融资融券数据")
        
        # 查询融资融券
        result = collector.conn.execute("""
        SELECT trade_date, rz_balance, rz_change, rz_change_pct,
               rq_balance, rq_change, rq_change_pct,
               total_balance, total_change, total_change_pct
        FROM margin_trading
        WHERE stock_code = ?
        ORDER BY trade_date DESC
        LIMIT 20
        """, (test_stock,)).fetchdf()
        
        if not result.empty:
            print(result.to_string(index=False))
        else:
            print("暂无融资融券数据")
        
        print_section(f"4. 查询 {test_stock} 龙虎榜数据")
        
        # 查询龙虎榜
        result = collector.conn.execute("""
        SELECT dt.trade_date, dt.list_type, dt.reason, dt.buy_amount, dt.sell_amount, dt.net_amount
        FROM dragon_tiger dt
        WHERE dt.stock_code = ?
        ORDER BY dt.trade_date DESC
        LIMIT 20
        """, (test_stock,)).fetchdf()
        
        if not result.empty:
            print(result.to_string(index=False))
        else:
            print("暂无龙虎榜数据")
        
        print_section(f"5. 查询表结构和元数据")
        
        print("\n--- northbound_flow 表结构 ---")
        cols = collector.conn.execute("PRAGMA table_info(northbound_flow)").fetchall()
        for col in cols:
            print(f"  {col[1]} ({col[2]})")
        
        print("\n--- margin_trading 表结构 ---")
        cols = collector.conn.execute("PRAGMA table_info(margin_trading)").fetchall()
        for col in cols:
            print(f"  {col[1]} ({col[2]})")
        
        print("\n--- dragon_tiger 表结构 ---")
        cols = collector.conn.execute("PRAGMA table_info(dragon_tiger)").fetchall()
        for col in cols:
            print(f"  {col[1]} ({col[2]})")
        
        print_section(f"6. 查询字段元数据")
        
        result = collector.conn.execute("""
        SELECT table_name, column_name, description, unit
        FROM column_metadata
        WHERE table_name IN ('northbound_flow', 'margin_trading', 'dragon_tiger', 'dragon_tiger_detail')
        ORDER BY table_name, column_name
        """).fetchdf()
        
        if not result.empty:
            print(result.to_string(index=False))
        
        print_section(f"7. 常用查询示例")
        
        print("\n--- 北向资金持续流入的日期 ---")
        result = collector.conn.execute("""
        SELECT trade_date, close, net_inflow, inflow_5d, holding_ratio
        FROM northbound_flow
        WHERE stock_code = ? AND net_inflow > 0
        ORDER BY net_inflow DESC
        LIMIT 10
        """, (test_stock,)).fetchdf()
        
        if not result.empty:
            print(result.to_string(index=False))
        else:
            print("暂无数据")
        
        print("\n--- 融资余额大幅增加的日期 ---")
        result = collector.conn.execute("""
        SELECT trade_date, rz_balance, rz_change, rz_change_pct
        FROM margin_trading
        WHERE stock_code = ? AND rz_change_pct > 5
        ORDER BY rz_change_pct DESC
        LIMIT 10
        """, (test_stock,)).fetchdf()
        
        if not result.empty:
            print(result.to_string(index=False))
        else:
            print("暂无数据")
        
        print_section("测试完成")
        
    except Exception as e:
        print(f"测试失败：{e}")
        import traceback
        traceback.print_exc()
    finally:
        collector.close()

if __name__ == "__main__":
    main()
