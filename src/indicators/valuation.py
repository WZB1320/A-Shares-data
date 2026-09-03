import pandas as pd
import logging
from datetime import datetime, timedelta
from .base import BaseIndicatorCalculator

logger = logging.getLogger(__name__)


class ValuationIndicatorCalculator(BaseIndicatorCalculator):
    def calculate_for_stock(self, stock_code: str):
        logger.info(f"计算 {stock_code} 估值指标")

        df_daily = self.db_ops.query("""
            SELECT stock_code, trade_date, close, volume
            FROM stock_daily
            WHERE stock_code = ?
            ORDER BY trade_date
        """, (stock_code,))

        if df_daily.empty:
            return

        df_financial = self.db_ops.query("""
            SELECT report_date, report_type, net_profit, total_revenue, equity_parent,
                   net_profit_ttm, total_revenue_ttm, roe_ttm, roe_annual, announcement_date
            FROM financial_intermediate
            WHERE stock_code = ?
            ORDER BY report_date DESC
        """, (stock_code,))

        # 找到最近年报(FY)的净利润和营收, 用于计算静态PE/PS
        # 注意: report_type 全部是"合并期末", 无法区分年报/季报, 用 report_date 的月份判断
        annual_net_profit = None
        annual_total_revenue = None
        for _, fr in df_financial.iterrows():
            rd = fr.get('report_date')
            if rd is None:
                continue
            rd_dt = pd.to_datetime(rd)
            if rd_dt.month == 12 and rd_dt.day == 31:
                np_val = self._safe_float(fr.get('net_profit'))
                tr_val = self._safe_float(fr.get('total_revenue'))
                if np_val is not None:
                    annual_net_profit = np_val
                if tr_val is not None:
                    annual_total_revenue = tr_val
                break

        df_capital = self.db_ops.query("""
            SELECT record_date, total_shares
            FROM stock_capital
            WHERE stock_code = ?
            ORDER BY record_date DESC
            LIMIT 1
        """, (stock_code,))

        total_shares = None
        if not df_capital.empty:
            total_shares = self._safe_float(df_capital.iloc[0]['total_shares'])

        one_year_ago = (datetime.now() - timedelta(days=365)).date()
        total_dividend = 0
        div_result = self.db_ops.conn.execute("""
            SELECT COALESCE(SUM(cash_per_share), 0) 
            FROM dividends 
            WHERE stock_code = ? AND dividend_date >= ?
        """, (stock_code, one_year_ago)).fetchone()
        if div_result:
            total_dividend = div_result[0]

        valuation_data = []

        for _, row in df_daily.iterrows():
            trade_date = row['trade_date']
            close = self._safe_float(row.get('close'))

            if close is None or total_shares is None or total_shares == 0:
                continue

            pe_ttm = None
            pb = None
            ps_ttm = None
            roe = None
            pe_annual = None
            ps_annual = None
            roe_ttm = None
            roe_annual = None
            used_report_date = None
            used_announcement_date = None

            for _, fin_row in df_financial.iterrows():
                ann_date = fin_row.get('announcement_date')
                if ann_date is None:
                    continue
                ann_date_pd = pd.to_datetime(ann_date)
                trade_date_pd = pd.to_datetime(trade_date)
                if ann_date_pd > trade_date_pd:
                    continue
                used_report_date = fin_row['report_date']
                used_announcement_date = ann_date
                net_profit_ttm = self._safe_float(fin_row.get('net_profit_ttm'))
                total_revenue_ttm = self._safe_float(fin_row.get('total_revenue_ttm'))
                equity_parent = self._safe_float(fin_row.get('equity_parent'))
                roe_ttm_val = self._safe_float(fin_row.get('roe_ttm'))
                roe_annual_val = self._safe_float(fin_row.get('roe_annual'))
                net_profit = self._safe_float(fin_row.get('net_profit'))
                total_revenue = self._safe_float(fin_row.get('total_revenue'))

                market_cap = close * total_shares

                if net_profit_ttm is not None and net_profit_ttm != 0:
                    pe_ttm = market_cap / net_profit_ttm

                if equity_parent is not None and equity_parent != 0:
                    pb = market_cap / equity_parent

                if total_revenue_ttm is not None and total_revenue_ttm != 0:
                    ps_ttm = market_cap / total_revenue_ttm

                roe = roe_ttm_val
                roe_ttm = roe_ttm_val
                roe_annual = roe_annual_val

                # 静态PE/PS: 用最近年报净利润/营收, 而非当前报告期累计值
                # 避免Q1报告期用1-3月净利润导致PE偏高4倍
                if annual_net_profit is not None and annual_net_profit != 0:
                    pe_annual = market_cap / annual_net_profit

                if annual_total_revenue is not None and annual_total_revenue != 0:
                    ps_annual = market_cap / annual_total_revenue

                break

            if pe_ttm is None and pb is None and ps_ttm is None:
                continue

            dividend_yield = None
            total_dividend_f = self._safe_float(total_dividend)
            if total_dividend_f is not None and total_dividend_f > 0 and close is not None and close > 0:
                dividend_yield = round(total_dividend_f / close * 100, 4)

            valuation_data.append({
                'stock_code': stock_code,
                'trade_date': trade_date,
                'pe_ttm': pe_ttm,
                'pb': pb,
                'ps_ttm': ps_ttm,
                'dividend_yield': dividend_yield,
                'roe': roe,
                'pe_annual': pe_annual,
                'ps_annual': ps_annual,
                'roe_ttm': roe_ttm,
                'roe_annual': roe_annual,
                'used_report_date': used_report_date,
                'used_announcement_date': used_announcement_date,
            })

        df_valuation = pd.DataFrame(valuation_data)

        # 事务保证: DELETE + INSERT 原子化
        # 若无事务,DELETE 成功后 INSERT 失败会导致该股票所有估值指标丢失
        with self.db_ops.transaction():
            self.db_ops.conn.execute("DELETE FROM valuation_indicators WHERE stock_code = ?", (stock_code,))
            self.db_ops.insert_dataframe("valuation_indicators", df_valuation, ["stock_code", "trade_date"])

        logger.info(f"{stock_code} 估值指标完成")