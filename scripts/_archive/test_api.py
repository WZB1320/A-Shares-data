import akshare as ak

# Test with exact same params as the code
code = "600519"
start_fmt = "2016-05-10"
end_fmt = "2026-05-11"

print(f"Testing with code={code}, begin={start_fmt}, end={end_fmt}")
try:
    df = ak.stock_individual_notice_report(
        security=code,
        begin_date=start_fmt,
        end_date=end_fmt
    )
    print(f"Columns: {list(df.columns)}")
    print(f"Shape: {df.shape}")
    
    # Test the rename
    df = df.rename(columns={
        "公告标题": "title",
        "公告类型": "announcement_type",
        "公告日期": "announcement_date",
        "网址": "pdf_url"
    })
    df["stock_code"] = "sh600519"
    print("Rename and assign OK")
    print(f"Columns after: {list(df.columns)}")
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

# Also test with a shenzhen stock
print("\n--- Testing sz002843 ---")
try:
    df2 = ak.stock_individual_notice_report(
        security="002843",
        begin_date="2016-05-10",
        end_date="2026-05-11"
    )
    print(f"Columns: {list(df2.columns)}")
    print(f"Shape: {df2.shape}")
except Exception as e:
    print(f"Error: {e}")