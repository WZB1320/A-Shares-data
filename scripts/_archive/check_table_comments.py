import duckdb

conn = duckdb.connect('stock_data.duckdb')

print("=== 数据库中所有表 ===\n")
tables = conn.execute("SHOW TABLES").fetchall()

for (table_name,) in tables:
    print(f"📋 {table_name}")
    try:
        # 查看表结构
        result = conn.execute(f"DESCRIBE {table_name}").fetchall()
        for col in result:
            col_name, col_type = col[0], col[1]
            print(f"  • {col_name} ({col_type})")
    except Exception as e:
        print(f"  无法获取表结构: {e}")
    print()

conn.close()
