import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stock_data_collector import StockDataCollector

def main():
    print("=" * 80)
    print("📊 重新计算所有股票技术指标")
    print("=" * 80)
    print()
    
    collector = StockDataCollector(db_path='stock_data.duckdb')
    
    # 重新计算所有股票技术指标
    collector.recalculate_all_technical_indicators()
    
    print()
    print("✅ 技术指标计算完成！")
    print()
    
    # 简单验证京东方数据
    print("📋 京东方示例数据（最近10条，部分指标）：")
    print("-" * 80)
    
    result = collector.conn.execute("""
    SELECT trade_date, close,
           ma5, ma10, ma20,
           ema12, ema26,
           macd_dif, macd_dea, macd_hist,
           rsi6, rsi12,
           kdj_k, kdj_d, kdj_j,
           cci20, wr14,
           atr14,
           mfi14
    FROM technical_indicators
    WHERE stock_code = 'sz000725'
    ORDER BY trade_date DESC
    LIMIT 10
    """).fetchdf()
    
    print(result.to_string(index=False))
    print()
    
    print("=" * 80)
    print("📊 技术指标完整列表：")
    print("=" * 80)
    print("1. 均线类：MA5, MA10, MA20, MA60")
    print("2. 趋势类：EMA12, EMA26, MACD(DIF/DEA/Hist), BOLL(上/中/下轨), BIAS(5/10/20/60)")
    print("3. 震荡类：RSI(6/12/24), KDJ(K/D/J), CCI20, WR14")
    print("4. 波动率类：ATR14, STD20")
    print("5. 量价类：VOL-MA5/MA10, OBV, MFI14, VR")
    print("6. 进阶：DMI(+DI/-DI/ADX/ADXR), SAR, WVAD")

if __name__ == "__main__":
    main()
