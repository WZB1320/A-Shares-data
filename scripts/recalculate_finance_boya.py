import logging
import sys
from datetime import datetime
import duckdb
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def main():
    db_path = 'stock_data.duckdb'
    
    try:
        conn = duckdb.connect(db_path)
        print(f"成功连接数据库: {db_path}")
        
        # 先查询京东方 A 的财务报表数据
        print("\n=== 查询京东方 A (sz000725) 的财务数据 ===")
        df_finance = conn.execute("""
            SELECT report_date, report_type, total_revenue, net_profit, total_assets, total_liabilities, 
                   eps, equity_parent, announcement_date
            FROM financial_statements 
            WHERE stock_code = 'sz000725'
            ORDER BY report_date DESC
        """).fetchdf()
        print(df_finance.to_string())
        
        # 查询京东方 A 的总股本数据
        print("\n=== 查询京东方 A 的总股本数据 ===")
        df_cap = conn.execute("""
            SELECT * FROM stock_capital WHERE stock_code = 'sz000725' ORDER BY record_date DESC
        """).fetchdf()
        print(df_cap.to_string())
        
        conn.close()
        
        # 现在导入我们的模块并重新计算
        sys.path.insert(0, '.')
        from stock_data_collector import StockDataCollector
        
        collector = StockDataCollector(db_path)
        
        print("\n=== 重新计算京东方 A 的财务中间指标 ===")
        collector.calculate_financial_intermediate('sz000725')
        
        print("\n=== 重新计算京东方 A 的估值指标 ===")
        collector.calculate_valuation_indicators('sz000725')
        
        # 验证结果
        conn = duckdb.connect(db_path)
        
        print("\n=== 验证计算后的财务中间指标 ===")
        df_inter = conn.execute("""
            SELECT * FROM financial_intermediate 
            WHERE stock_code = 'sz000725'
            ORDER BY report_date DESC
            LIMIT 10
        """).fetchdf()
        print(df_inter.to_string())
        
        print("\n=== 验证 2026-03-04 的估值指标 ===")
        df_val = conn.execute("""
            SELECT * FROM valuation_indicators 
            WHERE stock_code = 'sz000725' AND trade_date = '2026-03-04'
        """).fetchdf()
        print(df_val.to_string())
        
        conn.close()
        
        print("\n=== 计算完成 ===")
        
    except Exception as e:
        print(f"错误: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
