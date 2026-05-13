import akshare as ak

print("获取京东方资产负债表数据...")
df = ak.stock_financial_report_sina(stock="sz000725", symbol="资产负债表")

print("\n=== 所有列名 ===")
for col in df.columns:
    print(col)

print("\n=== 最新一条数据 ===")
print(df.iloc[0])

print("\n=== 查找包含'股东'或'权益'的列 ===")
for col in df.columns:
    if '股东' in col or '权益' in col:
        print(col)
