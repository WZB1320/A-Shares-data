-- ============================================================
-- P1-2 表结构变更: 增加流动比率/速动比率相关字段
--
-- 涉及表:
--   1. financial_statements      (原始数据表) - 增加流动资产/流动负债
--   2. financial_intermediate    (计算中间表) - 增加流动比率/速动比率
--
-- 兼容性说明:
--   - 使用 IF NOT EXISTS 保证可重复执行
--   - 新字段默认 NULL,不影响现有数据
--   - 不修改现有字段类型,无数据丢失风险
--   - DuckDB ADD COLUMN 只能加到表末尾,不影响查询
--
-- 执行方式:
--   python -c "import duckdb; c=duckdb.connect('data/stock_data.duckdb'); c.execute(open('scripts/migration_p1_2.sql').read()); print('done')"
-- ============================================================

-- ============================================================
-- 第一步: financial_statements 表增加流动资产/流动负债字段
-- ============================================================
-- 用途: 存储从 AKShare 资产负债表接口采集的原始数据
-- 数据源: stock_balance_sheet_by_report_em 的 TOTAL_CURRENT_ASSETS / TOTAL_CURRENT_LIAB
-- 类型选择: DECIMAL(18,2) 与同表 total_assets/total_liabilities 保持一致
-- 示例值: 002272 2026Q1 流动资产=2,261,500,140.09 流动负债=1,472,706,515.79

ALTER TABLE financial_statements ADD COLUMN IF NOT EXISTS current_assets DECIMAL(18,2);
ALTER TABLE financial_statements ADD COLUMN IF NOT EXISTS current_liabilities DECIMAL(18,2);

-- 注: inventory 字段已存在(DECIMAL(18,2)),速动比率计算公式:
--   速动比率 = (流动资产 - 存货) / 流动负债


-- ============================================================
-- 第二步: financial_intermediate 表增加流动比率/速动比率字段
-- ============================================================
-- 用途: 存储计算后的比率指标
-- 类型选择: DECIMAL(10,4) 与同表 roe_annual/gross_margin_annual 保持一致
-- 取值范围: DECIMAL(10,4) 最大 999999.9999,流动比率通常 0.5~10,足够
-- 计算公式:
--   流动比率 = 流动资产 / 流动负债
--   速动比率 = (流动资产 - 存货) / 流动负债

ALTER TABLE financial_intermediate ADD COLUMN IF NOT EXISTS current_ratio DECIMAL(10,4);
ALTER TABLE financial_intermediate ADD COLUMN IF NOT EXISTS quick_ratio DECIMAL(10,4);


-- ============================================================
-- 验证语句(执行后检查字段是否添加成功)
-- ============================================================
-- 预期: financial_statements 末尾出现 current_assets, current_liabilities
-- 预期: financial_intermediate 末尾出现 current_ratio, quick_ratio

-- SELECT column_name, data_type FROM information_schema.columns
--   WHERE table_name = 'financial_statements' AND column_name IN ('current_assets','current_liabilities');
--
-- SELECT column_name, data_type FROM information_schema.columns
--   WHERE table_name = 'financial_intermediate' AND column_name IN ('current_ratio','quick_ratio');


-- ============================================================
-- 回滚语句(如需撤销变更)
-- ============================================================
-- ALTER TABLE financial_statements DROP COLUMN current_assets;
-- ALTER TABLE financial_statements DROP COLUMN current_liabilities;
-- ALTER TABLE financial_intermediate DROP COLUMN current_ratio;
-- ALTER TABLE financial_intermediate DROP COLUMN quick_ratio;


-- ============================================================
-- 数据影响评估
-- ============================================================
-- 1. 现有数据: 新字段全部为 NULL,不影响现有查询和 API 返回
-- 2. API 兼容: /api/indicators/financial/ 返回的 JSON 会多出 4 个字段(值为 null),
--              调用方使用 .get() 取值不受影响
-- 3. 采集兼容: 现有 _upsert_financial 的 INSERT 语句未列出新字段,
--              新字段自动为 NULL,不会报错
-- 4. 计算兼容: financial.py 的 SELECT 语句未包含新字段,
--              计算结果中 current_ratio/quick_ratio 为 NULL
--              需要后续修改采集器和计算器才能填充数据
