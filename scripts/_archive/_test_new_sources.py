"""测试 mootdx 和 baostock 连通性"""
import sys

print("=== 测试 mootdx ===")
try:
    from mootdx.quotes import Quotes
    client = Quotes.factory(market='std', bestip=True, timeout=10)
    # 获取茅台日线
    df = client.bars(symbol='600519', frequency=4, start=0, offset=10)
    print(f"成功! 行数: {len(df)}")
    print(f"列名: {list(df.columns)}")
    if len(df) > 0:
        print(df.tail(3).to_string())
    client.close()
except Exception as e:
    print(f"失败: {e}")

print()
print("=== 测试 baostock ===")
try:
    import baostock as bs
    lg = bs.login()
    print(f"登录: {lg.error_msg}")
    rs = bs.query_history_k_data_plus(
        code="sh.600519",
        fields="date,open,high,low,close,volume,amount,turn,pctChg",
        start_date="2025-05-15",
        end_date="2025-05-23",
        frequency="d",
        adjustflag="2"
    )
    data = []
    while rs.error_code == '0' and rs.next():
        data.append(rs.get_row_data())
    import pandas as pd
    df = pd.DataFrame(data, columns=rs.fields)
    print(f"成功! 行数: {len(df)}")
    print(df.to_string())
    bs.logout()
except Exception as e:
    print(f"失败: {e}")

print()
print("=== 测试 baostock 财务数据 ===")
try:
    import baostock as bs
    lg = bs.login()
    # 盈利能力
    rs = bs.query_profit_data(code="sh.600519", year=2025, quarter=4)
    data = []
    while rs.error_code == '0' and rs.next():
        data.append(rs.get_row_data())
    df = pd.DataFrame(data, columns=rs.fields)
    print(f"盈利能力: {len(df)} 行")
    if len(df) > 0:
        print(df.to_string())

    # 资产负债
    rs = bs.query_balance_data(code="sh.600519", year=2025, quarter=4)
    data = []
    while rs.error_code == '0' and rs.next():
        data.append(rs.get_row_data())
    df = pd.DataFrame(data, columns=rs.fields)
    print(f"\n资产负债: {len(df)} 行")
    if len(df) > 0:
        print(df.to_string())

    # 现金流
    rs = bs.query_cash_flow_data(code="sh.600519", year=2025, quarter=4)
    data = []
    while rs.error_code == '0' and rs.next():
        data.append(rs.get_row_data())
    df = pd.DataFrame(data, columns=rs.fields)
    print(f"\n现金流: {len(df)} 行")
    if len(df) > 0:
        print(df.to_string())

    bs.logout()
except Exception as e:
    print(f"失败: {e}")

print()
print("=== 测试 baostock 估值指标 ===")
try:
    import baostock as bs
    lg = bs.login()
    rs = bs.query_history_k_data_plus(
        code="sh.600519",
        fields="date,peTTM,pbMRQ,psTTM",
        start_date="2025-05-19",
        end_date="2025-05-23",
        frequency="d"
    )
    data = []
    while rs.error_code == '0' and rs.next():
        data.append(rs.get_row_data())
    df = pd.DataFrame(data, columns=rs.fields)
    print(f"估值数据: {len(df)} 行")
    print(df.to_string())
    bs.logout()
except Exception as e:
    print(f"失败: {e}")
