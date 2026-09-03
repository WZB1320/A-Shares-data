import akshare as ak
import duckdb
import pandas as pd
from datetime import datetime, timedelta
import logging
import os
from typing import List, Dict

# ====================== 配置区域（您只需要修改这里）======================
# 您需要采集的股票代码列表（格式：sh600000, sz000001）
STOCK_CODES = [
    "sh600519",  # 贵州茅台
    "sz002843",  # 泰嘉股份
    "sz002272",  # 川润股份
    "sh600346",  # 恒力石化
    "sh600309",  # 万华化学
    "sh600887",  # 伊利股份
    "sh600276",  # 恒瑞股份
    "sz002714",  # 牧原股份
    "sz000725",  # 京东方A
    "sh600089",  # 东方电气
    "sz002648",  # 卫星化学
    "sh513700",  # 港股通医药ETF
    # 在此添加您需要的其他股票代码
]

# 数据起始日期（十年前）
START_DATE = "20160510"

# 数据库文件路径（会自动创建）
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "stock_data.duckdb")

# 日志文件路径
LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "stock_collector.log")
# ======================================================================

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class StockDataCollector:
    def __init__(self, db_path: str):
        """初始化数据库连接"""
        self.conn = duckdb.connect(db_path)
        self._create_tables()
        
    def _create_tables(self):
        """创建数据库表结构"""
        # 股票日线表（包含复权因子）
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_daily (
            stock_code VARCHAR(10) NOT NULL,
            trade_date DATE NOT NULL,
            open DECIMAL(10,2),
            high DECIMAL(10,2),
            low DECIMAL(10,2),
            close DECIMAL(10,2),
            volume BIGINT,
            amount DECIMAL(18,2),
            adjust_factor DECIMAL(10,6),
            prev_close DECIMAL(10,2),
            change_pct DECIMAL(10,4),
            amplitude DECIMAL(10,4),
            body_size DECIMAL(10,4),
            upper_shadow DECIMAL(10,4),
            lower_shadow DECIMAL(10,4),
            high_20 DECIMAL(10,2),
            low_20 DECIMAL(10,2),
            high_60 DECIMAL(10,2),
            low_60 DECIMAL(10,2),
            PRIMARY KEY (stock_code, trade_date)
        )
        """)
        
        # 给现有表增加新字段（如果不存在）
        new_columns = [
            ('prev_close', 'DECIMAL(10,2)'),
            ('change_pct', 'DECIMAL(10,4)'),
            ('amplitude', 'DECIMAL(10,4)'),
            ('body_size', 'DECIMAL(10,4)'),
            ('upper_shadow', 'DECIMAL(10,4)'),
            ('lower_shadow', 'DECIMAL(10,4)'),
            ('high_20', 'DECIMAL(10,2)'),
            ('low_20', 'DECIMAL(10,2)'),
            ('high_60', 'DECIMAL(10,2)'),
            ('low_60', 'DECIMAL(10,2)'),
        ]
        
        for col_name, col_type in new_columns:
            try:
                self.conn.execute(f"ALTER TABLE stock_daily ADD COLUMN IF NOT EXISTS {col_name} {col_type}")
            except Exception as e:
                logger.debug(f"stock_daily 表可能已有 {col_name} 字段，跳过")
        
        # 财务报表表
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS financial_statements (
            stock_code VARCHAR(10) NOT NULL,
            report_date DATE NOT NULL,
            report_type VARCHAR(20) NOT NULL,
            total_revenue DECIMAL(18,2),
            net_profit DECIMAL(18,2),
            total_assets DECIMAL(18,2),
            total_liabilities DECIMAL(18,2),
            operating_cash_flow DECIMAL(18,2),
            eps DECIMAL(10,4),
            roe DECIMAL(10,4),
            equity_parent DECIMAL(18,2),
            announcement_date DATE,
            operating_cost DECIMAL(18,2),
            net_profit_deducted DECIMAL(18,2),
            inventory DECIMAL(18,2),
            accounts_receivable DECIMAL(18,2),
            accounts_payable DECIMAL(18,2),
            capex DECIMAL(18,2),
            interest_expense DECIMAL(18,2),
            PRIMARY KEY (stock_code, report_date, report_type)
        )
        """)
        
        # 给现有表增加 equity_parent 字段
        try:
            self.conn.execute("ALTER TABLE financial_statements ADD COLUMN IF NOT EXISTS equity_parent DECIMAL(18,2)")
        except Exception as e:
            logger.debug(f"financial_statements 表可能已有 equity_parent 字段，跳过")
        
        # 给现有表增加 announcement_date 字段
        try:
            self.conn.execute("ALTER TABLE financial_statements ADD COLUMN IF NOT EXISTS announcement_date DATE")
        except Exception as e:
            logger.debug(f"financial_statements 表可能已有 announcement_date 字段，跳过")
        
        # 添加新增的财务字段
        new_fin_columns = [
            ('operating_cost', 'DECIMAL(18,2)'),
            ('net_profit_deducted', 'DECIMAL(18,2)'),
            ('inventory', 'DECIMAL(18,2)'),
            ('accounts_receivable', 'DECIMAL(18,2)'),
            ('accounts_payable', 'DECIMAL(18,2)'),
            ('capex', 'DECIMAL(18,2)'),
            ('interest_expense', 'DECIMAL(18,2)'),
        ]
        
        for col_name, col_type in new_fin_columns:
            try:
                self.conn.execute(f"ALTER TABLE financial_statements ADD COLUMN IF NOT EXISTS {col_name} {col_type}")
            except Exception as e:
                logger.debug(f"financial_statements 表可能已有 {col_name} 字段，跳过")
        
        # 公告元数据表
        self.conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_announcements_id;
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_announcements_id'),
            stock_code VARCHAR(10) NOT NULL,
            announcement_date DATE NOT NULL,
            title VARCHAR(255),
            pdf_url VARCHAR(255),
            announcement_type VARCHAR(50),
            UNIQUE (stock_code, announcement_date, title)
        )
        """)
        
        # 数据更新记录表
        self.conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_update_log_id;
        CREATE TABLE IF NOT EXISTS update_log (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_update_log_id'),
            stock_code VARCHAR(10) NOT NULL,
            data_type VARCHAR(20) NOT NULL,
            last_update_date DATE NOT NULL,
            update_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (stock_code, data_type)
        )
        """)
        
        # 技术指标表
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS technical_indicators (
            stock_code VARCHAR(10) NOT NULL,
            trade_date DATE NOT NULL,
            -- 均线类
            ma5 DECIMAL(10,2),
            ma10 DECIMAL(10,2),
            ma20 DECIMAL(10,2),
            ma60 DECIMAL(10,2),
            -- 趋势类：EMA, BOLL, MACD, BIAS
            ema12 DECIMAL(10,4),
            ema26 DECIMAL(10,4),
            boll_mid DECIMAL(10,2),
            boll_upper DECIMAL(10,2),
            boll_lower DECIMAL(10,2),
            macd_dif DECIMAL(10,4),
            macd_dea DECIMAL(10,4),
            macd_hist DECIMAL(10,4),
            bias5 DECIMAL(10,4),
            bias10 DECIMAL(10,4),
            bias20 DECIMAL(10,4),
            bias60 DECIMAL(10,4),
            -- 震荡类：RSI, KDJ, CCI, WR
            rsi6 DECIMAL(10,4),
            rsi12 DECIMAL(10,4),
            rsi24 DECIMAL(10,4),
            kdj_k DECIMAL(10,4),
            kdj_d DECIMAL(10,4),
            kdj_j DECIMAL(10,4),
            cci20 DECIMAL(10,4),
            wr14 DECIMAL(10,4),
            -- 波动率类：ATR, 标准差
            atr14 DECIMAL(10,4),
            std20 DECIMAL(10,4),
            -- 量价类：VOL-MA, OBV, MFI, VR
            vol_ma5 DECIMAL(20,0),
            vol_ma10 DECIMAL(20,0),
            obv DECIMAL(20,0),
            mfi14 DECIMAL(10,4),
            vr DECIMAL(10,4),
            -- 进阶可选：DMI, SAR, WVAD
            dmi_pdi DECIMAL(10,4),
            dmi_mdi DECIMAL(10,4),
            dmi_adx DECIMAL(10,4),
            dmi_adxr DECIMAL(10,4),
            sar DECIMAL(10,2),
            wvad DECIMAL(10,4),
            PRIMARY KEY (stock_code, trade_date)
        )
        """)
        
        # 给现有表增加新字段（如果不存在）
        tech_new_columns = [
            ('ema12', 'DECIMAL(10,4)'),
            ('ema26', 'DECIMAL(10,4)'),
            ('boll_mid', 'DECIMAL(10,2)'),
            ('boll_upper', 'DECIMAL(10,2)'),
            ('boll_lower', 'DECIMAL(10,2)'),
            ('macd_dif', 'DECIMAL(10,4)'),
            ('macd_dea', 'DECIMAL(10,4)'),
            ('macd_hist', 'DECIMAL(10,4)'),
            ('bias5', 'DECIMAL(10,4)'),
            ('bias10', 'DECIMAL(10,4)'),
            ('bias20', 'DECIMAL(10,4)'),
            ('bias60', 'DECIMAL(10,4)'),
            ('rsi6', 'DECIMAL(10,4)'),
            ('rsi12', 'DECIMAL(10,4)'),
            ('rsi24', 'DECIMAL(10,4)'),
            ('kdj_k', 'DECIMAL(10,4)'),
            ('kdj_d', 'DECIMAL(10,4)'),
            ('kdj_j', 'DECIMAL(10,4)'),
            ('cci20', 'DECIMAL(10,4)'),
            ('wr14', 'DECIMAL(10,4)'),
            ('atr14', 'DECIMAL(10,4)'),
            ('std20', 'DECIMAL(10,4)'),
            ('vol_ma5', 'DECIMAL(20,0)'),
            ('vol_ma10', 'DECIMAL(20,0)'),
            ('obv', 'DECIMAL(20,0)'),
            ('mfi14', 'DECIMAL(10,4)'),
            ('vr', 'DECIMAL(10,4)'),
            ('dmi_pdi', 'DECIMAL(10,4)'),
            ('dmi_mdi', 'DECIMAL(10,4)'),
            ('dmi_adx', 'DECIMAL(10,4)'),
            ('dmi_adxr', 'DECIMAL(10,4)'),
            ('sar', 'DECIMAL(10,2)'),
            ('wvad', 'DECIMAL(10,4)'),
        ]
        
        for col_name, col_type in tech_new_columns:
            try:
                self.conn.execute(f"ALTER TABLE technical_indicators ADD COLUMN IF NOT EXISTS {col_name} {col_type}")
            except Exception as e:
                logger.debug(f"technical_indicators 表可能已有 {col_name} 字段，跳过")
        
        # 估值指标表
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS valuation_indicators (
            stock_code VARCHAR(10) NOT NULL,
            trade_date DATE NOT NULL,
            pe_ttm DECIMAL(10,4),
            pb DECIMAL(10,4),
            ps_ttm DECIMAL(10,4),
            dividend_yield DECIMAL(10,4),
            roe DECIMAL(10,4),
            pe_annual DECIMAL(10,4),
            ps_annual DECIMAL(10,4),
            roe_ttm DECIMAL(10,4),
            roe_annual DECIMAL(10,4),
            used_report_date DATE,
            used_announcement_date DATE,
            PRIMARY KEY (stock_code, trade_date)
        )
        """)
        
        # 给现有表增加新字段
        val_new_columns = [
            ('pe_annual', 'DECIMAL(10,4)'),
            ('ps_annual', 'DECIMAL(10,4)'),
            ('roe_ttm', 'DECIMAL(10,4)'),
            ('roe_annual', 'DECIMAL(10,4)'),
            ('used_report_date', 'DATE'),
            ('used_announcement_date', 'DATE'),
        ]
        
        for col_name, col_type in val_new_columns:
            try:
                self.conn.execute(f"ALTER TABLE valuation_indicators ADD COLUMN IF NOT EXISTS {col_name} {col_type}")
            except Exception as e:
                logger.debug(f"valuation_indicators 表可能已有 {col_name} 字段，跳过")
        
        # 总股本表
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_capital (
            stock_code VARCHAR(10) NOT NULL,
            record_date DATE NOT NULL,
            total_shares DECIMAL(20,0),
            PRIMARY KEY (stock_code, record_date)
        )
        """)
        
        # 分红表
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS dividends (
            stock_code VARCHAR(10) NOT NULL,
            dividend_date DATE NOT NULL,
            cash_per_share DECIMAL(10,4),
            announcement_date DATE,
            PRIMARY KEY (stock_code, dividend_date)
        )
        """)
        
        # 财务中间指标表
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS financial_intermediate (
            stock_code VARCHAR(10) NOT NULL,
            report_date DATE NOT NULL,
            report_type VARCHAR(20) NOT NULL,
            -- 原始财报数据（累计值）
            eps DECIMAL(10,4),
            bvps DECIMAL(10,4),
            revenue_per_share DECIMAL(10,4),
            net_profit DECIMAL(18,2),
            total_revenue DECIMAL(18,2),
            equity DECIMAL(18,2),
            equity_parent DECIMAL(18,2),
            total_assets DECIMAL(18,2),
            total_shares DECIMAL(20,0),
            announcement_date DATE,
            operating_cost DECIMAL(18,2),
            net_profit_deducted DECIMAL(18,2),
            inventory DECIMAL(18,2),
            accounts_receivable DECIMAL(18,2),
            accounts_payable DECIMAL(18,2),
            capex DECIMAL(18,2),
            interest_expense DECIMAL(18,2),
            operating_cash_flow DECIMAL(18,2),
            -- 单季度数据
            net_profit_q DECIMAL(18,2),
            total_revenue_q DECIMAL(18,2),
            eps_q DECIMAL(10,4),
            operating_cost_q DECIMAL(18,2),
            net_profit_deducted_q DECIMAL(18,2),
            operating_cash_flow_q DECIMAL(18,2),
            capex_q DECIMAL(18,2),
            -- TTM 滚动 12 个月数据
            net_profit_ttm DECIMAL(18,2),
            total_revenue_ttm DECIMAL(18,2),
            eps_ttm DECIMAL(10,4),
            operating_cost_ttm DECIMAL(18,2),
            net_profit_deducted_ttm DECIMAL(18,2),
            operating_cash_flow_ttm DECIMAL(18,2),
            capex_ttm DECIMAL(18,2),
            interest_expense_ttm DECIMAL(18,2),
            -- 平均资产类数据（期初+期末平均）
            avg_equity_parent DECIMAL(18,2),
            avg_total_assets DECIMAL(18,2),
            avg_inventory DECIMAL(18,2),
            avg_accounts_receivable DECIMAL(18,2),
            avg_accounts_payable DECIMAL(18,2),
            -- 平均资产类数据（TTM口径：当前+4季度前平均）
            avg_equity_parent_ttm DECIMAL(18,2),
            avg_total_assets_ttm DECIMAL(18,2),
            avg_inventory_ttm DECIMAL(18,2),
            avg_accounts_receivable_ttm DECIMAL(18,2),
            avg_accounts_payable_ttm DECIMAL(18,2),
            -- 盈利类指标（年度口径）
            roe_annual DECIMAL(10,4),
            roa_annual DECIMAL(10,4),
            gross_margin_annual DECIMAL(10,4),
            net_margin_parent_annual DECIMAL(10,4),
            net_margin_deducted_annual DECIMAL(10,4),
            -- 盈利类指标（TTM 口径）
            roe_ttm DECIMAL(10,4),
            roa_ttm DECIMAL(10,4),
            gross_margin_ttm DECIMAL(10,4),
            net_margin_parent_ttm DECIMAL(10,4),
            net_margin_deducted_ttm DECIMAL(10,4),
            -- 成长类指标（同比增速）
            revenue_yoy_annual DECIMAL(10,4),
            net_profit_yoy_annual DECIMAL(10,4),
            revenue_yoy_qoq DECIMAL(10,4),
            net_profit_yoy_qoq DECIMAL(10,4),
            revenue_yoy_ttm DECIMAL(10,4),
            net_profit_yoy_ttm DECIMAL(10,4),
            -- 成长类指标（3年CAGR）
            revenue_cagr_3y DECIMAL(10,4),
            net_profit_cagr_3y DECIMAL(10,4),
            net_profit_deducted_cagr_3y DECIMAL(10,4),
            -- 杜邦三层拆解（年度口径）
            dupont_net_margin_annual DECIMAL(10,4),
            dupont_asset_turnover_annual DECIMAL(10,4),
            dupont_equity_multiplier_annual DECIMAL(10,4),
            -- 杜邦三层拆解（TTM 口径）
            dupont_net_margin_ttm DECIMAL(10,4),
            dupont_asset_turnover_ttm DECIMAL(10,4),
            dupont_equity_multiplier_ttm DECIMAL(10,4),
            -- 营运能力（年度口径）
            inventory_turnover_annual DECIMAL(10,4),
            inventory_days_annual DECIMAL(10,4),
            accounts_receivable_turnover_annual DECIMAL(10,4),
            accounts_receivable_days_annual DECIMAL(10,4),
            accounts_payable_turnover_annual DECIMAL(10,4),
            accounts_payable_days_annual DECIMAL(10,4),
            cash_cycle_annual DECIMAL(10,4),
            -- 营运能力（TTM 口径）
            inventory_turnover_ttm DECIMAL(10,4),
            inventory_days_ttm DECIMAL(10,4),
            accounts_receivable_turnover_ttm DECIMAL(10,4),
            accounts_receivable_days_ttm DECIMAL(10,4),
            accounts_payable_turnover_ttm DECIMAL(10,4),
            accounts_payable_days_ttm DECIMAL(10,4),
            cash_cycle_ttm DECIMAL(10,4),
            -- 现金流质量指标（TTM 口径）
            cash_profit_coverage_ttm DECIMAL(10,4),
            fcf_ttm DECIMAL(18,2),
            fcf_profit_coverage_ttm DECIMAL(10,4),
            cash_interest_coverage_ttm DECIMAL(10,4),
            PRIMARY KEY (stock_code, report_date, report_type)
        )
        """)
        
        # 给现有表增加新字段
        new_cols = [
            ('total_assets', 'DECIMAL(18,2)'),
            ('operating_cost', 'DECIMAL(18,2)'),
            ('net_profit_deducted', 'DECIMAL(18,2)'),
            ('inventory', 'DECIMAL(18,2)'),
            ('accounts_receivable', 'DECIMAL(18,2)'),
            ('accounts_payable', 'DECIMAL(18,2)'),
            ('capex', 'DECIMAL(18,2)'),
            ('interest_expense', 'DECIMAL(18,2)'),
            ('operating_cash_flow', 'DECIMAL(18,2)'),
            ('net_profit_q', 'DECIMAL(18,2)'),
            ('total_revenue_q', 'DECIMAL(18,2)'),
            ('eps_q', 'DECIMAL(10,4)'),
            ('operating_cost_q', 'DECIMAL(18,2)'),
            ('net_profit_deducted_q', 'DECIMAL(18,2)'),
            ('operating_cash_flow_q', 'DECIMAL(18,2)'),
            ('capex_q', 'DECIMAL(18,2)'),
            ('net_profit_ttm', 'DECIMAL(18,2)'),
            ('total_revenue_ttm', 'DECIMAL(18,2)'),
            ('eps_ttm', 'DECIMAL(10,4)'),
            ('operating_cost_ttm', 'DECIMAL(18,2)'),
            ('net_profit_deducted_ttm', 'DECIMAL(18,2)'),
            ('operating_cash_flow_ttm', 'DECIMAL(18,2)'),
            ('capex_ttm', 'DECIMAL(18,2)'),
            ('interest_expense_ttm', 'DECIMAL(18,2)'),
            ('avg_equity_parent', 'DECIMAL(18,2)'),
            ('avg_total_assets', 'DECIMAL(18,2)'),
            ('avg_inventory', 'DECIMAL(18,2)'),
            ('avg_accounts_receivable', 'DECIMAL(18,2)'),
            ('avg_accounts_payable', 'DECIMAL(18,2)'),
            ('avg_equity_parent_ttm', 'DECIMAL(18,2)'),
            ('avg_total_assets_ttm', 'DECIMAL(18,2)'),
            ('avg_inventory_ttm', 'DECIMAL(18,2)'),
            ('avg_accounts_receivable_ttm', 'DECIMAL(18,2)'),
            ('avg_accounts_payable_ttm', 'DECIMAL(18,2)'),
            ('roe_annual', 'DECIMAL(10,4)'),
            ('roa_annual', 'DECIMAL(10,4)'),
            ('gross_margin_annual', 'DECIMAL(10,4)'),
            ('net_margin_parent_annual', 'DECIMAL(10,4)'),
            ('net_margin_deducted_annual', 'DECIMAL(10,4)'),
            ('roe_ttm', 'DECIMAL(10,4)'),
            ('roa_ttm', 'DECIMAL(10,4)'),
            ('gross_margin_ttm', 'DECIMAL(10,4)'),
            ('net_margin_parent_ttm', 'DECIMAL(10,4)'),
            ('net_margin_deducted_ttm', 'DECIMAL(10,4)'),
            ('revenue_yoy_annual', 'DECIMAL(10,4)'),
            ('net_profit_yoy_annual', 'DECIMAL(10,4)'),
            ('revenue_yoy_qoq', 'DECIMAL(10,4)'),
            ('net_profit_yoy_qoq', 'DECIMAL(10,4)'),
            ('revenue_yoy_ttm', 'DECIMAL(10,4)'),
            ('net_profit_yoy_ttm', 'DECIMAL(10,4)'),
            ('revenue_cagr_3y', 'DECIMAL(10,4)'),
            ('net_profit_cagr_3y', 'DECIMAL(10,4)'),
            ('net_profit_deducted_cagr_3y', 'DECIMAL(10,4)'),
            ('dupont_net_margin_annual', 'DECIMAL(10,4)'),
            ('dupont_asset_turnover_annual', 'DECIMAL(10,4)'),
            ('dupont_equity_multiplier_annual', 'DECIMAL(10,4)'),
            ('dupont_net_margin_ttm', 'DECIMAL(10,4)'),
            ('dupont_asset_turnover_ttm', 'DECIMAL(10,4)'),
            ('dupont_equity_multiplier_ttm', 'DECIMAL(10,4)'),
            ('inventory_turnover_annual', 'DECIMAL(10,4)'),
            ('inventory_days_annual', 'DECIMAL(10,4)'),
            ('accounts_receivable_turnover_annual', 'DECIMAL(10,4)'),
            ('accounts_receivable_days_annual', 'DECIMAL(10,4)'),
            ('accounts_payable_turnover_annual', 'DECIMAL(10,4)'),
            ('accounts_payable_days_annual', 'DECIMAL(10,4)'),
            ('cash_cycle_annual', 'DECIMAL(10,4)'),
            ('inventory_turnover_ttm', 'DECIMAL(10,4)'),
            ('inventory_days_ttm', 'DECIMAL(10,4)'),
            ('accounts_receivable_turnover_ttm', 'DECIMAL(10,4)'),
            ('accounts_receivable_days_ttm', 'DECIMAL(10,4)'),
            ('accounts_payable_turnover_ttm', 'DECIMAL(10,4)'),
            ('accounts_payable_days_ttm', 'DECIMAL(10,4)'),
            ('cash_cycle_ttm', 'DECIMAL(10,4)'),
            ('cash_profit_coverage_ttm', 'DECIMAL(10,4)'),
            ('fcf_ttm', 'DECIMAL(18,2)'),
            ('fcf_profit_coverage_ttm', 'DECIMAL(10,4)'),
            ('cash_interest_coverage_ttm', 'DECIMAL(10,4)'),
        ]
        
        for col_name, col_type in new_cols:
            try:
                self.conn.execute(f"ALTER TABLE financial_intermediate ADD COLUMN IF NOT EXISTS {col_name} {col_type}")
            except Exception as e:
                logger.debug(f"financial_intermediate 表可能已有 {col_name} 字段，跳过")
        
        # 北向资金表（个股日频）
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS northbound_flow (
            stock_code VARCHAR(10) NOT NULL,
            trade_date DATE NOT NULL,
            net_inflow DECIMAL(18,2),
            holding_shares DECIMAL(20,0),
            holding_value DECIMAL(18,2),
            holding_ratio DECIMAL(10,4),
            inflow_5d DECIMAL(18,2),
            inflow_10d DECIMAL(18,2),
            inflow_30d DECIMAL(18,2),
            PRIMARY KEY (stock_code, trade_date)
        )
        """)
        
        # 融资融券表
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS margin_trading (
            stock_code VARCHAR(10) NOT NULL,
            trade_date DATE NOT NULL,
            rz_balance DECIMAL(18,2),
            rz_change DECIMAL(18,2),
            rz_change_pct DECIMAL(10,4),
            rq_balance DECIMAL(18,2),
            rq_change DECIMAL(18,2),
            rq_change_pct DECIMAL(10,4),
            total_balance DECIMAL(18,2),
            total_change DECIMAL(18,2),
            total_change_pct DECIMAL(10,4),
            PRIMARY KEY (stock_code, trade_date)
        )
        """)
        
        # 龙虎榜表
        self.conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_dragon_tiger_id;
        CREATE TABLE IF NOT EXISTS dragon_tiger (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_dragon_tiger_id'),
            stock_code VARCHAR(10) NOT NULL,
            trade_date DATE NOT NULL,
            list_type VARCHAR(50),
            reason VARCHAR(255),
            buy_amount DECIMAL(18,2),
            sell_amount DECIMAL(18,2),
            net_amount DECIMAL(18,2),
            institution_buy_ratio DECIMAL(10,4),
            institution_sell_ratio DECIMAL(10,4),
            institution_net_ratio DECIMAL(10,4),
            UNIQUE (stock_code, trade_date, list_type)
        )
        """)
        
        # 龙虎榜席位明细
        self.conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_dragon_tiger_detail_id;
        CREATE TABLE IF NOT EXISTS dragon_tiger_detail (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_dragon_tiger_detail_id'),
            dragon_tiger_id INTEGER NOT NULL,
            seat_name VARCHAR(255),
            seat_type VARCHAR(50),
            buy_amount DECIMAL(18,2),
            sell_amount DECIMAL(18,2),
            net_amount DECIMAL(18,2),
            rank INTEGER
        )
        """)
        
        # 数据元数据表（存储字段说明）
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS column_metadata (
            table_name VARCHAR(50) NOT NULL,
            column_name VARCHAR(50) NOT NULL,
            description VARCHAR(255),
            unit VARCHAR(20),
            PRIMARY KEY (table_name, column_name)
        )
        """)
        
        # 初始化元数据
        self._init_column_metadata()
        
        logger.info("数据库表结构初始化完成")
        
    def _init_column_metadata(self):
        """初始化字段元数据（说明和单位）"""
        metadata = [
            # stock_daily
            ('stock_daily', 'stock_code', '股票代码', None),
            ('stock_daily', 'trade_date', '交易日期', None),
            ('stock_daily', 'open', '开盘价', '元'),
            ('stock_daily', 'high', '最高价', '元'),
            ('stock_daily', 'low', '最低价', '元'),
            ('stock_daily', 'close', '收盘价', '元'),
            ('stock_daily', 'volume', '成交量', '股'),
            ('stock_daily', 'amount', '成交额', '元'),
            ('stock_daily', 'adjust_factor', '复权因子', None),
            ('stock_daily', 'prev_close', '昨日收盘价', '元'),
            ('stock_daily', 'change_pct', '涨跌幅', '%'),
            ('stock_daily', 'amplitude', '振幅', '%'),
            ('stock_daily', 'body_size', '实体幅度', '元'),
            ('stock_daily', 'upper_shadow', '上影线', '元'),
            ('stock_daily', 'lower_shadow', '下影线', '元'),
            ('stock_daily', 'high_20', '20日最高价', '元'),
            ('stock_daily', 'low_20', '20日最低价', '元'),
            ('stock_daily', 'high_60', '60日最高价', '元'),
            ('stock_daily', 'low_60', '60日最低价', '元'),
            
            # financial_statements
            ('financial_statements', 'stock_code', '股票代码', None),
            ('financial_statements', 'report_date', '报告期', None),
            ('financial_statements', 'report_type', '报告类型', None),
            ('financial_statements', 'total_revenue', '营业收入', '元'),
            ('financial_statements', 'net_profit', '净利润', '元'),
            ('financial_statements', 'total_assets', '总资产', '元'),
            ('financial_statements', 'total_liabilities', '总负债', '元'),
            ('financial_statements', 'operating_cash_flow', '经营现金流', '元'),
            ('financial_statements', 'eps', '每股收益', '元'),
            ('financial_statements', 'roe', '净资产收益率', '%'),
            ('financial_statements', 'equity_parent', '归属于母公司股东权益', '元'),
            ('financial_statements', 'announcement_date', '公告日期', None),
            ('financial_statements', 'operating_cost', '营业成本', '元'),
            ('financial_statements', 'net_profit_deducted', '扣非归母净利润', '元'),
            ('financial_statements', 'inventory', '存货', '元'),
            ('financial_statements', 'accounts_receivable', '应收账款', '元'),
            ('financial_statements', 'accounts_payable', '应付账款', '元'),
            ('financial_statements', 'capex', '购建固定资产、无形资产支付的现金', '元'),
            ('financial_statements', 'interest_expense', '利息支出', '元'),
            
            # announcements
            ('announcements', 'id', 'ID', None),
            ('announcements', 'stock_code', '股票代码', None),
            ('announcements', 'announcement_date', '公告日期', None),
            ('announcements', 'title', '公告标题', None),
            ('announcements', 'pdf_url', 'PDF链接', None),
            ('announcements', 'announcement_type', '公告类型', None),
            
            # update_log
            ('update_log', 'id', 'ID', None),
            ('update_log', 'stock_code', '股票代码', None),
            ('update_log', 'data_type', '数据类型', None),
            ('update_log', 'last_update_date', '最后更新日期', None),
            ('update_log', 'update_time', '更新时间', None),
            
            # technical_indicators
            ('technical_indicators', 'stock_code', '股票代码', None),
            ('technical_indicators', 'trade_date', '交易日期', None),
            ('technical_indicators', 'ma5', '5日均线', '元'),
            ('technical_indicators', 'ma10', '10日均线', '元'),
            ('technical_indicators', 'ma20', '20日均线', '元'),
            ('technical_indicators', 'ma60', '60日均线', '元'),
            ('technical_indicators', 'ema12', 'EMA12', '元'),
            ('technical_indicators', 'ema26', 'EMA26', '元'),
            ('technical_indicators', 'boll_mid', '布林带中轨(MA20)', '元'),
            ('technical_indicators', 'boll_upper', '布林带上轨', '元'),
            ('technical_indicators', 'boll_lower', '布林带下轨', '元'),
            ('technical_indicators', 'macd_dif', 'MACD-DIF', None),
            ('technical_indicators', 'macd_dea', 'MACD-DEA', None),
            ('technical_indicators', 'macd_hist', 'MACD红绿柱', None),
            ('technical_indicators', 'bias5', 'BIAS5乖离率', '%'),
            ('technical_indicators', 'bias10', 'BIAS10乖离率', '%'),
            ('technical_indicators', 'bias20', 'BIAS20乖离率', '%'),
            ('technical_indicators', 'bias60', 'BIAS60乖离率', '%'),
            ('technical_indicators', 'rsi6', 'RSI6', None),
            ('technical_indicators', 'rsi12', 'RSI12', None),
            ('technical_indicators', 'rsi24', 'RSI24', None),
            ('technical_indicators', 'kdj_k', 'KDJ-K值', None),
            ('technical_indicators', 'kdj_d', 'KDJ-D值', None),
            ('technical_indicators', 'kdj_j', 'KDJ-J值', None),
            ('technical_indicators', 'cci20', 'CCI20顺势指标', None),
            ('technical_indicators', 'wr14', 'WR14威廉指标', '%'),
            ('technical_indicators', 'atr14', 'ATR14平均真实波幅', '元'),
            ('technical_indicators', 'std20', '20日收盘价标准差', '元'),
            ('technical_indicators', 'vol_ma5', 'VOL-MA5均量线', '股'),
            ('technical_indicators', 'vol_ma10', 'VOL-MA10均量线', '股'),
            ('technical_indicators', 'obv', 'OBV能量潮', '股'),
            ('technical_indicators', 'mfi14', 'MFI14资金流向指标', None),
            ('technical_indicators', 'vr', 'VR成交量变异率', None),
            ('technical_indicators', 'dmi_pdi', 'DMI+DI', None),
            ('technical_indicators', 'dmi_mdi', 'DMI-DI', None),
            ('technical_indicators', 'dmi_adx', 'DMI-ADX', None),
            ('technical_indicators', 'dmi_adxr', 'DMI-ADXR', None),
            ('technical_indicators', 'sar', 'SAR抛物线止损指标', '元'),
            ('technical_indicators', 'wvad', 'WVAD威廉变异离散量', None),
            
            # valuation_indicators
            ('valuation_indicators', 'stock_code', '股票代码', None),
            ('valuation_indicators', 'trade_date', '交易日期', None),
            ('valuation_indicators', 'pe_ttm', '市盈率TTM', '倍'),
            ('valuation_indicators', 'pb', '市净率', '倍'),
            ('valuation_indicators', 'ps_ttm', '市销率TTM', '倍'),
            ('valuation_indicators', 'dividend_yield', '股息率', '%'),
            ('valuation_indicators', 'roe', '净资产收益率', '%'),
            ('valuation_indicators', 'pe_annual', '市盈率（年度口径）', '倍'),
            ('valuation_indicators', 'ps_annual', '市销率（年度口径）', '倍'),
            ('valuation_indicators', 'roe_ttm', '净资产收益率TTM', '%'),
            ('valuation_indicators', 'roe_annual', '净资产收益率（年度口径）', '%'),
            ('valuation_indicators', 'used_report_date', '使用的财报报告期', None),
            ('valuation_indicators', 'used_announcement_date', '使用的财报公告日期', None),
            
            # stock_capital
            ('stock_capital', 'stock_code', '股票代码', None),
            ('stock_capital', 'record_date', '记录日期', None),
            ('stock_capital', 'total_shares', '总股本', '股'),
            
            # dividends
            ('dividends', 'stock_code', '股票代码', None),
            ('dividends', 'dividend_date', '分红日期', None),
            ('dividends', 'cash_per_share', '每股分红', '元'),
            ('dividends', 'announcement_date', '公告日期', None),
            
            # financial_intermediate
            ('financial_intermediate', 'stock_code', '股票代码', None),
            ('financial_intermediate', 'report_date', '报告期', None),
            ('financial_intermediate', 'report_type', '报告类型', None),
            ('financial_intermediate', 'eps', '每股收益（累计值）', '元'),
            ('financial_intermediate', 'bvps', '每股净资产', '元'),
            ('financial_intermediate', 'revenue_per_share', '每股营业收入（累计值）', '元'),
            ('financial_intermediate', 'net_profit', '净利润（累计值）', '元'),
            ('financial_intermediate', 'total_revenue', '营业收入（累计值）', '元'),
            ('financial_intermediate', 'equity', '股东权益合计', '元'),
            ('financial_intermediate', 'equity_parent', '归属于母公司股东权益', '元'),
            ('financial_intermediate', 'total_assets', '总资产', '元'),
            ('financial_intermediate', 'total_shares', '总股本', '股'),
            ('financial_intermediate', 'announcement_date', '公告日期', None),
            ('financial_intermediate', 'operating_cost', '营业成本（累计值）', '元'),
            ('financial_intermediate', 'net_profit_deducted', '扣非归母净利润（累计值）', '元'),
            ('financial_intermediate', 'inventory', '存货', '元'),
            ('financial_intermediate', 'accounts_receivable', '应收账款', '元'),
            ('financial_intermediate', 'accounts_payable', '应付账款', '元'),
            ('financial_intermediate', 'capex', '购建固定资产、无形资产支付的现金（累计值）', '元'),
            ('financial_intermediate', 'interest_expense', '利息支出（累计值）', '元'),
            ('financial_intermediate', 'operating_cash_flow', '经营现金流（累计值）', '元'),
            ('financial_intermediate', 'net_profit_q', '净利润（单季度）', '元'),
            ('financial_intermediate', 'total_revenue_q', '营业收入（单季度）', '元'),
            ('financial_intermediate', 'eps_q', '每股收益（单季度）', '元'),
            ('financial_intermediate', 'operating_cost_q', '营业成本（单季度）', '元'),
            ('financial_intermediate', 'net_profit_deducted_q', '扣非归母净利润（单季度）', '元'),
            ('financial_intermediate', 'operating_cash_flow_q', '经营现金流（单季度）', '元'),
            ('financial_intermediate', 'capex_q', '购建固定资产、无形资产支付的现金（单季度）', '元'),
            ('financial_intermediate', 'net_profit_ttm', '净利润（TTM）', '元'),
            ('financial_intermediate', 'total_revenue_ttm', '营业收入（TTM）', '元'),
            ('financial_intermediate', 'eps_ttm', '每股收益（TTM）', '元'),
            ('financial_intermediate', 'operating_cost_ttm', '营业成本（TTM）', '元'),
            ('financial_intermediate', 'net_profit_deducted_ttm', '扣非归母净利润（TTM）', '元'),
            ('financial_intermediate', 'operating_cash_flow_ttm', '经营现金流（TTM）', '元'),
            ('financial_intermediate', 'capex_ttm', '购建固定资产、无形资产支付的现金（TTM）', '元'),
            ('financial_intermediate', 'interest_expense_ttm', '利息支出（TTM）', '元'),
            ('financial_intermediate', 'avg_equity_parent', '平均归母净资产（年报口径：FY→FY）', '元'),
            ('financial_intermediate', 'avg_total_assets', '平均总资产（年报口径：FY→FY）', '元'),
            ('financial_intermediate', 'avg_inventory', '平均存货（年报口径：FY→FY）', '元'),
            ('financial_intermediate', 'avg_accounts_receivable', '平均应收账款（年报口径：FY→FY）', '元'),
            ('financial_intermediate', 'avg_accounts_payable', '平均应付账款（年报口径：FY→FY）', '元'),
            ('financial_intermediate', 'avg_equity_parent_ttm', '平均归母净资产（TTM口径：当前→4Q前）', '元'),
            ('financial_intermediate', 'avg_total_assets_ttm', '平均总资产（TTM口径：当前→4Q前）', '元'),
            ('financial_intermediate', 'avg_inventory_ttm', '平均存货（TTM口径：当前→4Q前）', '元'),
            ('financial_intermediate', 'avg_accounts_receivable_ttm', '平均应收账款（TTM口径：当前→4Q前）', '元'),
            ('financial_intermediate', 'avg_accounts_payable_ttm', '平均应付账款（TTM口径：当前→4Q前）', '元'),
            ('financial_intermediate', 'roe_annual', 'ROE（年度口径）', '%'),
            ('financial_intermediate', 'roa_annual', 'ROA（年度口径）', '%'),
            ('financial_intermediate', 'gross_margin_annual', '销售毛利率（年度口径）', '%'),
            ('financial_intermediate', 'net_margin_parent_annual', '归母净利率（年度口径）', '%'),
            ('financial_intermediate', 'net_margin_deducted_annual', '扣非净利率（年度口径）', '%'),
            ('financial_intermediate', 'roe_ttm', 'ROE（TTM口径）', '%'),
            ('financial_intermediate', 'roa_ttm', 'ROA（TTM口径）', '%'),
            ('financial_intermediate', 'gross_margin_ttm', '销售毛利率（TTM口径）', '%'),
            ('financial_intermediate', 'net_margin_parent_ttm', '归母净利率（TTM口径）', '%'),
            ('financial_intermediate', 'net_margin_deducted_ttm', '扣非净利率（TTM口径）', '%'),
            ('financial_intermediate', 'revenue_yoy_annual', '营业收入同比增速（年度）', '%'),
            ('financial_intermediate', 'net_profit_yoy_annual', '净利润同比增速（年度）', '%'),
            ('financial_intermediate', 'revenue_yoy_qoq', '营业收入同比增速（单季度）', '%'),
            ('financial_intermediate', 'net_profit_yoy_qoq', '净利润同比增速（单季度）', '%'),
            ('financial_intermediate', 'revenue_yoy_ttm', '营业收入同比增速（TTM）', '%'),
            ('financial_intermediate', 'net_profit_yoy_ttm', '净利润同比增速（TTM）', '%'),
            ('financial_intermediate', 'revenue_cagr_3y', '营业收入3年CAGR', '%'),
            ('financial_intermediate', 'net_profit_cagr_3y', '净利润3年CAGR', '%'),
            ('financial_intermediate', 'net_profit_deducted_cagr_3y', '扣非净利润3年CAGR', '%'),
            ('financial_intermediate', 'dupont_net_margin_annual', '杜邦-销售净利率（年度口径）', '%'),
            ('financial_intermediate', 'dupont_asset_turnover_annual', '杜邦-总资产周转率（年度口径）', '次'),
            ('financial_intermediate', 'dupont_equity_multiplier_annual', '杜邦-权益乘数（年度口径）', '倍'),
            ('financial_intermediate', 'dupont_net_margin_ttm', '杜邦-销售净利率（TTM口径）', '%'),
            ('financial_intermediate', 'dupont_asset_turnover_ttm', '杜邦-总资产周转率（TTM口径）', '次'),
            ('financial_intermediate', 'dupont_equity_multiplier_ttm', '杜邦-权益乘数（TTM口径）', '倍'),
            ('financial_intermediate', 'inventory_turnover_annual', '存货周转率（年度口径）', '次'),
            ('financial_intermediate', 'inventory_days_annual', '存货周转天数（年度口径）', '天'),
            ('financial_intermediate', 'accounts_receivable_turnover_annual', '应收账款周转率（年度口径）', '次'),
            ('financial_intermediate', 'accounts_receivable_days_annual', '应收账款周转天数（年度口径）', '天'),
            ('financial_intermediate', 'accounts_payable_turnover_annual', '应付账款周转率（年度口径）', '次'),
            ('financial_intermediate', 'accounts_payable_days_annual', '应付账款周转天数（年度口径）', '天'),
            ('financial_intermediate', 'cash_cycle_annual', '现金周期（年度口径）', '天'),
            ('financial_intermediate', 'inventory_turnover_ttm', '存货周转率（TTM口径）', '次'),
            ('financial_intermediate', 'inventory_days_ttm', '存货周转天数（TTM口径）', '天'),
            ('financial_intermediate', 'accounts_receivable_turnover_ttm', '应收账款周转率（TTM口径）', '次'),
            ('financial_intermediate', 'accounts_receivable_days_ttm', '应收账款周转天数（TTM口径）', '天'),
            ('financial_intermediate', 'accounts_payable_turnover_ttm', '应付账款周转率（TTM口径）', '次'),
            ('financial_intermediate', 'accounts_payable_days_ttm', '应付账款周转天数（TTM口径）', '天'),
            ('financial_intermediate', 'cash_cycle_ttm', '现金周期（TTM口径）', '天'),
            ('financial_intermediate', 'cash_profit_coverage_ttm', '盈利现金保障倍数（TTM）', '倍'),
            ('financial_intermediate', 'fcf_ttm', '自由现金流FCF（TTM）', '元'),
            ('financial_intermediate', 'fcf_profit_coverage_ttm', 'FCF净利润覆盖率（TTM）', '倍'),
            ('financial_intermediate', 'cash_interest_coverage_ttm', '现金流利息覆盖倍数（TTM）', '倍'),
            
            # northbound_flow
            ('northbound_flow', 'stock_code', '股票代码', None),
            ('northbound_flow', 'trade_date', '交易日期', None),
            ('northbound_flow', 'net_inflow', '当日净流入金额', '元'),
            ('northbound_flow', 'holding_shares', '持股数量', '股'),
            ('northbound_flow', 'holding_value', '持股市值', '元'),
            ('northbound_flow', 'holding_ratio', '持股占比', '%'),
            ('northbound_flow', 'inflow_5d', '5日累计净流入', '元'),
            ('northbound_flow', 'inflow_10d', '10日累计净流入', '元'),
            ('northbound_flow', 'inflow_30d', '30日累计净流入', '元'),
            
            # margin_trading
            ('margin_trading', 'stock_code', '股票代码', None),
            ('margin_trading', 'trade_date', '交易日期', None),
            ('margin_trading', 'rz_balance', '融资余额', '元'),
            ('margin_trading', 'rz_change', '融资余额变动', '元'),
            ('margin_trading', 'rz_change_pct', '融资余额变动幅度', '%'),
            ('margin_trading', 'rq_balance', '融券余额', '元'),
            ('margin_trading', 'rq_change', '融券余额变动', '元'),
            ('margin_trading', 'rq_change_pct', '融券余额变动幅度', '%'),
            ('margin_trading', 'total_balance', '融资融券总余额', '元'),
            ('margin_trading', 'total_change', '融资融券总余额变动', '元'),
            ('margin_trading', 'total_change_pct', '融资融券总余额变动幅度', '%'),
            
            # dragon_tiger
            ('dragon_tiger', 'id', 'ID', None),
            ('dragon_tiger', 'stock_code', '股票代码', None),
            ('dragon_tiger', 'trade_date', '交易日期', None),
            ('dragon_tiger', 'list_type', '榜单类型', None),
            ('dragon_tiger', 'reason', '上榜原因', None),
            ('dragon_tiger', 'buy_amount', '买入总额', '元'),
            ('dragon_tiger', 'sell_amount', '卖出总额', '元'),
            ('dragon_tiger', 'net_amount', '净额', '元'),
            ('dragon_tiger', 'institution_buy_ratio', '机构买入占比', '%'),
            ('dragon_tiger', 'institution_sell_ratio', '机构卖出占比', '%'),
            ('dragon_tiger', 'institution_net_ratio', '机构净买卖占比', '%'),
            
            # dragon_tiger_detail
            ('dragon_tiger_detail', 'id', 'ID', None),
            ('dragon_tiger_detail', 'dragon_tiger_id', '龙虎榜ID', None),
            ('dragon_tiger_detail', 'seat_name', '席位名称', None),
            ('dragon_tiger_detail', 'seat_type', '席位类型', None),
            ('dragon_tiger_detail', 'buy_amount', '买入金额', '元'),
            ('dragon_tiger_detail', 'sell_amount', '卖出金额', '元'),
            ('dragon_tiger_detail', 'net_amount', '净额', '元'),
            ('dragon_tiger_detail', 'rank', '排名', None),
        ]
        
        # 插入或更新元数据
        for table_name, column_name, description, unit in metadata:
            try:
                self.conn.execute("""
                INSERT OR REPLACE INTO column_metadata 
                (table_name, column_name, description, unit)
                VALUES (?, ?, ?, ?)
                """, (table_name, column_name, description, unit))
            except Exception as e:
                logger.debug(f"无法插入元数据 ({table_name}.{column_name}): {e}")
        
    def _get_last_update_date(self, stock_code: str, data_type: str) -> str:
        """获取指定股票指定数据类型的最后更新日期"""
        result = self.conn.execute("""
        SELECT MAX(last_update_date) FROM update_log 
        WHERE stock_code = ? AND data_type = ?
        """, (stock_code, data_type)).fetchone()
        
        if result[0] is None:
            return START_DATE
        else:
            # 从最后更新日期的下一天开始
            last_date = datetime.strptime(str(result[0]), "%Y-%m-%d")
            next_date = last_date + timedelta(days=1)
            return next_date.strftime("%Y%m%d")
            
    def _update_last_update_date(self, stock_code: str, data_type: str, end_date: str):
        """更新最后更新日期记录"""
        self.conn.execute("""
        INSERT INTO update_log (stock_code, data_type, last_update_date, update_time)
        VALUES (?, ?, ?, now())
        ON CONFLICT (stock_code, data_type)
        DO UPDATE SET last_update_date = EXCLUDED.last_update_date,
                      update_time = now()
        """, (stock_code, data_type, end_date))
        
    def collect_daily_data(self, stock_code: str):
        """采集单只股票的日线数据（增量更新）"""
        last_update = self._get_last_update_date(stock_code, "daily")
        today = datetime.now().strftime("%Y%m%d")
        
        if last_update > today:
            logger.info(f"{stock_code} 日线数据已是最新，无需更新")
            return
            
        try:
            logger.info(f"开始采集 {stock_code} 日线数据，时间范围：{last_update} 至 {today}")
            
            # 获取前复权日线数据
            df = ak.stock_zh_a_daily(
                symbol=stock_code,
                start_date=last_update,
                end_date=today,
                adjust="qfq"
            )
            
            if df.empty:
                logger.info(f"{stock_code} 没有新的日线数据")
                return
                
            # 重命名列以匹配数据库
            df = df.rename(columns={
                "date": "trade_date",
                "turnover": "amount"
            })
            
            # 添加股票代码列
            df["stock_code"] = stock_code
            
            # 转换日期格式
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
            
            # 插入原始数据到数据库（自动去重）
            self.conn.register("df", df)
            self.conn.execute("""
            INSERT INTO stock_daily 
            (stock_code, trade_date, open, high, low, close, volume, amount, adjust_factor)
            SELECT stock_code, trade_date, open, high, low, close, volume, amount, adjust_factor
            FROM df
            ON CONFLICT (stock_code, trade_date) DO NOTHING
            """)
            self.conn.unregister("df")
            
            # 计算并更新指标
            self._calculate_daily_indicators(stock_code)
            self._calculate_technical_indicators(stock_code)
            
            # 更新最后更新日期
            today_fmt = datetime.now().strftime("%Y-%m-%d")
            self._update_last_update_date(stock_code, "daily", today_fmt)
            
            logger.info(f"{stock_code} 日线数据采集完成，新增 {len(df)} 条记录")
            
        except Exception as e:
            logger.error(f"{stock_code} 日线数据采集失败：{str(e)}")
    
    def _calculate_daily_indicators(self, stock_code: str):
        """计算并更新日线指标（涨跌幅、振幅、K线指标、高低点等）"""
        try:
            # 获取完整历史数据
            df = self.conn.execute("""
            SELECT stock_code, trade_date, open, high, low, close
            FROM stock_daily
            WHERE stock_code = ?
            ORDER BY trade_date
            """, (stock_code,)).fetchdf()
            
            if df.empty:
                return
            
            df = df.sort_values('trade_date').reset_index(drop=True)
            
            # 1. 计算昨日收盘价
            df['prev_close'] = df['close'].shift(1)
            
            # 2. 计算涨跌幅 (change_pct) = (今收 - 昨收) / 昨收 * 100
            df['change_pct'] = (df['close'] - df['prev_close']) / df['prev_close'] * 100
            
            # 3. 计算振幅 (amplitude) = (最高 - 最低) / 昨收 * 100
            df['amplitude'] = (df['high'] - df['low']) / df['prev_close'] * 100
            
            # 4. 计算实体幅度 (body_size) = | 收盘 - 开盘 |
            df['body_size'] = abs(df['close'] - df['open'])
            
            # 5. 计算上影线 (upper_shadow) = 最高 - max(开盘, 收盘)
            df['upper_shadow'] = df['high'] - df[['open', 'close']].max(axis=1)
            
            # 6. 计算下影线 (lower_shadow) = min(开盘, 收盘) - 最低
            df['lower_shadow'] = df[['open', 'close']].min(axis=1) - df['low']
            
            # 7. 计算20日最高价（包含当日）
            df['high_20'] = df['high'].rolling(window=20, min_periods=1).max()
            
            # 8. 计算20日最低价
            df['low_20'] = df['low'].rolling(window=20, min_periods=1).min()
            
            # 9. 计算60日最高价
            df['high_60'] = df['high'].rolling(window=60, min_periods=1).max()
            
            # 10. 计算60日最低价
            df['low_60'] = df['low'].rolling(window=60, min_periods=1).min()
            
            # 更新数据库
            self.conn.register('df_update', df)
            self.conn.execute("""
            UPDATE stock_daily
            SET prev_close = df_update.prev_close,
                change_pct = df_update.change_pct,
                amplitude = df_update.amplitude,
                body_size = df_update.body_size,
                upper_shadow = df_update.upper_shadow,
                lower_shadow = df_update.lower_shadow,
                high_20 = df_update.high_20,
                low_20 = df_update.low_20,
                high_60 = df_update.high_60,
                low_60 = df_update.low_60
            FROM df_update
            WHERE stock_daily.stock_code = df_update.stock_code 
              AND stock_daily.trade_date = df_update.trade_date
            """)
            self.conn.unregister('df_update')
            
            logger.debug(f"{stock_code} 日线指标计算完成")
            
        except Exception as e:
            logger.error(f"{stock_code} 日线指标计算失败：{str(e)}")
    
    def recalculate_all_daily_indicators(self):
        """重新计算所有股票的日线指标"""
        logger.info("开始重新计算所有股票日线指标")
        
        # 获取所有股票代码
        stock_codes = self.conn.execute("SELECT DISTINCT stock_code FROM stock_daily").fetchall()
        
        for (stock_code,) in stock_codes:
            logger.info(f"计算 {stock_code} 指标...")
            self._calculate_daily_indicators(stock_code)
        
        logger.info("所有股票日线指标计算完成")
    
    def _calculate_technical_indicators(self, stock_code: str):
        """计算所有技术指标并写入 technical_indicators 表"""
        try:
            # 获取完整的行情数据
            df = self.conn.execute("""
            SELECT stock_code, trade_date, open, high, low, close, volume, amount
            FROM stock_daily
            WHERE stock_code = ?
            ORDER BY trade_date
            """, (stock_code,)).fetchdf()
            
            if len(df) < 2:
                return
            
            df = df.sort_values('trade_date').reset_index(drop=True)
            n = len(df)
            
            # ============================================
            # 1. 均线类：MA5, MA10, MA20, MA60
            # ============================================
            df['ma5'] = df['close'].rolling(5, min_periods=1).mean()
            df['ma10'] = df['close'].rolling(10, min_periods=1).mean()
            df['ma20'] = df['close'].rolling(20, min_periods=1).mean()
            df['ma60'] = df['close'].rolling(60, min_periods=1).mean()
            
            # ============================================
            # 2. 趋势类：EMA12, EMA26, MACD, BOLL, BIAS
            # ============================================
            
            # EMA - 指数移动平均
            df['ema12'] = df['close'].ewm(span=12, adjust=False, min_periods=1).mean()
            df['ema26'] = df['close'].ewm(span=26, adjust=False, min_periods=1).mean()
            
            # MACD (12, 26, 9)
            df['macd_dif'] = df['ema12'] - df['ema26']
            df['macd_dea'] = df['macd_dif'].ewm(span=9, adjust=False, min_periods=1).mean()
            df['macd_hist'] = (df['macd_dif'] - df['macd_dea']) * 2
            
            # 布林带 (BOLL) - 中轨MA20，上轨MA20 + 2*标准差，下轨MA20 - 2*标准差
            df['boll_mid'] = df['ma20']
            df['boll_std'] = df['close'].rolling(20, min_periods=1).std()
            df['boll_upper'] = df['boll_mid'] + 2 * df['boll_std']
            df['boll_lower'] = df['boll_mid'] - 2 * df['boll_std']
            
            # BIAS 乖离率 = (收盘价 - 均线) / 均线 * 100
            df['bias5'] = (df['close'] - df['ma5']) / df['ma5'] * 100
            df['bias10'] = (df['close'] - df['ma10']) / df['ma10'] * 100
            df['bias20'] = (df['close'] - df['ma20']) / df['ma20'] * 100
            df['bias60'] = (df['close'] - df['ma60']) / df['ma60'] * 100
            
            # ============================================
            # 3. 震荡类：RSI, KDJ, CCI, WR
            # ============================================
            
            # RSI - 相对强弱指标
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).fillna(0)
            loss = (-delta.where(delta < 0, 0)).fillna(0)
            
            for period in [6, 12, 24]:
                avg_gain = gain.rolling(window=period, min_periods=1).mean()
                avg_loss = loss.rolling(window=period, min_periods=1).mean()
                rs = avg_gain / avg_loss.where(avg_loss != 0, pd.NA)
                df[f'rsi{period}'] = 100 - (100 / (1 + rs))
            
            # KDJ (9, 3, 3)
            low_list = df['low'].rolling(9, min_periods=1).min()
            high_list = df['high'].rolling(9, min_periods=1).max()
            rsv = (df['close'] - low_list) / (high_list - low_list).replace(0, pd.NA) * 100
            rsv = rsv.fillna(50)
            
            df['kdj_k'] = rsv.ewm(com=2, adjust=False, min_periods=1).mean()
            df['kdj_d'] = df['kdj_k'].ewm(com=2, adjust=False, min_periods=1).mean()
            df['kdj_j'] = 3 * df['kdj_k'] - 2 * df['kdj_d']
            
            # CCI (20) - 顺势指标
            tp = (df['high'] + df['low'] + df['close']) / 3
            ma_tp = tp.rolling(20, min_periods=1).mean()
            md = tp.rolling(20, min_periods=1).apply(lambda x: (abs(x - x.mean())).mean(), raw=True)
            df['cci20'] = (tp - ma_tp) / (0.015 * md).replace(0, pd.NA)
            df['cci20'] = df['cci20'].fillna(0)
            
            # WR (14) - 威廉指标
            low14 = df['low'].rolling(14, min_periods=1).min()
            high14 = df['high'].rolling(14, min_periods=1).max()
            df['wr14'] = (high14 - df['close']) / (high14 - low14).replace(0, pd.NA) * 100
            df['wr14'] = df['wr14'].fillna(50)
            
            # ============================================
            # 4. 波动率类：ATR, 标准差
            # ============================================
            
            # ATR (14) - 平均真实波幅
            tr1 = df['high'] - df['low']
            tr2 = abs(df['high'] - df['close'].shift(1))
            tr3 = abs(df['low'] - df['close'].shift(1))
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            df['atr14'] = tr.rolling(14, min_periods=1).mean()
            
            # 20日收盘价标准差
            df['std20'] = df['close'].rolling(20, min_periods=1).std()
            
            # ============================================
            # 5. 量价类：VOL-MA, OBV, MFI, VR
            # ============================================
            
            # 均量线
            df['vol_ma5'] = df['volume'].rolling(5, min_periods=1).mean()
            df['vol_ma10'] = df['volume'].rolling(10, min_periods=1).mean()
            
            # OBV - 能量潮
            obv = (df['volume'] * ((df['close'] > df['close'].shift(1)).astype(int) - 
                                    (df['close'] < df['close'].shift(1)).astype(int))).fillna(0)
            df['obv'] = obv.cumsum()
            
            # MFI (14) - 资金流向指标
            typical_price = (df['high'] + df['low'] + df['close']) / 3
            money_flow = typical_price * df['volume']
            
            positive_flow = money_flow.where(typical_price > typical_price.shift(1), 0)
            negative_flow = money_flow.where(typical_price < typical_price.shift(1), 0)
            
            pos_sum = positive_flow.rolling(14, min_periods=1).sum()
            neg_sum = negative_flow.rolling(14, min_periods=1).sum()
            
            mfi_ratio = pos_sum / neg_sum.where(neg_sum != 0, pd.NA)
            df['mfi14'] = 100 - (100 / (1 + mfi_ratio))
            df['mfi14'] = df['mfi14'].fillna(50)
            
            # VR - 成交量变异率
            up_vol = df['volume'].where(df['close'] > df['close'].shift(1), 0)
            down_vol = df['volume'].where(df['close'] < df['close'].shift(1), 0)
            flat_vol = df['volume'].where(df['close'] == df['close'].shift(1), 0)
            
            up_sum = up_vol.rolling(24, min_periods=1).sum()
            down_sum = down_vol.rolling(24, min_periods=1).sum()
            flat_sum = flat_vol.rolling(24, min_periods=1).sum()
            
            df['vr'] = (up_sum + flat_sum / 2) / (down_sum + flat_sum / 2).replace(0, pd.NA) * 100
            df['vr'] = df['vr'].fillna(100)
            
            # ============================================
            # 6. 进阶：DMI, SAR, WVAD
            # ============================================
            
            # DMI - 动向指标
            high = df['high']
            low = df['low']
            close = df['close']
            
            plus_dm = high.diff().clip(lower=0)
            minus_dm = -low.diff().clip(upper=0)
            
            tr = pd.concat([high - low, 
                          abs(high - close.shift()), 
                          abs(low - close.shift())], axis=1).max(axis=1)
            
            atr = tr.rolling(14, min_periods=1).mean()
            
            plus_di = 100 * (plus_dm.rolling(14, min_periods=1).mean() / atr.replace(0, pd.NA))
            minus_di = 100 * (minus_dm.rolling(14, min_periods=1).mean() / atr.replace(0, pd.NA))
            
            dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, pd.NA)
            adx = dx.rolling(14, min_periods=1).mean()
            adxr = (adx + adx.shift(14)).rolling(2, min_periods=1).mean()
            
            df['dmi_pdi'] = plus_di
            df['dmi_mdi'] = minus_di
            df['dmi_adx'] = adx
            df['dmi_adxr'] = adxr
            
            # SAR - 抛物线止损指标 (简化版实现)
            af_step = 0.02
            af_max = 0.2
            sar = pd.Series(index=df.index, dtype='float64')
            is_up_trend = True
            ep = df['high'][0] if n > 0 else 0
            af = af_step
            
            if n > 0:
                sar.iloc[0] = df['low'][0] if n > 0 else 0
                
                for i in range(1, n):
                    sar.iloc[i] = sar.iloc[i-1] + af * (ep - sar.iloc[i-1])
                    
                    if is_up_trend:
                        if df['low'][i] < sar.iloc[i]:
                            is_up_trend = False
                            sar.iloc[i] = ep
                            ep = df['low'][i]
                            af = af_step
                        else:
                            if df['high'][i] > ep:
                                ep = df['high'][i]
                                af = min(af + af_step, af_max)
                    else:
                        if df['high'][i] > sar.iloc[i]:
                            is_up_trend = True
                            sar.iloc[i] = ep
                            ep = df['high'][i]
                            af = af_step
                        else:
                            if df['low'][i] < ep:
                                ep = df['low'][i]
                                af = min(af + af_step, af_max)
            
            df['sar'] = sar
            
            # WVAD - 威廉变异离散量
            wvad = ((df['close'] - df['open']) / (df['high'] - df['low']).replace(0, pd.NA)) * df['volume']
            df['wvad'] = wvad.fillna(0)
            
            # ============================================
            # 写入数据库
            # ============================================
            
            # 先清理旧数据
            self.conn.execute("""
            DELETE FROM technical_indicators WHERE stock_code = ?
            """, (stock_code,))
            
            # 插入新数据
            self.conn.register('df_tech', df)
            self.conn.execute("""
            INSERT INTO technical_indicators (
                stock_code, trade_date,
                ma5, ma10, ma20, ma60,
                ema12, ema26, boll_mid, boll_upper, boll_lower,
                macd_dif, macd_dea, macd_hist,
                bias5, bias10, bias20, bias60,
                rsi6, rsi12, rsi24,
                kdj_k, kdj_d, kdj_j, cci20, wr14,
                atr14, std20,
                vol_ma5, vol_ma10, obv, mfi14, vr,
                dmi_pdi, dmi_mdi, dmi_adx, dmi_adxr, sar, wvad
            )
            SELECT 
                stock_code, trade_date,
                ma5, ma10, ma20, ma60,
                ema12, ema26, boll_mid, boll_upper, boll_lower,
                macd_dif, macd_dea, macd_hist,
                bias5, bias10, bias20, bias60,
                rsi6, rsi12, rsi24,
                kdj_k, kdj_d, kdj_j, cci20, wr14,
                atr14, std20,
                vol_ma5, vol_ma10, obv, mfi14, vr,
                dmi_pdi, dmi_mdi, dmi_adx, dmi_adxr, sar, wvad
            FROM df_tech
            """)
            self.conn.unregister('df_tech')
            
            logger.debug(f"{stock_code} 技术指标计算完成")
            
        except Exception as e:
            logger.error(f"{stock_code} 技术指标计算失败：{str(e)}")
    
    def recalculate_all_technical_indicators(self):
        """重新计算所有股票的技术指标"""
        logger.info("开始重新计算所有股票技术指标")
        
        # 获取所有股票代码
        stock_codes = self.conn.execute("SELECT DISTINCT stock_code FROM stock_daily").fetchall()
        
        for (stock_code,) in stock_codes:
            logger.info(f"计算 {stock_code} 技术指标...")
            self._calculate_technical_indicators(stock_code)
        
        logger.info("所有股票技术指标计算完成")
            
    def collect_financial_data(self, stock_code: str):
        """采集单只股票的财务数据（增量更新）"""
        last_update = self._get_last_update_date(stock_code, "financial")
        today = datetime.now().strftime("%Y%m%d")
        today_fmt = datetime.now().strftime("%Y-%m-%d")

        try:
            logger.info(f"开始采集 {stock_code} 财务数据")

            income_df = ak.stock_financial_report_sina(stock=stock_code, symbol="利润表")
            balance_df = ak.stock_financial_report_sina(stock=stock_code, symbol="资产负债表")
            cashflow_df = ak.stock_financial_report_sina(stock=stock_code, symbol="现金流量表")

            income_df["报告日"] = pd.to_datetime(income_df["报告日"])
            balance_df["报告日"] = pd.to_datetime(balance_df["报告日"])
            cashflow_df["报告日"] = pd.to_datetime(cashflow_df["报告日"])

            cutoff_date = datetime.strptime(last_update, "%Y%m%d")
            income_df = income_df[income_df["报告日"] >= cutoff_date]
            balance_df = balance_df[balance_df["报告日"] >= cutoff_date]
            cashflow_df = cashflow_df[cashflow_df["报告日"] >= cutoff_date]

            merged = income_df[["报告日", "类型"]].drop_duplicates()

            income_map = income_df.set_index(["报告日", "类型"])
            balance_map = balance_df.set_index(["报告日", "类型"])
            cashflow_map = cashflow_df.set_index(["报告日", "类型"])
            
            # 创建一个公告日期的映射
            announcement_date_map = {}
            # 从任意一个表中获取公告日期（三个表应该都有）
            for _, row in income_df.iterrows():
                key = (row["报告日"], row["类型"])
                announcement_date_map[key] = row.get("公告日期")
            for _, row in balance_df.iterrows():
                key = (row["报告日"], row["类型"])
                if key not in announcement_date_map:
                    announcement_date_map[key] = row.get("公告日期")

            financial_data = []
            for _, row in merged.iterrows():
                key = (row["报告日"], row["类型"])
                data = {
                    "stock_code": stock_code,
                    "report_date": key[0],
                    "report_type": key[1],
                    "total_revenue": None,
                    "net_profit": None,
                    "total_assets": None,
                    "total_liabilities": None,
                    "operating_cash_flow": None,
                    "eps": None,
                    "roe": None,
                    "equity_parent": None,
                    "announcement_date": None,
                    "operating_cost": None,
                    "net_profit_deducted": None,
                    "inventory": None,
                    "accounts_receivable": None,
                    "accounts_payable": None,
                    "capex": None,
                    "interest_expense": None,
                }

                if key in income_map.index:
                    ir = income_map.loc[key]
                    data["total_revenue"] = self._safe_get(ir, "营业总收入")
                    data["net_profit"] = self._safe_get(ir, "净利润")
                    data["eps"] = self._safe_get(ir, "基本每股收益")
                    data["operating_cost"] = self._safe_get(ir, "营业成本")
                    if data["operating_cost"] is None:
                        data["operating_cost"] = self._safe_get(ir, "营业总成本")
                    data["net_profit_deducted"] = self._safe_get(ir, "扣除非经常性损益后的净利润")
                    if data["net_profit_deducted"] is None:
                        data["net_profit_deducted"] = self._safe_get(ir, "扣非净利润")
                    data["interest_expense"] = self._safe_get(ir, "利息费用")
                    if data["interest_expense"] is None:
                        data["interest_expense"] = self._safe_get(ir, "利息支出")

                if key in balance_map.index:
                    br = balance_map.loc[key]
                    data["total_assets"] = self._safe_get(br, "资产总计")
                    data["total_liabilities"] = self._safe_get(br, "负债合计")
                    data["equity_parent"] = self._safe_get(br, "归属于母公司股东权益合计")
                    if data["equity_parent"] is None:
                        data["equity_parent"] = self._safe_get(br, "归属于母公司股东的权益")
                    if data["equity_parent"] is None:
                        data["equity_parent"] = self._safe_get(br, "归属于母公司所有者权益")
                    if data["equity_parent"] is None:
                        data["equity_parent"] = self._safe_get(br, "股东权益合计")
                    data["inventory"] = self._safe_get(br, "存货")
                    data["accounts_receivable"] = self._safe_get(br, "应收账款")
                    if data["accounts_receivable"] is None:
                        data["accounts_receivable"] = self._safe_get(br, "应收票据及应收账款")
                    data["accounts_payable"] = self._safe_get(br, "应付账款")
                    if data["accounts_payable"] is None:
                        data["accounts_payable"] = self._safe_get(br, "应付票据及应付账款")

                if key in cashflow_map.index:
                    cr = cashflow_map.loc[key]
                    data["operating_cash_flow"] = self._safe_get(cr, "经营活动产生的现金流量净额")
                    data["capex"] = self._safe_get(cr, "购建固定资产、无形资产和其他长期资产所支付的现金")
                    if data["capex"] is None:
                        data["capex"] = self._safe_get(cr, "购建固定资产、无形资产和其他长期资产支付的现金")
                    if data["capex"] is None:
                        data["capex"] = self._safe_get(cr, "购建固定资产、无形资产支付的现金")
                
                # 获取公告日期
                if key in announcement_date_map:
                    data["announcement_date"] = announcement_date_map[key]

                if data["total_assets"] and data["total_liabilities"]:
                    equity = data["total_assets"] - data["total_liabilities"]
                    if equity and equity != 0 and data["net_profit"]:
                        data["roe"] = round(data["net_profit"] / equity * 100, 4)

                financial_data.append(data)

            df = pd.DataFrame(financial_data)

            if df.empty:
                logger.info(f"{stock_code} 没有新的财务数据")
                return

            df["report_date"] = pd.to_datetime(df["report_date"]).dt.date
            # 处理公告日期
            df["announcement_date"] = pd.to_datetime(df["announcement_date"]).dt.date

            self.conn.register("df", df)
            self.conn.execute("""
            INSERT INTO financial_statements 
            (stock_code, report_date, report_type, total_revenue, net_profit,
             total_assets, total_liabilities, operating_cash_flow, eps, roe, equity_parent, announcement_date,
             operating_cost, net_profit_deducted, inventory, accounts_receivable, accounts_payable, capex, interest_expense)
            SELECT stock_code, report_date, report_type, total_revenue, net_profit,
                   total_assets, total_liabilities, operating_cash_flow, eps, roe, equity_parent, announcement_date,
                   operating_cost, net_profit_deducted, inventory, accounts_receivable, accounts_payable, capex, interest_expense
            FROM df
            ON CONFLICT (stock_code, report_date, report_type) DO UPDATE SET
                total_revenue = EXCLUDED.total_revenue,
                net_profit = EXCLUDED.net_profit,
                total_assets = EXCLUDED.total_assets,
                total_liabilities = EXCLUDED.total_liabilities,
                operating_cash_flow = EXCLUDED.operating_cash_flow,
                eps = EXCLUDED.eps,
                roe = EXCLUDED.roe,
                equity_parent = EXCLUDED.equity_parent,
                announcement_date = EXCLUDED.announcement_date,
                operating_cost = EXCLUDED.operating_cost,
                net_profit_deducted = EXCLUDED.net_profit_deducted,
                inventory = EXCLUDED.inventory,
                accounts_receivable = EXCLUDED.accounts_receivable,
                accounts_payable = EXCLUDED.accounts_payable,
                capex = EXCLUDED.capex,
                interest_expense = EXCLUDED.interest_expense
            """)
            self.conn.unregister("df")

            self._update_missing_financial_fields(stock_code)

            self._update_last_update_date(stock_code, "financial", today_fmt)

            logger.info(f"{stock_code} 财务数据采集完成，新增 {len(df)} 条记录")

        except Exception as e:
            logger.error(f"{stock_code} 财务数据采集失败：{str(e)}")

    @staticmethod
    def _safe_get(row, col_name):
        """安全获取字段值，避免KeyError"""
        val = row.get(col_name) if hasattr(row, "get") else None
        if val is None:
            return None
        try:
            result = float(val)
            if pd.isna(result):
                return None
            return result
        except (ValueError, TypeError):
            return None

    def _update_missing_financial_fields(self, stock_code: str):
        """补充采集 Sina 利润表的利息支出 + 东方财富的扣非归母净利润和利息支出"""
        try:
            income_df = ak.stock_financial_report_sina(stock=stock_code, symbol="利润表")
            income_df["报告日"] = pd.to_datetime(income_df["报告日"]).dt.date

            for _, row in income_df.iterrows():
                rd = row["报告日"]
                rt = row["类型"]
                interest = self._safe_get(row, "利息费用")
                if interest is None:
                    interest = self._safe_get(row, "利息支出")
                if interest is not None:
                    self.conn.execute("""
                        UPDATE financial_statements 
                        SET interest_expense = ? 
                        WHERE stock_code = ? AND report_date = ? AND report_type = ?
                    """, [interest, stock_code, rd, rt])
        except Exception as e:
            logger.warning(f"{stock_code} Sina利息支出补充采集失败：{e}")

        try:
            if stock_code.startswith('sh'):
                em_code = 'SH' + stock_code[2:]
            elif stock_code.startswith('sz'):
                em_code = 'SZ' + stock_code[2:]
            else:
                em_code = stock_code.upper()

            df = ak.stock_profit_sheet_by_report_em(symbol=em_code)
            df['report_date'] = pd.to_datetime(df['REPORT_DATE']).dt.date

            for _, row in df.iterrows():
                rd = row['report_date']

                deducted = row.get('DEDUCT_PARENT_NETPROFIT')
                if pd.notna(deducted):
                    self.conn.execute("""
                        UPDATE financial_statements 
                        SET net_profit_deducted = ? 
                        WHERE stock_code = ? AND report_date = ?
                    """, [float(deducted), stock_code, rd])

                existing = self.conn.execute("""
                    SELECT interest_expense FROM financial_statements 
                    WHERE stock_code = ? AND report_date = ?
                """, [stock_code, rd]).fetchone()

                if existing is None or existing[0] is None:
                    interest = row.get('INTEREST_EXPENSE')
                    if pd.isna(interest):
                        interest = row.get('FE_INTEREST_EXPENSE')
                    if pd.isna(interest):
                        interest = row.get('FINANCE_EXPENSE')
                    if pd.notna(interest):
                        self.conn.execute("""
                            UPDATE financial_statements 
                            SET interest_expense = ? 
                            WHERE stock_code = ? AND report_date = ?
                        """, [float(interest), stock_code, rd])
        except Exception as e:
            logger.warning(f"{stock_code} 东方财富补充采集失败：{e}")

    @staticmethod
    def _to_float(val):
        """将 Decimal/str/None 统一转为 float，None 返回 None"""
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None
            
    def collect_announcements(self, stock_code: str):
        """采集单只股票的公告元数据（增量更新）"""
        last_update = self._get_last_update_date(stock_code, "announcement")
        today_ymd = datetime.now().strftime("%Y%m%d")

        if last_update > today_ymd:
            logger.info(f"{stock_code} 公告数据已是最新，无需更新")
            return

        try:
            start_fmt = f"{last_update[:4]}-{last_update[4:6]}-{last_update[6:8]}"
            end_fmt = datetime.now().strftime("%Y-%m-%d")
            logger.info(f"开始采集 {stock_code} 公告数据，时间范围：{start_fmt} 至 {end_fmt}")

            code = stock_code[2:]

            df = ak.stock_individual_notice_report(
                security=code,
                begin_date=start_fmt,
                end_date=end_fmt
            )

            if df.empty:
                logger.info(f"{stock_code} 没有新的公告数据")
                return

            df = df.rename(columns={
                "公告标题": "title",
                "公告类型": "announcement_type",
                "公告日期": "announcement_date",
                "网址": "pdf_url"
            })
            df["stock_code"] = stock_code

            df["announcement_date"] = pd.to_datetime(df["announcement_date"]).dt.date

            self.conn.register("df", df)
            self.conn.execute("""
            INSERT INTO announcements 
            (stock_code, announcement_date, title, pdf_url, announcement_type)
            SELECT stock_code, announcement_date, title, pdf_url, announcement_type
            FROM df
            ON CONFLICT (stock_code, announcement_date, title) DO NOTHING
            """)
            self.conn.unregister("df")

            self._update_last_update_date(stock_code, "announcement", end_fmt)

            logger.info(f"{stock_code} 公告数据采集完成，新增 {len(df)} 条记录")

        except Exception as e:
            logger.error(f"{stock_code} 公告数据采集失败：{str(e)}")
            
    def collect_capital_data(self, stock_code: str):
        """采集单只股票的总股本数据"""
        try:
            logger.info(f"开始采集 {stock_code} 总股本数据")
            
            code = stock_code[2:]
            
            # 使用 akshare 获取股本结构
            df = ak.stock_individual_info_em(symbol=code)
            
            if df.empty:
                logger.info(f"{stock_code} 总股本数据为空")
                return
            
            # 查找总股本
            total_shares = None
            for _, row in df.iterrows():
                if '总股本' in str(row.get('item', '')):
                    try:
                        total_shares = float(row.get('value', 0))
                        total_shares = int(total_shares)
                        break
                    except:
                        continue
            
            if total_shares is None:
                logger.info(f"{stock_code} 未找到总股本数据")
                return
            
            today = datetime.now().strftime("%Y-%m-%d")
            
            # 插入数据库
            self.conn.execute("""
            INSERT INTO stock_capital (stock_code, record_date, total_shares)
            VALUES (?, ?, ?)
            ON CONFLICT (stock_code, record_date) DO UPDATE SET total_shares = EXCLUDED.total_shares
            """, (stock_code, today, total_shares))
            
            logger.info(f"{stock_code} 总股本数据采集完成，总股本：{total_shares:,} 股")
            
        except Exception as e:
            logger.error(f"{stock_code} 总股本数据采集失败：{str(e)}")
    
    def collect_dividend_data(self, stock_code: str):
        """采集单只股票的分红数据"""
        try:
            logger.info(f"开始采集 {stock_code} 分红数据")
            
            code = stock_code[2:]
            
            try:
                df = ak.stock_history_dividend_detail(symbol=code, indicator="分红")
            except:
                logger.info(f"{stock_code} 分红数据接口调用失败")
                return
            
            if df.empty:
                logger.info(f"{stock_code} 没有分红数据")
                return
            
            dividend_data = []
            for _, row in df.iterrows():
                try:
                    cash_per_share_raw = self._safe_get(row, '派息')
                    if cash_per_share_raw is None or cash_per_share_raw == 0:
                        continue
                    
                    progress = str(row.get('进度', ''))
                    if '实施' not in progress:
                        continue
                    
                    dividend_date = row.get('除权除息日')
                    if pd.isna(dividend_date):
                        continue
                    
                    dividend_date = pd.to_datetime(dividend_date).date()
                    
                    announcement_date = row.get('公告日期')
                    if not pd.isna(announcement_date):
                        announcement_date = pd.to_datetime(announcement_date).date()
                    else:
                        announcement_date = None
                    
                    cash_per_share = round(cash_per_share_raw / 10, 4)
                    
                    dividend_data.append({
                        'stock_code': stock_code,
                        'dividend_date': dividend_date,
                        'cash_per_share': cash_per_share,
                        'announcement_date': announcement_date
                    })
                except Exception as e:
                    continue
            
            if not dividend_data:
                logger.info(f"{stock_code} 没有有效的分红数据")
                return
            
            df_div = pd.DataFrame(dividend_data)
            
            self.conn.register("df_div", df_div)
            self.conn.execute("""
            INSERT INTO dividends (stock_code, dividend_date, cash_per_share, announcement_date)
            SELECT stock_code, dividend_date, cash_per_share, announcement_date
            FROM df_div
            ON CONFLICT (stock_code, dividend_date) DO NOTHING
            """)
            self.conn.unregister("df_div")
            
            logger.info(f"{stock_code} 分红数据采集完成，新增 {len(df_div)} 条记录")
            
        except Exception as e:
            logger.error(f"{stock_code} 分红数据采集失败：{str(e)}")
            
    def calculate_financial_intermediate(self, stock_code: str):
        """计算财务中间指标（完整财务处理规则实现）"""
        try:
            logger.info(f"开始计算 {stock_code} 财务中间指标")
            
            # 获取完整财务数据
            df_finance = self.conn.execute("""
                SELECT report_date, report_type, total_revenue, net_profit, total_assets, total_liabilities, 
                       eps, equity_parent, announcement_date, operating_cost, net_profit_deducted, 
                       inventory, accounts_receivable, accounts_payable, capex, interest_expense, operating_cash_flow
                FROM financial_statements 
                WHERE stock_code = ? 
                ORDER BY report_date
            """, (stock_code,)).fetchdf()
            
            if df_finance.empty:
                logger.info(f"{stock_code} 没有财务数据")
                return
            
            # 获取最新总股本
            total_shares = None
            capital_result = self.conn.execute("""
                SELECT total_shares 
                FROM stock_capital 
                WHERE stock_code = ? 
                ORDER BY record_date DESC 
                LIMIT 1
            """, (stock_code,)).fetchone()
            if capital_result:
                total_shares = capital_result[0]
            
            df_finance = df_finance.sort_values('report_date').reset_index(drop=True)
            
            # 根据 report_date 推断季度类型
            def get_quarter_type(report_date_val):
                dt = pd.to_datetime(report_date_val)
                month = dt.month
                day = dt.day
                if month == 3 and day == 31:
                    return 'Q1'
                elif month == 6 and day == 30:
                    return 'Q2'
                elif month == 9 and day == 30:
                    return 'Q3'
                elif month == 12 and day == 31:
                    return 'FY'
                else:
                    if month <= 3:
                        return 'Q1'
                    elif month <= 6:
                        return 'Q2'
                    elif month <= 9:
                        return 'Q3'
                    else:
                        return 'FY'
            
            df_finance['quarter_type'] = df_finance['report_date'].apply(get_quarter_type)
            
            intermediate_data = []
            n = len(df_finance)
            
            # 预先计算每条记录的单季度数据，避免重复计算
            q_data_cache = {}
            for i in range(n):
                row = df_finance.iloc[i]
                qt = row['quarter_type']
                
                q_data = {}
                q_data['net_profit_q'] = self._calc_q_data(df_finance, i, qt, 'net_profit')
                q_data['total_revenue_q'] = self._calc_q_data(df_finance, i, qt, 'total_revenue')
                q_data['operating_cost_q'] = self._calc_q_data(df_finance, i, qt, 'operating_cost')
                q_data['net_profit_deducted_q'] = self._calc_q_data(df_finance, i, qt, 'net_profit_deducted')
                q_data['operating_cash_flow_q'] = self._calc_q_data(df_finance, i, qt, 'operating_cash_flow')
                q_data['capex_q'] = self._calc_q_data(df_finance, i, qt, 'capex')
                q_data['eps_q'] = self._calc_q_data(df_finance, i, qt, 'eps')
                q_data['interest_expense_q'] = self._calc_q_data(df_finance, i, qt, 'interest_expense')
                
                q_data_cache[i] = q_data
            
            # 处理每条记录
            for i in range(n):
                row = df_finance.iloc[i]
                report_date = row['report_date']
                quarter_type = row['quarter_type']
                report_type = row['report_type']
                
                # 基础数据
                net_profit = self._to_float(row.get('net_profit'))
                total_revenue = self._to_float(row.get('total_revenue'))
                total_assets = self._to_float(row.get('total_assets'))
                total_liabilities = self._to_float(row.get('total_liabilities'))
                eps = self._to_float(row.get('eps'))
                equity_parent = self._to_float(row.get('equity_parent'))
                announcement_date = row.get('announcement_date')
                operating_cost = self._to_float(row.get('operating_cost'))
                net_profit_deducted = self._to_float(row.get('net_profit_deducted'))
                inventory = self._to_float(row.get('inventory'))
                accounts_receivable = self._to_float(row.get('accounts_receivable'))
                accounts_payable = self._to_float(row.get('accounts_payable'))
                capex = self._to_float(row.get('capex'))
                interest_expense = self._to_float(row.get('interest_expense'))
                operating_cash_flow = self._to_float(row.get('operating_cash_flow'))
                
                # 计算净资产等基础指标
                equity = None
                equity_to_use = None
                bvps = None
                revenue_per_share = None
                
                if total_assets is not None and total_liabilities is not None:
                    equity = total_assets - total_liabilities
                
                if equity_parent is not None and equity_parent > 0:
                    equity_to_use = equity_parent
                elif equity is not None and equity > 0:
                    equity_to_use = equity
                
                if equity_to_use is not None and total_shares is not None and total_shares > 0:
                    bvps = round(float(equity_to_use) / float(total_shares), 4)
                
                if total_revenue is not None and total_shares is not None and total_shares > 0:
                    revenue_per_share = round(float(total_revenue) / float(total_shares), 4)
                
                # 从缓存中获取单季度数据
                q_cache = q_data_cache[i]
                net_profit_q = q_cache['net_profit_q']
                total_revenue_q = q_cache['total_revenue_q']
                operating_cost_q = q_cache['operating_cost_q']
                net_profit_deducted_q = q_cache['net_profit_deducted_q']
                operating_cash_flow_q = q_cache['operating_cash_flow_q']
                capex_q = q_cache['capex_q']
                eps_q = q_cache['eps_q']
                
                # 计算 TTM 数据
                ttm_data = self._calc_ttm_data(df_finance, q_data_cache, i)
                net_profit_ttm = ttm_data['net_profit_ttm']
                total_revenue_ttm = ttm_data['total_revenue_ttm']
                operating_cost_ttm = ttm_data['operating_cost_ttm']
                net_profit_deducted_ttm = ttm_data['net_profit_deducted_ttm']
                operating_cash_flow_ttm = ttm_data['operating_cash_flow_ttm']
                capex_ttm = ttm_data['capex_ttm']
                eps_ttm = ttm_data['eps_ttm']
                interest_expense_ttm = ttm_data['interest_expense_ttm']
                
                # 计算平均资产类数据（年报口径：FY→FY）
                avg_data_annual = self._calc_avg_data(df_finance, i, 'annual')
                avg_equity_parent = avg_data_annual['avg_equity_parent']
                avg_total_assets = avg_data_annual['avg_total_assets']
                avg_inventory = avg_data_annual['avg_inventory']
                avg_accounts_receivable = avg_data_annual['avg_accounts_receivable']
                avg_accounts_payable = avg_data_annual['avg_accounts_payable']
                
                # 计算平均资产类数据（TTM口径：当前→4季度前）
                avg_data_ttm = self._calc_avg_data(df_finance, i, 'ttm')
                avg_equity_parent_ttm = avg_data_ttm['avg_equity_parent']
                avg_total_assets_ttm = avg_data_ttm['avg_total_assets']
                avg_inventory_ttm = avg_data_ttm['avg_inventory']
                avg_accounts_receivable_ttm = avg_data_ttm['avg_accounts_receivable']
                avg_accounts_payable_ttm = avg_data_ttm['avg_accounts_payable']
                
                # --- 盈利类指标（年度口径）---
                roe_annual, roa_annual = None, None
                gross_margin_annual, net_margin_parent_annual, net_margin_deducted_annual = None, None, None
                
                if quarter_type == 'FY':
                    if net_profit is not None and avg_equity_parent is not None and avg_equity_parent > 0:
                        roe_annual = round(float(net_profit) / float(avg_equity_parent) * 100, 4)
                    if net_profit is not None and avg_total_assets is not None and avg_total_assets > 0:
                        roa_annual = round(float(net_profit) / float(avg_total_assets) * 100, 4)
                    if total_revenue is not None and operating_cost is not None and total_revenue > 0:
                        gross_margin_annual = round(float(total_revenue - operating_cost) / float(total_revenue) * 100, 4)
                    if net_profit is not None and total_revenue is not None and total_revenue > 0:
                        net_margin_parent_annual = round(float(net_profit) / float(total_revenue) * 100, 4)
                    if net_profit_deducted is not None and total_revenue is not None and total_revenue > 0:
                        net_margin_deducted_annual = round(float(net_profit_deducted) / float(total_revenue) * 100, 4)
                
                # --- 盈利类指标（TTM 口径）---
                roe_ttm, roa_ttm = None, None
                gross_margin_ttm, net_margin_parent_ttm, net_margin_deducted_ttm = None, None, None
                
                if net_profit_ttm is not None and avg_equity_parent_ttm is not None and avg_equity_parent_ttm > 0:
                    roe_ttm = round(float(net_profit_ttm) / float(avg_equity_parent_ttm) * 100, 4)
                if net_profit_ttm is not None and avg_total_assets_ttm is not None and avg_total_assets_ttm > 0:
                    roa_ttm = round(float(net_profit_ttm) / float(avg_total_assets_ttm) * 100, 4)
                if total_revenue_ttm is not None and operating_cost_ttm is not None and total_revenue_ttm > 0:
                    gross_margin_ttm = round(float(total_revenue_ttm - operating_cost_ttm) / float(total_revenue_ttm) * 100, 4)
                if net_profit_ttm is not None and total_revenue_ttm is not None and total_revenue_ttm > 0:
                    net_margin_parent_ttm = round(float(net_profit_ttm) / float(total_revenue_ttm) * 100, 4)
                if net_profit_deducted_ttm is not None and total_revenue_ttm is not None and total_revenue_ttm > 0:
                    net_margin_deducted_ttm = round(float(net_profit_deducted_ttm) / float(total_revenue_ttm) * 100, 4)
                
                # --- 成长类指标（同比增速）---
                growth_data = self._calc_growth_rates(df_finance, i, q_data_cache, quarter_type)
                revenue_yoy_annual = growth_data['revenue_yoy_annual']
                net_profit_yoy_annual = growth_data['net_profit_yoy_annual']
                revenue_yoy_qoq = growth_data['revenue_yoy_qoq']
                net_profit_yoy_qoq = growth_data['net_profit_yoy_qoq']
                revenue_yoy_ttm = growth_data['revenue_yoy_ttm']
                net_profit_yoy_ttm = growth_data['net_profit_yoy_ttm']
                
                # --- 成长类指标（3年CAGR）---
                cagr_data = self._calc_cagr(df_finance, i)
                revenue_cagr_3y = cagr_data['revenue_cagr_3y']
                net_profit_cagr_3y = cagr_data['net_profit_cagr_3y']
                net_profit_deducted_cagr_3y = cagr_data['net_profit_deducted_cagr_3y']
                
                # --- 杜邦三层拆解（年度口径）---
                dupont_annual = self._calc_dupont(total_revenue, net_profit, avg_total_assets, avg_equity_parent)
                dupont_net_margin_annual = dupont_annual['net_margin']
                dupont_asset_turnover_annual = dupont_annual['asset_turnover']
                dupont_equity_multiplier_annual = dupont_annual['equity_multiplier']
                
                # --- 杜邦三层拆解（TTM 口径）---
                dupont_ttm = self._calc_dupont(total_revenue_ttm, net_profit_ttm, avg_total_assets_ttm, avg_equity_parent_ttm)
                dupont_net_margin_ttm = dupont_ttm['net_margin']
                dupont_asset_turnover_ttm = dupont_ttm['asset_turnover']
                dupont_equity_multiplier_ttm = dupont_ttm['equity_multiplier']
                
                # --- 营运能力（年度口径）---
                ops_annual = self._calc_ops_data(operating_cost, total_revenue, avg_inventory, 
                                               avg_accounts_receivable, avg_accounts_payable)
                inventory_turnover_annual = ops_annual['inventory_turnover']
                inventory_days_annual = ops_annual['inventory_days']
                accounts_receivable_turnover_annual = ops_annual['accounts_receivable_turnover']
                accounts_receivable_days_annual = ops_annual['accounts_receivable_days']
                accounts_payable_turnover_annual = ops_annual['accounts_payable_turnover']
                accounts_payable_days_annual = ops_annual['accounts_payable_days']
                cash_cycle_annual = ops_annual['cash_cycle']
                
                # --- 营运能力（TTM 口径）---
                ops_ttm = self._calc_ops_data(operating_cost_ttm, total_revenue_ttm, avg_inventory_ttm, 
                                             avg_accounts_receivable_ttm, avg_accounts_payable_ttm)
                inventory_turnover_ttm = ops_ttm['inventory_turnover']
                inventory_days_ttm = ops_ttm['inventory_days']
                accounts_receivable_turnover_ttm = ops_ttm['accounts_receivable_turnover']
                accounts_receivable_days_ttm = ops_ttm['accounts_receivable_days']
                accounts_payable_turnover_ttm = ops_ttm['accounts_payable_turnover']
                accounts_payable_days_ttm = ops_ttm['accounts_payable_days']
                cash_cycle_ttm = ops_ttm['cash_cycle']
                
                # --- 现金流质量指标（TTM 口径）---
                cf_data = self._calc_cash_flow_data(operating_cash_flow_ttm, net_profit_ttm, 
                                                   capex_ttm, interest_expense_ttm)
                cash_profit_coverage_ttm = cf_data['cash_profit_coverage']
                fcf_ttm = cf_data['fcf']
                fcf_profit_coverage_ttm = cf_data['fcf_profit_coverage']
                cash_interest_coverage_ttm = cf_data['cash_interest_coverage']
                
                intermediate_data.append({
                    'stock_code': stock_code,
                    'report_date': pd.to_datetime(report_date).date(),
                    'report_type': report_type,
                    'eps': eps,
                    'bvps': bvps,
                    'revenue_per_share': revenue_per_share,
                    'net_profit': net_profit,
                    'total_revenue': total_revenue,
                    'equity': equity,
                    'equity_parent': equity_parent,
                    'total_assets': total_assets,
                    'total_shares': total_shares,
                    'announcement_date': pd.to_datetime(announcement_date).date() if announcement_date is not None else None,
                    'operating_cost': operating_cost,
                    'net_profit_deducted': net_profit_deducted,
                    'inventory': inventory,
                    'accounts_receivable': accounts_receivable,
                    'accounts_payable': accounts_payable,
                    'capex': capex,
                    'interest_expense': interest_expense,
                    'operating_cash_flow': operating_cash_flow,
                    'net_profit_q': net_profit_q,
                    'total_revenue_q': total_revenue_q,
                    'eps_q': eps_q,
                    'operating_cost_q': operating_cost_q,
                    'net_profit_deducted_q': net_profit_deducted_q,
                    'operating_cash_flow_q': operating_cash_flow_q,
                    'capex_q': capex_q,
                    'net_profit_ttm': net_profit_ttm,
                    'total_revenue_ttm': total_revenue_ttm,
                    'eps_ttm': eps_ttm,
                    'operating_cost_ttm': operating_cost_ttm,
                    'net_profit_deducted_ttm': net_profit_deducted_ttm,
                    'operating_cash_flow_ttm': operating_cash_flow_ttm,
                    'capex_ttm': capex_ttm,
                    'interest_expense_ttm': interest_expense_ttm,
                    'avg_equity_parent': avg_equity_parent,
                    'avg_total_assets': avg_total_assets,
                    'avg_inventory': avg_inventory,
                    'avg_accounts_receivable': avg_accounts_receivable,
                    'avg_accounts_payable': avg_accounts_payable,
                    'avg_equity_parent_ttm': avg_equity_parent_ttm,
                    'avg_total_assets_ttm': avg_total_assets_ttm,
                    'avg_inventory_ttm': avg_inventory_ttm,
                    'avg_accounts_receivable_ttm': avg_accounts_receivable_ttm,
                    'avg_accounts_payable_ttm': avg_accounts_payable_ttm,
                    'roe_annual': roe_annual,
                    'roa_annual': roa_annual,
                    'gross_margin_annual': gross_margin_annual,
                    'net_margin_parent_annual': net_margin_parent_annual,
                    'net_margin_deducted_annual': net_margin_deducted_annual,
                    'roe_ttm': roe_ttm,
                    'roa_ttm': roa_ttm,
                    'gross_margin_ttm': gross_margin_ttm,
                    'net_margin_parent_ttm': net_margin_parent_ttm,
                    'net_margin_deducted_ttm': net_margin_deducted_ttm,
                    'revenue_yoy_annual': revenue_yoy_annual,
                    'net_profit_yoy_annual': net_profit_yoy_annual,
                    'revenue_yoy_qoq': revenue_yoy_qoq,
                    'net_profit_yoy_qoq': net_profit_yoy_qoq,
                    'revenue_yoy_ttm': revenue_yoy_ttm,
                    'net_profit_yoy_ttm': net_profit_yoy_ttm,
                    'revenue_cagr_3y': revenue_cagr_3y,
                    'net_profit_cagr_3y': net_profit_cagr_3y,
                    'net_profit_deducted_cagr_3y': net_profit_deducted_cagr_3y,
                    'dupont_net_margin_annual': dupont_net_margin_annual,
                    'dupont_asset_turnover_annual': dupont_asset_turnover_annual,
                    'dupont_equity_multiplier_annual': dupont_equity_multiplier_annual,
                    'dupont_net_margin_ttm': dupont_net_margin_ttm,
                    'dupont_asset_turnover_ttm': dupont_asset_turnover_ttm,
                    'dupont_equity_multiplier_ttm': dupont_equity_multiplier_ttm,
                    'inventory_turnover_annual': inventory_turnover_annual,
                    'inventory_days_annual': inventory_days_annual,
                    'accounts_receivable_turnover_annual': accounts_receivable_turnover_annual,
                    'accounts_receivable_days_annual': accounts_receivable_days_annual,
                    'accounts_payable_turnover_annual': accounts_payable_turnover_annual,
                    'accounts_payable_days_annual': accounts_payable_days_annual,
                    'cash_cycle_annual': cash_cycle_annual,
                    'inventory_turnover_ttm': inventory_turnover_ttm,
                    'inventory_days_ttm': inventory_days_ttm,
                    'accounts_receivable_turnover_ttm': accounts_receivable_turnover_ttm,
                    'accounts_receivable_days_ttm': accounts_receivable_days_ttm,
                    'accounts_payable_turnover_ttm': accounts_payable_turnover_ttm,
                    'accounts_payable_days_ttm': accounts_payable_days_ttm,
                    'cash_cycle_ttm': cash_cycle_ttm,
                    'cash_profit_coverage_ttm': cash_profit_coverage_ttm,
                    'fcf_ttm': fcf_ttm,
                    'fcf_profit_coverage_ttm': fcf_profit_coverage_ttm,
                    'cash_interest_coverage_ttm': cash_interest_coverage_ttm,
                })
            
            df_inter = pd.DataFrame(intermediate_data)
            
            # 插入数据库
            self.conn.register("df_inter", df_inter)
            
            col_list = [
                'stock_code', 'report_date', 'report_type', 'eps', 'bvps', 'revenue_per_share', 
                'net_profit', 'total_revenue', 'equity', 'equity_parent', 'total_assets', 
                'total_shares', 'announcement_date', 'operating_cost', 'net_profit_deducted',
                'inventory', 'accounts_receivable', 'accounts_payable', 'capex', 'interest_expense',
                'operating_cash_flow', 'net_profit_q', 'total_revenue_q', 'eps_q', 'operating_cost_q',
                'net_profit_deducted_q', 'operating_cash_flow_q', 'capex_q', 'net_profit_ttm',
                'total_revenue_ttm', 'eps_ttm', 'operating_cost_ttm', 'net_profit_deducted_ttm',
                'operating_cash_flow_ttm', 'capex_ttm', 'interest_expense_ttm', 'avg_equity_parent', 'avg_total_assets',
                'avg_inventory', 'avg_accounts_receivable', 'avg_accounts_payable',
                'avg_equity_parent_ttm', 'avg_total_assets_ttm', 'avg_inventory_ttm',
                'avg_accounts_receivable_ttm', 'avg_accounts_payable_ttm',
                'roe_annual', 'roa_annual', 'gross_margin_annual', 'net_margin_parent_annual',
                'net_margin_deducted_annual', 'roe_ttm', 'roa_ttm', 'gross_margin_ttm',
                'net_margin_parent_ttm', 'net_margin_deducted_ttm', 'revenue_yoy_annual',
                'net_profit_yoy_annual', 'revenue_yoy_qoq', 'net_profit_yoy_qoq',
                'revenue_yoy_ttm', 'net_profit_yoy_ttm', 'revenue_cagr_3y', 'net_profit_cagr_3y',
                'net_profit_deducted_cagr_3y', 'dupont_net_margin_annual', 'dupont_asset_turnover_annual',
                'dupont_equity_multiplier_annual', 'dupont_net_margin_ttm', 'dupont_asset_turnover_ttm',
                'dupont_equity_multiplier_ttm', 'inventory_turnover_annual', 'inventory_days_annual',
                'accounts_receivable_turnover_annual', 'accounts_receivable_days_annual',
                'accounts_payable_turnover_annual', 'accounts_payable_days_annual', 'cash_cycle_annual',
                'inventory_turnover_ttm', 'inventory_days_ttm', 'accounts_receivable_turnover_ttm',
                'accounts_receivable_days_ttm', 'accounts_payable_turnover_ttm', 'accounts_payable_days_ttm',
                'cash_cycle_ttm', 'cash_profit_coverage_ttm', 'fcf_ttm', 'fcf_profit_coverage_ttm',
                'cash_interest_coverage_ttm'
            ]
            
            update_clause = ', '.join([f"{col} = EXCLUDED.{col}" for col in col_list])
            insert_sql = f"""
            INSERT INTO financial_intermediate 
            ({', '.join(col_list)})
            SELECT {', '.join(col_list)}
            FROM df_inter
            ON CONFLICT (stock_code, report_date, report_type) DO UPDATE 
            SET {update_clause}
            """
            self.conn.execute(insert_sql)
            self.conn.unregister("df_inter")
            
            logger.info(f"{stock_code} 财务中间指标计算完成，共 {len(df_inter)} 条记录")
            
        except Exception as e:
            logger.error(f"{stock_code} 财务中间指标计算失败：{str(e)}")
            import traceback
            logger.error(traceback.format_exc())
    
    def _calc_q_data(self, df_finance, idx, qt, col_name):
        """计算单季度数据"""
        curr_val = self._to_float(df_finance.iloc[idx].get(col_name))
        
        if qt == 'Q1':
            return curr_val
        elif qt in ['Q2', 'Q3', 'FY']:
            prev_data = None
            report_date_dt = pd.to_datetime(df_finance.iloc[idx]['report_date'])
            target_prev_quarter = {'Q2': 'Q1', 'Q3': 'Q2', 'FY': 'Q3'}[qt]
            
            for j in range(idx-1, -1, -1):
                candidate = df_finance.iloc[j]
                candidate_date = pd.to_datetime(candidate['report_date'])
                candidate_qtype = candidate['quarter_type']
                if candidate_date.year == report_date_dt.year and candidate_qtype == target_prev_quarter:
                    prev_data = candidate
                    break
            
            if prev_data is not None:
                prev_val = self._to_float(prev_data.get(col_name))
                if curr_val is not None and prev_val is not None:
                    return curr_val - prev_val
        return None
    
    def _calc_ttm_data(self, df_finance, q_data_cache, idx):
        """计算 TTM 滚动 12 个月数据"""
        ttm_result = {
            'net_profit_ttm': None, 'total_revenue_ttm': None, 'operating_cost_ttm': None,
            'net_profit_deducted_ttm': None, 'operating_cash_flow_ttm': None, 
            'capex_ttm': None, 'eps_ttm': None, 'interest_expense_ttm': None
        }
        
        q_list = []
        for j in range(idx, -1, -1):
            q_list.append(j)
            if len(q_list) >= 4:
                break
        
        if len(q_list) >= 4:
            totals = {'net_profit': 0, 'total_revenue': 0, 'operating_cost': 0,
                     'net_profit_deducted': 0, 'operating_cash_flow': 0,
                     'capex': 0, 'eps': 0, 'interest_expense': 0}
            valid_count = {k: 0 for k in totals.keys()}
            
            for j_idx in q_list:
                q_cache = q_data_cache[j_idx]
                
                for k in totals.keys():
                    q_key = f"{k}_q"
                    q_val = q_cache.get(q_key)
                    if q_val is not None:
                        totals[k] += q_val
                        valid_count[k] += 1
            
            ttm_keys = {
                'net_profit': 'net_profit_ttm', 'total_revenue': 'total_revenue_ttm',
                'operating_cost': 'operating_cost_ttm', 'net_profit_deducted': 'net_profit_deducted_ttm',
                'operating_cash_flow': 'operating_cash_flow_ttm', 'capex': 'capex_ttm',
                'eps': 'eps_ttm', 'interest_expense': 'interest_expense_ttm'
            }
            for k, ttm_key in ttm_keys.items():
                if valid_count[k] >= 4:
                    ttm_result[ttm_key] = totals[k]
        
        return ttm_result
    
    def _calc_avg_data(self, df_finance, idx, mode='annual'):
        """计算期初+期末平均数据
        mode='annual': (当前FY + 上年FY) / 2，用于年报口径指标
        mode='ttm': (当前 + 4季度前) / 2，用于TTM口径指标
        """
        avg_result = {
            'avg_equity_parent': None, 'avg_total_assets': None,
            'avg_inventory': None, 'avg_accounts_receivable': None,
            'avg_accounts_payable': None
        }
        
        curr_row = df_finance.iloc[idx]
        curr_date = pd.to_datetime(curr_row['report_date'])
        curr_qt = curr_row['quarter_type']
        
        prev_idx = None
        
        if mode == 'annual':
            if curr_qt != 'FY':
                return avg_result
            for j in range(idx - 1, -1, -1):
                candidate_date = pd.to_datetime(df_finance.iloc[j]['report_date'])
                candidate_qt = df_finance.iloc[j]['quarter_type']
                if candidate_qt == 'FY' and candidate_date.year == curr_date.year - 1:
                    prev_idx = j
                    break
        else:
            for j in range(idx - 1, -1, -1):
                candidate_date = pd.to_datetime(df_finance.iloc[j]['report_date'])
                candidate_qt = df_finance.iloc[j]['quarter_type']
                months_back = (curr_date.year - candidate_date.year) * 12 + (curr_date.month - candidate_date.month)
                if candidate_qt == curr_qt and months_back >= 12:
                    prev_idx = j
                    break
        
        if prev_idx is not None:
            prev_row = df_finance.iloc[prev_idx]
            
            pairs = [
                ('equity_parent', 'avg_equity_parent'),
                ('total_assets', 'avg_total_assets'),
                ('inventory', 'avg_inventory'),
                ('accounts_receivable', 'avg_accounts_receivable'),
                ('accounts_payable', 'avg_accounts_payable')
            ]
            
            for col_name, avg_col in pairs:
                curr_val = self._to_float(curr_row.get(col_name))
                prev_val = self._to_float(prev_row.get(col_name))
                if curr_val is not None and prev_val is not None:
                    avg_result[avg_col] = (curr_val + prev_val) / 2
        
        return avg_result
    
    def _calc_growth_rates(self, df_finance, idx, q_data_cache, qt):
        """计算同比增速"""
        result = {
            'revenue_yoy_annual': None, 'net_profit_yoy_annual': None,
            'revenue_yoy_qoq': None, 'net_profit_yoy_qoq': None,
            'revenue_yoy_ttm': None, 'net_profit_yoy_ttm': None
        }
        
        curr_report_date = pd.to_datetime(df_finance.iloc[idx]['report_date'])
        target_year = curr_report_date.year - 1
        
        # 找去年同季度的数据
        last_year_idx = None
        for j in range(idx-1, -1, -1):
            candidate_date = pd.to_datetime(df_finance.iloc[j]['report_date'])
            candidate_qt = df_finance.iloc[j]['quarter_type']
            if candidate_date.year == target_year and candidate_qt == qt:
                last_year_idx = j
                break
        
        if last_year_idx is not None:
            # 年度同比（仅针对 FY）
            if qt == 'FY':
                curr_rev = self._to_float(df_finance.iloc[idx].get('total_revenue'))
                curr_profit = self._to_float(df_finance.iloc[idx].get('net_profit'))
                last_rev = self._to_float(df_finance.iloc[last_year_idx].get('total_revenue'))
                last_profit = self._to_float(df_finance.iloc[last_year_idx].get('net_profit'))
                
                if curr_rev is not None and last_rev is not None and last_rev != 0:
                    result['revenue_yoy_annual'] = round((float(curr_rev) - float(last_rev)) / float(last_rev) * 100, 4)
                if curr_profit is not None and last_profit is not None and last_profit != 0:
                    result['net_profit_yoy_annual'] = round((float(curr_profit) - float(last_profit)) / float(last_profit) * 100, 4)
            
            # 单季度同比
            curr_q_rev = q_data_cache[idx].get('total_revenue_q')
            curr_q_profit = q_data_cache[idx].get('net_profit_q')
            last_q_rev = q_data_cache[last_year_idx].get('total_revenue_q')
            last_q_profit = q_data_cache[last_year_idx].get('net_profit_q')
            
            if curr_q_rev is not None and last_q_rev is not None and last_q_rev != 0:
                result['revenue_yoy_qoq'] = round((float(curr_q_rev) - float(last_q_rev)) / float(last_q_rev) * 100, 4)
            if curr_q_profit is not None and last_q_profit is not None and last_q_profit != 0:
                result['net_profit_yoy_qoq'] = round((float(curr_q_profit) - float(last_q_profit)) / float(last_q_profit) * 100, 4)
            
            # TTM 同比 - 先计算去年的 TTM
            last_year_ttm_data = self._calc_ttm_data(df_finance, q_data_cache, last_year_idx)
            curr_ttm_data = self._calc_ttm_data(df_finance, q_data_cache, idx)
            
            curr_ttm_rev = curr_ttm_data.get('total_revenue_ttm')
            curr_ttm_profit = curr_ttm_data.get('net_profit_ttm')
            last_ttm_rev = last_year_ttm_data.get('total_revenue_ttm')
            last_ttm_profit = last_year_ttm_data.get('net_profit_ttm')
            
            if curr_ttm_rev is not None and last_ttm_rev is not None and last_ttm_rev != 0:
                result['revenue_yoy_ttm'] = round((float(curr_ttm_rev) - float(last_ttm_rev)) / float(last_ttm_rev) * 100, 4)
            if curr_ttm_profit is not None and last_ttm_profit is not None and last_ttm_profit != 0:
                result['net_profit_yoy_ttm'] = round((float(curr_ttm_profit) - float(last_ttm_profit)) / float(last_ttm_profit) * 100, 4)
        
        return result
    
    def _calc_cagr(self, df_finance, idx):
        """计算3年CAGR"""
        result = {
            'revenue_cagr_3y': None, 'net_profit_cagr_3y': None,
            'net_profit_deducted_cagr_3y': None
        }
        
        curr_report_date = pd.to_datetime(df_finance.iloc[idx]['report_date'])
        qt = df_finance.iloc[idx]['quarter_type']
        
        if qt != 'FY':
            return result
        
        target_year_3 = curr_report_date.year - 3
        idx_3y_ago = None
        
        for j in range(idx-1, -1, -1):
            candidate_date = pd.to_datetime(df_finance.iloc[j]['report_date'])
            candidate_qt = df_finance.iloc[j]['quarter_type']
            if candidate_date.year == target_year_3 and candidate_qt == 'FY':
                idx_3y_ago = j
                break
        
        if idx_3y_ago is not None:
            cols = [
                ('total_revenue', 'revenue_cagr_3y'),
                ('net_profit', 'net_profit_cagr_3y'),
                ('net_profit_deducted', 'net_profit_deducted_cagr_3y')
            ]
            
            for col_name, cagr_col in cols:
                curr_val = self._to_float(df_finance.iloc[idx].get(col_name))
                old_val = self._to_float(df_finance.iloc[idx_3y_ago].get(col_name))
                
                if curr_val is not None and old_val is not None and old_val > 0 and curr_val > 0:
                    cagr_val = (float(curr_val) / float(old_val)) ** (1/3) - 1
                    result[cagr_col] = round(cagr_val * 100, 4)
        
        return result
    
    def _calc_dupont(self, revenue, profit, avg_assets, avg_equity):
        """计算杜邦三层拆解"""
        result = {
            'net_margin': None, 'asset_turnover': None, 'equity_multiplier': None
        }
        
        if profit is not None and revenue is not None and revenue > 0:
            result['net_margin'] = round(float(profit) / float(revenue) * 100, 4)
        if revenue is not None and avg_assets is not None and avg_assets > 0:
            result['asset_turnover'] = round(float(revenue) / float(avg_assets), 4)
        if avg_assets is not None and avg_equity is not None and avg_equity > 0:
            result['equity_multiplier'] = round(float(avg_assets) / float(avg_equity), 4)
        
        return result
    
    def _calc_ops_data(self, cost, revenue, avg_inv, avg_ar, avg_ap):
        """计算营运能力指标
        cost/revenue: 年度累计或TTM合计
        avg_inv/avg_ar/avg_ap: 对应口径的平均资产
        """
        result = {
            'inventory_turnover': None, 'inventory_days': None,
            'accounts_receivable_turnover': None, 'accounts_receivable_days': None,
            'accounts_payable_turnover': None, 'accounts_payable_days': None,
            'cash_cycle': None
        }
        
        if cost is not None and avg_inv is not None and avg_inv > 0:
            result['inventory_turnover'] = round(float(cost) / float(avg_inv), 4)
            if result['inventory_turnover'] > 0:
                result['inventory_days'] = round(365 / result['inventory_turnover'], 4)
        
        if revenue is not None and avg_ar is not None and avg_ar > 0:
            result['accounts_receivable_turnover'] = round(float(revenue) / float(avg_ar), 4)
            if result['accounts_receivable_turnover'] > 0:
                result['accounts_receivable_days'] = round(365 / result['accounts_receivable_turnover'], 4)
        
        if cost is not None and avg_ap is not None and avg_ap > 0:
            result['accounts_payable_turnover'] = round(float(cost) / float(avg_ap), 4)
            if result['accounts_payable_turnover'] > 0:
                result['accounts_payable_days'] = round(365 / result['accounts_payable_turnover'], 4)
        
        if result['inventory_days'] is not None and result['accounts_receivable_days'] is not None and result['accounts_payable_days'] is not None:
            result['cash_cycle'] = round(result['inventory_days'] + result['accounts_receivable_days'] - result['accounts_payable_days'], 4)
        
        return result
    
    def _calc_cash_flow_data(self, ocf_ttm, profit_ttm, capex_ttm, interest_exp):
        """计算现金流质量指标"""
        result = {
            'cash_profit_coverage': None, 'fcf': None,
            'fcf_profit_coverage': None, 'cash_interest_coverage': None
        }
        
        if ocf_ttm is not None and profit_ttm is not None and profit_ttm != 0:
            result['cash_profit_coverage'] = round(float(ocf_ttm) / float(profit_ttm), 4)
        
        if ocf_ttm is not None and capex_ttm is not None:
            result['fcf'] = ocf_ttm - capex_ttm
        
        if result['fcf'] is not None and profit_ttm is not None and profit_ttm != 0:
            result['fcf_profit_coverage'] = round(float(result['fcf']) / float(profit_ttm), 4)
        
        if ocf_ttm is not None and interest_exp is not None and interest_exp != 0:
            result['cash_interest_coverage'] = round(float(ocf_ttm) / float(interest_exp), 4)
        
        return result
            
    def calculate_technical_indicators(self, stock_code: str):
        """计算技术指标（MA5/MA10/MA20/MA60）"""
        try:
            logger.info(f"开始计算 {stock_code} 技术指标")
            
            # 获取日线数据
            df = self.conn.execute("""
                SELECT trade_date, close 
                FROM stock_daily 
                WHERE stock_code = ? 
                ORDER BY trade_date
            """, (stock_code,)).fetchdf()
            
            if df.empty or len(df) < 5:
                logger.info(f"{stock_code} 日线数据不足，无法计算技术指标")
                return
            
            # 计算均线
            df = df.sort_values('trade_date').reset_index(drop=True)
            df['ma5'] = df['close'].rolling(window=5, min_periods=5).mean().round(2)
            df['ma10'] = df['close'].rolling(window=10, min_periods=10).mean().round(2)
            df['ma20'] = df['close'].rolling(window=20, min_periods=20).mean().round(2)
            df['ma60'] = df['close'].rolling(window=60, min_periods=60).mean().round(2)
            
            # 只保留有完整均线数据的行
            df = df.dropna(subset=['ma5', 'ma10', 'ma20', 'ma60'], how='all')
            
            if df.empty:
                logger.info(f"{stock_code} 没有可计算的技术指标数据")
                return
            
            df['stock_code'] = stock_code
            df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
            
            # 插入数据库
            self.conn.register("df_tech", df)
            self.conn.execute("""
            INSERT INTO technical_indicators (stock_code, trade_date, ma5, ma10, ma20, ma60)
            SELECT stock_code, trade_date, ma5, ma10, ma20, ma60
            FROM df_tech
            ON CONFLICT (stock_code, trade_date) DO UPDATE 
            SET ma5 = EXCLUDED.ma5, ma10 = EXCLUDED.ma10, ma20 = EXCLUDED.ma20, ma60 = EXCLUDED.ma60
            """)
            self.conn.unregister("df_tech")
            
            logger.info(f"{stock_code} 技术指标计算完成，共 {len(df)} 条记录")
            
        except Exception as e:
            logger.error(f"{stock_code} 技术指标计算失败：{str(e)}")
    
    def calculate_valuation_indicators(self, stock_code: str):
        """计算估值指标（完整财务处理规则实现）"""
        try:
            logger.info(f"开始计算 {stock_code} 估值指标")
            
            df_daily = self.conn.execute("""
                SELECT trade_date, close 
                FROM stock_daily 
                WHERE stock_code = ? 
                ORDER BY trade_date
            """, (stock_code,)).fetchdf()
            
            if df_daily.empty:
                logger.info(f"{stock_code} 日线数据为空")
                return
            
            df_inter = self.conn.execute("""
                SELECT report_date, report_type, eps, bvps, revenue_per_share, net_profit, total_revenue,
                       equity, equity_parent, announcement_date,
                       net_profit_ttm, total_revenue_ttm, eps_ttm,
                       roe_annual, roe_ttm, eps_q, net_profit_q, total_revenue_q,
                       total_shares
                FROM financial_intermediate 
                WHERE stock_code = ? 
                ORDER BY report_date
            """, (stock_code,)).fetchdf()
            
            one_year_ago = (datetime.now() - timedelta(days=365)).date()
            total_dividend = 0
            div_result = self.conn.execute("""
                SELECT COALESCE(SUM(cash_per_share), 0) 
                FROM dividends 
                WHERE stock_code = ? AND dividend_date >= ?
            """, (stock_code, one_year_ago)).fetchone()
            if div_result:
                total_dividend = div_result[0]
            
            valuation_data = []
            
            for _, daily_row in df_daily.iterrows():
                trade_date = daily_row['trade_date']
                close_price = self._to_float(daily_row.get('close'))
                
                if close_price is None or close_price == 0:
                    continue
                
                latest_inter = None
                used_report_date = None
                used_announcement_date = None
                
                if not df_inter.empty:
                    # 只使用公告日期 <= 交易日期的财报数据
                    # 如果没有公告日期，则回退到只检查 report_date
                    mask = pd.to_datetime(df_inter['report_date']) <= pd.to_datetime(trade_date)
                    if 'announcement_date' in df_inter.columns:
                        # 对于有公告日期的行，要求公告日期 <= 交易日期
                        # 对于没有公告日期的行，只要求 report_date <= 交易日期（回退逻辑）
                        announcement_date_check = pd.to_datetime(df_inter['announcement_date']) <= pd.to_datetime(trade_date)
                        mask = mask & (pd.to_datetime(df_inter['announcement_date']).isna() | announcement_date_check)
                    filtered = df_inter[mask]
                    if not filtered.empty:
                        latest_inter = filtered.iloc[-1]
                        used_report_date = latest_inter['report_date']
                        used_announcement_date = latest_inter.get('announcement_date')
                
                # 从最新的财务中间数据中获取已计算好的 TTM 和年度数据
                eps_ttm = None
                revenue_ttm_per_share = None
                eps_annual = None
                revenue_annual_per_share = None
                net_profit_ttm = None
                total_revenue_ttm = None
                roe_annual = None
                roe_ttm = None
                total_shares = None
                
                pb = None
                pe_ttm = None
                pe_annual = None
                ps_ttm = None
                ps_annual = None
                
                if latest_inter is not None:
                    bvps = self._to_float(latest_inter.get('bvps'))
                    eps_ttm = self._to_float(latest_inter.get('eps_ttm'))
                    eps_annual = self._to_float(latest_inter.get('eps'))
                    net_profit_ttm = self._to_float(latest_inter.get('net_profit_ttm'))
                    total_revenue_ttm = self._to_float(latest_inter.get('total_revenue_ttm'))
                    total_revenue_annual = self._to_float(latest_inter.get('total_revenue'))
                    total_shares = self._to_float(latest_inter.get('total_shares'))
                    roe_annual = self._to_float(latest_inter.get('roe_annual'))
                    roe_ttm = self._to_float(latest_inter.get('roe_ttm'))
                    
                    if total_shares is not None and total_shares > 0:
                        if total_revenue_ttm is not None:
                            revenue_ttm_per_share = round(float(total_revenue_ttm) / float(total_shares), 4)
                        if total_revenue_annual is not None:
                            revenue_annual_per_share = round(float(total_revenue_annual) / float(total_shares), 4)
                    
                    if bvps is not None and bvps > 0:
                        pb = round(close_price / bvps, 4)
                    
                    if eps_ttm is not None and eps_ttm > 0:
                        pe_ttm = round(close_price / eps_ttm, 4)
                    
                    if eps_annual is not None and eps_annual > 0:
                        pe_annual = round(close_price / eps_annual, 4)
                    
                    if revenue_ttm_per_share is not None and revenue_ttm_per_share > 0:
                        ps_ttm = round(close_price / revenue_ttm_per_share, 4)
                    
                    if revenue_annual_per_share is not None and revenue_annual_per_share > 0:
                        ps_annual = round(close_price / revenue_annual_per_share, 4)
                
                dividend_yield = None
                total_dividend_f = self._to_float(total_dividend)
                if total_dividend_f is not None and total_dividend_f > 0:
                    dividend_yield = round(total_dividend_f / close_price * 100, 4)
                
                valuation_data.append({
                    'stock_code': stock_code,
                    'trade_date': pd.to_datetime(trade_date).date(),
                    'pe_ttm': pe_ttm,
                    'pb': pb,
                    'ps_ttm': ps_ttm,
                    'dividend_yield': dividend_yield,
                    'roe': roe_ttm if roe_ttm is not None else roe_annual,
                    'pe_annual': pe_annual,
                    'ps_annual': ps_annual,
                    'roe_ttm': roe_ttm,
                    'roe_annual': roe_annual,
                    'used_report_date': pd.to_datetime(used_report_date).date() if used_report_date is not None else None,
                    'used_announcement_date': pd.to_datetime(used_announcement_date).date() if used_announcement_date is not None else None,
                })
            
            if not valuation_data:
                logger.info(f"{stock_code} 没有可计算的估值指标")
                return
            
            df_val = pd.DataFrame(valuation_data)
            
            self.conn.register("df_val", df_val)
            self.conn.execute("""
            INSERT INTO valuation_indicators 
            (stock_code, trade_date, pe_ttm, pb, ps_ttm, dividend_yield, roe,
             pe_annual, ps_annual, roe_ttm, roe_annual, used_report_date, used_announcement_date)
            SELECT stock_code, trade_date, pe_ttm, pb, ps_ttm, dividend_yield, roe,
                   pe_annual, ps_annual, roe_ttm, roe_annual, used_report_date, used_announcement_date
            FROM df_val
            ON CONFLICT (stock_code, trade_date) DO UPDATE 
            SET pe_ttm = EXCLUDED.pe_ttm, pb = EXCLUDED.pb, ps_ttm = EXCLUDED.ps_ttm, 
                dividend_yield = EXCLUDED.dividend_yield, roe = EXCLUDED.roe,
                pe_annual = EXCLUDED.pe_annual, ps_annual = EXCLUDED.ps_annual,
                roe_ttm = EXCLUDED.roe_ttm, roe_annual = EXCLUDED.roe_annual,
                used_report_date = EXCLUDED.used_report_date, used_announcement_date = EXCLUDED.used_announcement_date
            """)
            self.conn.unregister("df_val")
            
            logger.info(f"{stock_code} 估值指标计算完成，共 {len(df_val)} 条记录")
            
        except Exception as e:
            logger.error(f"{stock_code} 估值指标计算失败：{str(e)}")
    
    @staticmethod
    def _cumulative_to_quarterly(df):
        """将累计值财报数据转换为单季度数据"""
        if df.empty:
            return df
        
        df = df.copy()
        df = df.sort_values('report_date').reset_index(drop=True)
        
        eps_q = []
        rev_q = []
        
        for i, row in df.iterrows():
            report_date = pd.to_datetime(row['report_date'])
            month = report_date.month
            
            eps_cum = row.get('eps')
            rev_cum = row.get('revenue_per_share')
            
            if month == 3:
                eps_q.append(eps_cum)
                rev_q.append(rev_cum)
            else:
                prev_eps = None
                prev_rev = None
                for j in range(i - 1, -1, -1):
                    prev_date = pd.to_datetime(df.iloc[j]['report_date'])
                    if prev_date.year == report_date.year:
                        prev_eps = df.iloc[j].get('eps')
                        prev_rev = df.iloc[j].get('revenue_per_share')
                        break
                
                if prev_eps is not None and eps_cum is not None:
                    eps_q.append(eps_cum - prev_eps)
                else:
                    eps_q.append(eps_cum)
                
                if prev_rev is not None and rev_cum is not None:
                    rev_q.append(rev_cum - prev_rev)
                else:
                    rev_q.append(rev_cum)
        
        df['eps_q'] = eps_q
        df['revenue_per_share_q'] = rev_q
        return df
    
    def collect_northbound_flow(self, stock_code: str):
        """采集北向资金数据"""
        try:
            logger.info(f"开始采集 {stock_code} 北向资金数据")
            
            code = stock_code[2:]
            
            # 获取北向资金持股数据（使用 stock_hsgt_individual_em 接口）
            # 注: 原 stock_em_hsgt_hold_stock_em 接口已从 akshare 移除
            df = ak.stock_hsgt_individual_em(symbol=code)
            
            if df is None or df.empty:
                logger.info(f"{stock_code} 没有北向资金数据")
                return
            
            # 处理数据
            df = df.rename(columns={
                "持股日期": "trade_date",
                "持股数量": "holding_shares",
                "持股市值": "holding_value",
                "持股数量占A股百分比": "holding_ratio",
                "今日增持资金": "net_inflow",
            })
            
            df['stock_code'] = stock_code
            df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
            
            # 按日期排序
            df = df.sort_values('trade_date').reset_index(drop=True)
            
            # 计算累计净流入（首日"今日增持资金"为NaN，填充为0）
            if 'net_inflow' in df.columns:
                df['net_inflow'] = pd.to_numeric(df['net_inflow'], errors='coerce').fillna(0.0)
                df['inflow_5d'] = df['net_inflow'].rolling(5, min_periods=1).sum()
                df['inflow_10d'] = df['net_inflow'].rolling(10, min_periods=1).sum()
                df['inflow_30d'] = df['net_inflow'].rolling(30, min_periods=1).sum()
            
            # 插入数据库
            self.conn.register("df_nb", df)
            self.conn.execute("""
            INSERT INTO northbound_flow 
            (stock_code, trade_date, net_inflow, holding_shares, holding_value, holding_ratio,
             inflow_5d, inflow_10d, inflow_30d)
            SELECT stock_code, trade_date, net_inflow, holding_shares, holding_value, holding_ratio,
                   inflow_5d, inflow_10d, inflow_30d
            FROM df_nb
            ON CONFLICT (stock_code, trade_date) DO UPDATE 
            SET net_inflow = EXCLUDED.net_inflow,
                holding_shares = EXCLUDED.holding_shares,
                holding_value = EXCLUDED.holding_value,
                holding_ratio = EXCLUDED.holding_ratio,
                inflow_5d = EXCLUDED.inflow_5d,
                inflow_10d = EXCLUDED.inflow_10d,
                inflow_30d = EXCLUDED.inflow_30d
            """)
            self.conn.unregister("df_nb")
            
            logger.info(f"{stock_code} 北向资金数据采集完成，共 {len(df)} 条记录")
            
        except Exception as e:
            logger.error(f"{stock_code} 北向资金数据采集失败：{str(e)}")
    
    def collect_margin_trading(self, stock_code: str):
        """采集融资融券数据"""
        try:
            logger.info(f"开始采集 {stock_code} 融资融券数据")
            
            code = stock_code[2:]
            
            # 获取融资融券数据
            df = ak.stock_margin_sse(symbol=code)
            
            if df.empty:
                logger.info(f"{stock_code} 没有融资融券数据")
                return
            
            # 处理数据
            df = df.rename(columns={
                "日期": "trade_date",
                "融资余额": "rz_balance",
                "融券余额": "rq_balance"
            })
            
            df['stock_code'] = stock_code
            df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
            
            # 按日期排序
            df = df.sort_values('trade_date').reset_index(drop=True)
            
            # 计算变动
            if 'rz_balance' in df.columns:
                df['rz_change'] = df['rz_balance'].diff()
                df['rz_change_pct'] = df['rz_change'] / df['rz_balance'].shift(1) * 100
            
            if 'rq_balance' in df.columns:
                df['rq_change'] = df['rq_balance'].diff()
                df['rq_change_pct'] = df['rq_change'] / df['rq_balance'].shift(1) * 100
            
            # 计算总额
            df['total_balance'] = df.get('rz_balance', 0) + df.get('rq_balance', 0)
            df['total_change'] = df.get('rz_change', 0) + df.get('rq_change', 0)
            df['total_change_pct'] = df['total_change'] / df['total_balance'].shift(1) * 100
            
            # 插入数据库
            self.conn.register("df_mt", df)
            self.conn.execute("""
            INSERT INTO margin_trading 
            (stock_code, trade_date, rz_balance, rz_change, rz_change_pct,
             rq_balance, rq_change, rq_change_pct,
             total_balance, total_change, total_change_pct)
            SELECT stock_code, trade_date, rz_balance, rz_change, rz_change_pct,
                   rq_balance, rq_change, rq_change_pct,
                   total_balance, total_change, total_change_pct
            FROM df_mt
            ON CONFLICT (stock_code, trade_date) DO UPDATE 
            SET rz_balance = EXCLUDED.rz_balance,
                rz_change = EXCLUDED.rz_change,
                rz_change_pct = EXCLUDED.rz_change_pct,
                rq_balance = EXCLUDED.rq_balance,
                rq_change = EXCLUDED.rq_change,
                rq_change_pct = EXCLUDED.rq_change_pct,
                total_balance = EXCLUDED.total_balance,
                total_change = EXCLUDED.total_change,
                total_change_pct = EXCLUDED.total_change_pct
            """)
            self.conn.unregister("df_mt")
            
            logger.info(f"{stock_code} 融资融券数据采集完成，共 {len(df)} 条记录")
            
        except Exception as e:
            logger.error(f"{stock_code} 融资融券数据采集失败：{str(e)}")
    
    def collect_dragon_tiger(self, stock_code: str):
        """采集龙虎榜数据"""
        try:
            logger.info(f"开始采集 {stock_code} 龙虎榜数据")
            
            code = stock_code[2:]
            today = datetime.now().strftime("%Y%m%d")
            
            # 获取龙虎榜数据
            df = ak.stock_zh_a_hist_em(symbol=code, period="daily", start_date="20150101", end_date=today, adjust="")
            
            if df.empty:
                logger.info(f"{stock_code} 没有日线数据")
                return
            
            # 获取龙虎榜数据
            try:
                df_lhb = ak.stock_em_lhb_detail_daily(symbol="全部", date=datetime.now().strftime("%Y%m%d"))
                
                if not df_lhb.empty:
                    # 过滤出目标股票
                    df_lhb = df_lhb[df_lhb['代码'].astype(str) == code]
                    
                    if not df_lhb.empty:
                        for _, row in df_lhb.iterrows():
                            try:
                                # 插入主表
                                result = self.conn.execute("""
                                INSERT INTO dragon_tiger 
                                (stock_code, trade_date, list_type, reason, buy_amount, sell_amount, net_amount)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT (stock_code, trade_date, list_type) DO UPDATE SET
                                    reason = EXCLUDED.reason,
                                    buy_amount = EXCLUDED.buy_amount,
                                    sell_amount = EXCLUDED.sell_amount,
                                    net_amount = EXCLUDED.net_amount
                                RETURNING id
                                """, (
                                    stock_code,
                                    pd.to_datetime(row.get('日期')).date() if row.get('日期') else None,
                                    row.get('类型'),
                                    row.get('上榜原因'),
                                    self._to_float(row.get('买入额')),
                                    self._to_float(row.get('卖出额')),
                                    self._to_float(row.get('净买入'))
                                ))
                                
                                dragon_tiger_id = result.fetchone()[0] if result else None
                                
                                logger.info(f"{stock_code} 龙虎榜数据采集完成")
                            except Exception as e:
                                logger.error(f"{stock_code} 龙虎榜单条数据插入失败：{str(e)}")
            except Exception as e:
                logger.debug(f"获取龙虎榜详细数据失败：{str(e)}")
            
        except Exception as e:
            logger.error(f"{stock_code} 龙虎榜数据采集失败：{str(e)}")
    
    def calculate_northbound_accumulation(self, stock_code: str):
        """计算北向资金累计流入（5/10/30日）"""
        try:
            logger.info(f"开始计算 {stock_code} 北向资金累计流入")
            
            df = self.conn.execute("""
            SELECT stock_code, trade_date, net_inflow
            FROM northbound_flow
            WHERE stock_code = ?
            ORDER BY trade_date
            """, (stock_code,)).fetchdf()
            
            if df.empty or len(df) < 5:
                logger.info(f"{stock_code} 北向资金数据不足")
                return
            
            df = df.sort_values('trade_date').reset_index(drop=True)
            
            # 计算累计
            df['inflow_5d'] = df['net_inflow'].rolling(5, min_periods=1).sum()
            df['inflow_10d'] = df['net_inflow'].rolling(10, min_periods=1).sum()
            df['inflow_30d'] = df['net_inflow'].rolling(30, min_periods=1).sum()
            
            # 更新数据库
            self.conn.register("df_nb_acc", df)
            self.conn.execute("""
            INSERT INTO northbound_flow 
            (stock_code, trade_date, net_inflow, inflow_5d, inflow_10d, inflow_30d)
            SELECT stock_code, trade_date, net_inflow, inflow_5d, inflow_10d, inflow_30d
            FROM df_nb_acc
            ON CONFLICT (stock_code, trade_date) DO UPDATE 
            SET inflow_5d = EXCLUDED.inflow_5d,
                inflow_10d = EXCLUDED.inflow_10d,
                inflow_30d = EXCLUDED.inflow_30d
            """)
            self.conn.unregister("df_nb_acc")
            
            logger.info(f"{stock_code} 北向资金累计流入计算完成")
            
        except Exception as e:
            logger.error(f"{stock_code} 北向资金累计流入计算失败：{str(e)}")
            
    def collect_all(self):
        """采集所有配置股票的所有数据"""
        logger.info("开始全量数据采集")
        
        for stock_code in STOCK_CODES:
            logger.info(f"===== 开始处理 {stock_code} =====")
            # 1. 采集基础数据
            self.collect_daily_data(stock_code)
            self.collect_financial_data(stock_code)
            self.collect_announcements(stock_code)
            self.collect_capital_data(stock_code)
            self.collect_dividend_data(stock_code)
            
            # 2. 采集交易数据
            self.collect_northbound_flow(stock_code)
            self.collect_margin_trading(stock_code)
            self.collect_dragon_tiger(stock_code)
            
            # 3. 计算财务中间指标
            self.calculate_financial_intermediate(stock_code)
            
            # 4. 计算技术指标
            self.calculate_technical_indicators(stock_code)
            self._calculate_technical_indicators(stock_code)
            
            # 5. 计算估值指标
            self.calculate_valuation_indicators(stock_code)
            
            # 6. 计算北向资金累计流入
            self.calculate_northbound_accumulation(stock_code)
            
            logger.info(f"===== {stock_code} 处理完成 =====")
        
        logger.info("全量数据采集完成")
        
    def close(self):
        """关闭数据库连接"""
        self.conn.close()
        logger.info("数据库连接已关闭")

if __name__ == "__main__":
    # 创建采集器实例
    collector = StockDataCollector(DB_PATH)
    
    try:
        # 执行全量采集
        collector.collect_all()
    finally:
        # 确保数据库连接关闭
        collector.close()