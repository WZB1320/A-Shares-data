"""测试BaoStock各API的正确用法"""
import baostock as bs
import pandas as pd

lg = bs.login()
print(f"登录: {lg.error_msg}")

# 1. 盈利能力
print("\n=== 盈利能力 ===")
rs = bs.query_profit_data(code='sh.600519', year=2025, quarter=4)
data = []
while rs.error_code == '0' and rs.next():
    data.append(rs.get_row_data())
df = pd.DataFrame(data, columns=rs.fields)
print(f"行数: {len(df)}")
if len(df) > 0:
    print(f"列: {list(df.columns)}")
    print(df.iloc[0].to_string())

# 2. 分红数据
print("\n=== 分红数据 ===")
rs = bs.query_dividend_data(code='sh.600519', year='2024', yearType='report')
data = []
while rs.error_code == '0' and rs.next():
    data.append(rs.get_row_data())
df = pd.DataFrame(data, columns=rs.fields)
print(f"行数: {len(df)}")
if len(df) > 0:
    print(f"列: {list(df.columns)}")
    print(df.to_string())

# 3. 行业分类 - 检查正确API
print("\n=== 行业分类 ===")
try:
    rs = bs.query_stock_industry()
    print(f"error: {rs.error_code} {rs.error_msg}")
    data = []
    while rs.error_code == '0' and rs.next():
        data.append(rs.get_row_data())
    df = pd.DataFrame(data, columns=rs.fields)
    print(f"行数: {len(df)}")
    if len(df) > 0:
        matched = df[df['code'] == 'sh.600519']
        print(f"茅台匹配: {len(matched)} 行")
        if len(matched) > 0:
            print(matched.iloc[0].to_string())
except Exception as e:
    print(f"失败: {e}")

# 4. 融资融券 - 检查正确API
print("\n=== 融资融券 ===")
try:
    rs = bs.query_margin_data(code='sh.600519', start_date='2025-05-01', end_date='2025-05-23')
    print(f"error: {rs.error_code} {rs.error_msg}")
except AttributeError:
    print("query_margin_data 不存在, 尝试其他API...")
    # 检查可用的margin相关API
    margin_apis = [x for x in dir(bs) if 'margin' in x.lower()]
    print(f"可用margin API: {margin_apis}")

# 5. 检查所有query开头的API
print("\n=== 所有query API ===")
query_apis = [x for x in dir(bs) if x.startswith('query')]
print(query_apis)

# 6. 资产负债
print("\n=== 资产负债 ===")
rs = bs.query_balance_data(code='sh.600519', year=2025, quarter=4)
data = []
while rs.error_code == '0' and rs.next():
    data.append(rs.get_row_data())
df = pd.DataFrame(data, columns=rs.fields)
print(f"行数: {len(df)}")
if len(df) > 0:
    print(f"列: {list(df.columns)}")
    print(df.iloc[0].to_string())

# 7. 现金流
print("\n=== 现金流 ===")
rs = bs.query_cash_flow_data(code='sh.600519', year=2025, quarter=4)
data = []
while rs.error_code == '0' and rs.next():
    data.append(rs.get_row_data())
df = pd.DataFrame(data, columns=rs.fields)
print(f"行数: {len(df)}")
if len(df) > 0:
    print(f"列: {list(df.columns)}")
    print(df.iloc[0].to_string())

bs.logout()
