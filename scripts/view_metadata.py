import duckdb

conn = duckdb.connect('stock_data.duckdb')

# 初始化元数据（通过实例化 StockDataCollector）
from stock_data_collector import StockDataCollector
collector = StockDataCollector(db_path='stock_data.duckdb')

print("=" * 80)
print("📋 数据字典 - 字段说明和单位")
print("=" * 80)
print()

# 查询所有元数据
result = conn.execute("""
SELECT table_name, column_name, description, unit 
FROM column_metadata 
ORDER BY table_name, column_name
""").fetchall()

current_table = None

for table_name, column_name, description, unit in result:
    if current_table != table_name:
        if current_table is not None:
            print()
        print(f"## {table_name}")
        print("-" * 80)
        current_table = table_name
    
    unit_str = f" ({unit})" if unit else ""
    print(f"  {column_name:<30} {description:<40} {unit_str}")

conn.close()
