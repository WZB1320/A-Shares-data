
import duckdb
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def init_tables(conn):
    conn.execute("""
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
        turnover DECIMAL(10,6),
        outstanding_share DECIMAL(20,0),
        PRIMARY KEY (stock_code, trade_date)
    )
    """)

    conn.execute("""
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
        current_assets DECIMAL(18,2),
        current_liabilities DECIMAL(18,2),
        PRIMARY KEY (stock_code, report_date, report_type)
    )
    """)

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
    CREATE TABLE IF NOT EXISTS technical_indicators (
        stock_code VARCHAR(10) NOT NULL,
        trade_date DATE NOT NULL,
        ma5 DECIMAL(10,2),
        ma10 DECIMAL(10,2),
        ma20 DECIMAL(10,2),
        ma60 DECIMAL(10,2),
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
        rsi6 DECIMAL(10,4),
        rsi12 DECIMAL(10,4),
        rsi24 DECIMAL(10,4),
        kdj_k DECIMAL(10,4),
        kdj_d DECIMAL(10,4),
        kdj_j DECIMAL(10,4),
        cci20 DECIMAL(10,4),
        wr14 DECIMAL(10,4),
        atr14 DECIMAL(10,4),
        std20 DECIMAL(10,4),
        vol_ma5 DECIMAL(20,0),
        vol_ma10 DECIMAL(20,0),
        obv DECIMAL(20,0),
        mfi14 DECIMAL(10,4),
        vr DECIMAL(10,4),
        dmi_pdi DECIMAL(10,4),
        dmi_mdi DECIMAL(10,4),
        dmi_adx DECIMAL(10,4),
        dmi_adxr DECIMAL(10,4),
        sar DECIMAL(10,2),
        wvad DECIMAL(18,4),
        PRIMARY KEY (stock_code, trade_date)
    )
    """)

    conn.execute("""
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

    conn.execute("""
    CREATE TABLE IF NOT EXISTS stock_capital (
        stock_code VARCHAR(10) NOT NULL,
        record_date DATE NOT NULL,
        total_shares DECIMAL(20,0),
        PRIMARY KEY (stock_code, record_date)
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS dividends (
        stock_code VARCHAR(10) NOT NULL,
        dividend_date DATE NOT NULL,
        cash_per_share DECIMAL(10,4),
        announcement_date DATE,
        PRIMARY KEY (stock_code, dividend_date)
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS financial_intermediate (
        stock_code VARCHAR(10) NOT NULL,
        report_date DATE NOT NULL,
        report_type VARCHAR(20) NOT NULL,
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
        net_profit_q DECIMAL(18,2),
        total_revenue_q DECIMAL(18,2),
        eps_q DECIMAL(10,4),
        operating_cost_q DECIMAL(18,2),
        net_profit_deducted_q DECIMAL(18,2),
        operating_cash_flow_q DECIMAL(18,2),
        capex_q DECIMAL(18,2),
        net_profit_ttm DECIMAL(18,2),
        total_revenue_ttm DECIMAL(18,2),
        eps_ttm DECIMAL(10,4),
        operating_cost_ttm DECIMAL(18,2),
        net_profit_deducted_ttm DECIMAL(18,2),
        operating_cash_flow_ttm DECIMAL(18,2),
        capex_ttm DECIMAL(18,2),
        interest_expense_ttm DECIMAL(18,2),
        avg_equity_parent DECIMAL(18,2),
        avg_total_assets DECIMAL(18,2),
        avg_inventory DECIMAL(18,2),
        avg_accounts_receivable DECIMAL(18,2),
        avg_accounts_payable DECIMAL(18,2),
        avg_equity_parent_ttm DECIMAL(18,2),
        avg_total_assets_ttm DECIMAL(18,2),
        avg_inventory_ttm DECIMAL(18,2),
        avg_accounts_receivable_ttm DECIMAL(18,2),
        avg_accounts_payable_ttm DECIMAL(18,2),
        roe_annual DECIMAL(10,4),
        roa_annual DECIMAL(10,4),
        gross_margin_annual DECIMAL(10,4),
        net_margin_parent_annual DECIMAL(10,4),
        net_margin_deducted_annual DECIMAL(10,4),
        roe_ttm DECIMAL(10,4),
        roa_ttm DECIMAL(10,4),
        gross_margin_ttm DECIMAL(10,4),
        net_margin_parent_ttm DECIMAL(10,4),
        net_margin_deducted_ttm DECIMAL(10,4),
        revenue_yoy_annual DECIMAL(10,4),
        net_profit_yoy_annual DECIMAL(10,4),
        revenue_yoy_qoq DECIMAL(10,4),
        net_profit_yoy_qoq DECIMAL(10,4),
        revenue_yoy_ttm DECIMAL(10,4),
        net_profit_yoy_ttm DECIMAL(10,4),
        revenue_cagr_3y DECIMAL(10,4),
        net_profit_cagr_3y DECIMAL(10,4),
        net_profit_deducted_cagr_3y DECIMAL(10,4),
        dupont_net_margin_annual DECIMAL(10,4),
        dupont_asset_turnover_annual DECIMAL(10,4),
        dupont_equity_multiplier_annual DECIMAL(10,4),
        dupont_net_margin_ttm DECIMAL(10,4),
        dupont_asset_turnover_ttm DECIMAL(10,4),
        dupont_equity_multiplier_ttm DECIMAL(10,4),
        inventory_turnover_annual DECIMAL(18,4),
        inventory_days_annual DECIMAL(18,4),
        accounts_receivable_turnover_annual DECIMAL(18,4),
        accounts_receivable_days_annual DECIMAL(18,4),
        accounts_payable_turnover_annual DECIMAL(18,4),
        accounts_payable_days_annual DECIMAL(18,4),
        cash_cycle_annual DECIMAL(18,4),
        inventory_turnover_ttm DECIMAL(18,4),
        inventory_days_ttm DECIMAL(18,4),
        accounts_receivable_turnover_ttm DECIMAL(18,4),
        accounts_receivable_days_ttm DECIMAL(18,4),
        accounts_payable_turnover_ttm DECIMAL(18,4),
        accounts_payable_days_ttm DECIMAL(18,4),
        cash_cycle_ttm DECIMAL(18,4),
        cash_profit_coverage_ttm DECIMAL(18,4),
        fcf_ttm DECIMAL(18,2),
        fcf_profit_coverage_ttm DECIMAL(18,4),
        cash_interest_coverage_ttm DECIMAL(18,4),
        current_ratio DECIMAL(10,4),
        quick_ratio DECIMAL(10,4),
        PRIMARY KEY (stock_code, report_date, report_type)
    )
    """)

    conn.execute("""
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

    # 北向资金整体流向(沪股通+深股通+北向合计)
    # 数据源: ak.stock_hsgt_hist_em, 数据到2026年最新, 弥补个股接口只到2024-08的问题
    conn.execute("""
    CREATE TABLE IF NOT EXISTS northbound_market_flow (
        trade_date DATE NOT NULL PRIMARY KEY,
        net_buy_amount DECIMAL(18,4),
        buy_amount DECIMAL(18,4),
        sell_amount DECIMAL(18,4),
        cumulative_net_buy DECIMAL(18,4),
        daily_inflow DECIMAL(18,4),
        daily_balance DECIMAL(18,4),
        holding_market_value DECIMAL(18,4)
    )
    """)

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
    CREATE TABLE IF NOT EXISTS stock_industry (
        stock_code VARCHAR(10) NOT NULL,
        industry_name VARCHAR(100),
        industry_level VARCHAR(20),
        source VARCHAR(50),
        update_date DATE NOT NULL,
        PRIMARY KEY (stock_code)
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS column_metadata (
        table_name VARCHAR(50) NOT NULL,
        column_name VARCHAR(50) NOT NULL,
        description VARCHAR(255),
        unit VARCHAR(20),
        PRIMARY KEY (table_name, column_name)
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS stock_master (
        stock_code VARCHAR(10) PRIMARY KEY,
        stock_name VARCHAR(50) NOT NULL,
        stock_name_cn VARCHAR(100),
        market VARCHAR(10),
        board VARCHAR(20),
        listing_date DATE,
        delisting_date DATE,
        status VARCHAR(10) DEFAULT '正常',
        is_etf BOOLEAN DEFAULT FALSE,
        is_index BOOLEAN DEFAULT FALSE,
        added_date DATE DEFAULT CURRENT_DATE,
        notes VARCHAR(255)
    )
    """)

    logger.info("数据库表结构初始化完成")
