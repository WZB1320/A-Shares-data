import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stock_data_collector import StockDataCollector

def main():
    print("=" * 80)
    print("📊 重新计算股票日线指标")
    print("=" * 80)
    print()
    
    collector = StockDataCollector(db_path='stock_data.duckdb')
    
    # 重新计算所有股票指标
    collector.recalculate_all_daily_indicators()
    
    print()
    print("✅ 指标计算完成！")
    print()
    
    # 简单验证京东方数据
    print("📋 京东方示例数据（最近5条）：")
    print("-" * 80)
    
    result = collector.conn.execute("""
    SELECT trade_date, open, high, low, close, prev_close, 
           change_pct, amplitude, body_size, 
           upper_shadow, lower_shadow,
           high_20, low_20, high_60, low_60
    FROM stock_daily 
    WHERE stock_code = 'sz000725'
    ORDER BY trade_date DESC 
    LIMIT 5
    """).fetchdf()
    
    print(result.to_string(index=False))
    
    print()
    print("=" * 80)
    print("📝 指标说明：")
    print("=" * 80)
    print("  change_pct  : 涨跌幅 = (今收 - 昨收) / 昨收 * 100")
    print("  amplitude   : 振幅 = (最高 - 最低) / 昨收 * 100")
    print("  body_size   : 实体幅度 = | 收盘 - 开盘 |")
    print("  upper_shadow: 上影线 = 最高 - max(开盘, 收盘)")
    print("  lower_shadow: 下影线 = min(开盘, 收盘) - 最低")
    print("  high_20     : 20日最高价（滚动窗口，包含当日）")
    print("  low_20      : 20日最低价")
    print("  high_60     : 60日最高价")
    print("  low_60      : 60日最低价")

if __name__ == "__main__":
    main()
