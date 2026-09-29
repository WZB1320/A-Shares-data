"""财务数据采集服务 - 三级降级链

降级链:
- 主源 (AKShare Sina): 三大报表, 字段最全 (覆盖 19 个核心字段)
- 中备 (AKShare EM): 东方财富EM接口, 字段几乎与主源同等全 (覆盖 18 个字段)
- 末备 (BaoStock): 仅 net_profit + total_revenue 2 字段, 且 net_profit 为非归母口径

降级触发: 主源抛异常 -> 中备 -> 末备 (由调用方通过 BaseCollector.collect_with_fallback 编排)

数据治理约束:
- 中备/末备 upsert 均用 COALESCE(EXCLUDED.x, financial_statements.x) 保护已有非空字段
- 末备 net_profit 为非归母口径 (baostock 不提供归母净利润), 仅在主源+中备都失败时兜底
- BaoStock 现金流接口只有比率, 无原始金额, 末备不写 operating_cash_flow
"""
import akshare as ak
import baostock as bs
import pandas as pd
import logging
from datetime import datetime
from .base import BaseCollector
from .baostock_collector import _bs_global_lock

logger = logging.getLogger(__name__)


class FinancialService(BaseCollector):
    """财务数据采集服务: 主源 AKShare Sina, 中备 AKShare EM, 末备 BaoStock"""

    def __init__(self, db_ops, start_date: str):
        super().__init__(db_ops, start_date)

    def collect_stock(self, stock_code: str):
        # 不直接对外使用, 由 MultiSourceCollector 编排
        raise NotImplementedError("FinancialService 通过 collect_from_akshare / collect_from_akshare_em / collect_from_baostock 调用")

    # ============ 主源: AKShare Sina ============

    def collect_from_akshare(self, stock_code: str):
        """主源: 通过 AKShare Sina 采集三大报表数据

        覆盖 financial_statements 表 19 个字段 (current_assets/current_liabilities 由
        update_balance_sheet_fields 补充, 故主源不直接写这两列)
        """
        last_update = self.db_ops.get_last_update_date(stock_code, "financial", self.start_date)
        today_fmt = datetime.now().strftime("%Y-%m-%d")

        logger.info(f"[akshare] 开始采集 {stock_code} 财务数据")
        income_df = ak.stock_financial_report_sina(stock=stock_code, symbol="利润表")
        balance_df = ak.stock_financial_report_sina(stock=stock_code, symbol="资产负债表")
        cashflow_df = ak.stock_financial_report_sina(stock=stock_code, symbol="现金流量表")

        income_df["报告日"] = pd.to_datetime(income_df["报告日"])
        balance_df["报告日"] = pd.to_datetime(balance_df["报告日"])
        cashflow_df["报告日"] = pd.to_datetime(cashflow_df["报告日"])

        merged = income_df[["报告日", "类型"]].drop_duplicates()

        income_map = income_df.set_index(["报告日", "类型"])
        balance_map = balance_df.set_index(["报告日", "类型"])
        cashflow_map = cashflow_df.set_index(["报告日", "类型"])

        announcement_date_map = {}
        for _, row in income_df.iterrows():
            key = (row["报告日"], row["类型"])
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
                data["interest_expense"] = self._safe_get(ir, "利息费用")
                if data["interest_expense"] is None:
                    data["interest_expense"] = self._safe_get(ir, "利息支出")

            if key in balance_map.index:
                br = balance_map.loc[key]
                data["total_assets"] = self._safe_get(br, "资产总计")
                data["total_liabilities"] = self._safe_get(br, "负债合计")
                data["equity_parent"] = self._safe_get(br, "归属于母公司股东权益合计")
                data["inventory"] = self._safe_get(br, "存货")
                data["accounts_receivable"] = self._safe_get(br, "应收账款")
                data["accounts_payable"] = self._safe_get(br, "应付账款")

            if key in cashflow_map.index:
                cr = cashflow_map.loc[key]
                data["operating_cash_flow"] = self._safe_get(cr, "经营活动产生的现金流量净额")
                data["capex"] = self._safe_get(cr, "购建固定资产、无形资产和其他长期资产支付的现金")

            if key in announcement_date_map:
                data["announcement_date"] = announcement_date_map[key]

            if data["total_assets"] and data["total_liabilities"]:
                equity = data["total_assets"] - data["total_liabilities"]
                if equity and equity != 0 and data["net_profit"]:
                    data["roe"] = round(data["net_profit"] / equity * 100, 4)

            financial_data.append(data)

        df = pd.DataFrame(financial_data)
        if df.empty:
            return

        df["report_date"] = pd.to_datetime(df["report_date"]).dt.date
        df["announcement_date"] = pd.to_datetime(df["announcement_date"], errors="coerce").dt.date

        # 事务保证: 财务数据写入 + 水位更新 原子化
        try:
            with self.db_ops.transaction():
                self._upsert_financial(df)
                self.db_ops.update_last_update_date(stock_code, "financial", today_fmt)
            logger.info(f"[akshare] {stock_code} 财务数据完成，新增 {len(df)} 条")
        except Exception as e:
            logger.error(f"[akshare] {stock_code} 财务数据写入失败,已回滚: {e}")
            raise

    def update_missing_financial_fields(self, stock_code: str):
        """补充: 通过东方财富EM补充扣非净利润、利息支出等字段"""
        try:
            if stock_code.startswith('sh'):
                em_code = 'SH' + stock_code[2:]
            elif stock_code.startswith('sz'):
                em_code = 'SZ' + stock_code[2:]
            else:
                em_code = stock_code.upper()

            df = ak.stock_profit_sheet_by_report_em(symbol=em_code)
            df['report_date'] = pd.to_datetime(df['REPORT_DATE']).dt.date

            # 事务保证: 所有补充字段的批量 UPDATE 原子化
            with self.db_ops.transaction():
                for _, row in df.iterrows():
                    rd = row['report_date']

                    deducted = row.get('DEDUCT_PARENT_NETPROFIT')
                    if pd.notna(deducted):
                        self.db_ops.conn.execute("""
                            UPDATE financial_statements
                            SET net_profit_deducted = ?
                            WHERE stock_code = ? AND report_date = ?
                        """, [float(deducted), stock_code, rd])

                    existing = self.db_ops.conn.execute("""
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
                            self.db_ops.conn.execute("""
                                UPDATE financial_statements
                                SET interest_expense = ?
                                WHERE stock_code = ? AND report_date = ?
                            """, [float(interest), stock_code, rd])
        except Exception as e:
            logger.warning(f"[akshare] {stock_code} 东方财富补充采集失败：{e}")

    def update_balance_sheet_fields(self, stock_code: str):
        """补充: 通过东方财富EM资产负债表补充流动资产/流动负债字段

        数据源: ak.stock_balance_sheet_by_report_em
        补充字段: current_assets(TOTAL_CURRENT_ASSETS), current_liabilities(TOTAL_CURRENT_LIAB)
        """
        from .rate_limiter import akshare_rate_limited

        @akshare_rate_limited
        def _fetch(code):
            return ak.stock_balance_sheet_by_report_em(symbol=code)

        try:
            if stock_code.startswith('sh'):
                em_code = 'SH' + stock_code[2:]
            else:
                em_code = 'SZ' + stock_code[2:]

            df = _fetch(em_code)
            if df is None or df.empty:
                logger.info(f"[akshare] {stock_code} 无资产负债表数据")
                return

            df['report_date'] = pd.to_datetime(df['REPORT_DATE']).dt.date

            # 事务保证: 流动资产/负债批量 UPDATE 原子化
            with self.db_ops.transaction():
                for _, row in df.iterrows():
                    rd = row['report_date']
                    current_assets = row.get('TOTAL_CURRENT_ASSETS')
                    current_liab = row.get('TOTAL_CURRENT_LIAB')

                    if pd.notna(current_assets) or pd.notna(current_liab):
                        self.db_ops.conn.execute("""
                            UPDATE financial_statements
                            SET current_assets = ?,
                                current_liabilities = ?
                            WHERE stock_code = ? AND report_date = ?
                        """, [
                            float(current_assets) if pd.notna(current_assets) else None,
                            float(current_liab) if pd.notna(current_liab) else None,
                            stock_code, rd
                        ])
            logger.info(f"[akshare] {stock_code} 资产负债表字段补充完成")
        except Exception as e:
            logger.error(f"[akshare] {stock_code} 资产负债表字段补充失败: {e}")

    # ============ 中备源: AKShare EM (字段最全) ============

    def collect_from_akshare_em(self, stock_code: str):
        """中备: 通过 AKShare 东方财富EM接口采集利润表 + 资产负债表 + 现金流量表

        字段覆盖 (18 个, 与主源接近):
        利润表: total_revenue, net_profit(归母), net_profit_deducted, operating_cost, interest_expense, eps
        资产负债表: total_assets, total_liabilities, equity_parent, current_assets, current_liabilities,
                   inventory, accounts_receivable, accounts_payable
        现金流量表: operating_cash_flow, capex

        数据源: ak.stock_profit_sheet_by_report_em / stock_balance_sheet_by_report_em / stock_cash_flow_sheet_by_report_em
        """
        from .rate_limiter import akshare_rate_limited

        @akshare_rate_limited
        def _fetch_profit(code):
            return ak.stock_profit_sheet_by_report_em(symbol=code)

        @akshare_rate_limited
        def _fetch_balance(code):
            return ak.stock_balance_sheet_by_report_em(symbol=code)

        @akshare_rate_limited
        def _fetch_cashflow(code):
            return ak.stock_cash_flow_sheet_by_report_em(symbol=code)

        if stock_code.startswith('sh'):
            em_code = 'SH' + stock_code[2:]
        elif stock_code.startswith('sz'):
            em_code = 'SZ' + stock_code[2:]
        else:
            em_code = stock_code.upper()

        logger.info(f"[akshare_em] 开始采集 {stock_code} 财务数据 (中备)")

        # 利润表
        profit_df = _fetch_profit(em_code)
        # 资产负债表
        balance_df = _fetch_balance(em_code)
        # 现金流量表
        try:
            cashflow_df = _fetch_cashflow(em_code)
        except Exception as e:
            logger.warning(f"[akshare_em] {stock_code} 现金流量表获取失败, 跳过: {e}")
            cashflow_df = pd.DataFrame()

        if profit_df is None or profit_df.empty:
            logger.warning(f"[akshare_em] {stock_code} 利润表为空")
            raise RuntimeError(f"AKShare EM 利润表为空: {stock_code}")

        # 统一报告期字段
        profit_df = profit_df.copy()
        profit_df['report_date'] = pd.to_datetime(profit_df['REPORT_DATE']).dt.date
        if not balance_df.empty:
            balance_df = balance_df.copy()
            balance_df['report_date'] = pd.to_datetime(balance_df['REPORT_DATE']).dt.date
        if not cashflow_df.empty:
            cashflow_df = cashflow_df.copy()
            cashflow_df['report_date'] = pd.to_datetime(cashflow_df['REPORT_DATE']).dt.date

        # 用 report_date 索引, 利润表为基准
        records = []
        for _, row in profit_df.iterrows():
            rd = row['report_date']
            # EM 报告类型字段: 合并/母公司, 默认合并
            report_type = '合并报表'
            announcement_date = row.get('NOTICE_DATE')
            if pd.notna(announcement_date):
                try:
                    announcement_date = pd.to_datetime(announcement_date).date()
                except Exception:
                    announcement_date = None

            record = {
                'stock_code': stock_code,
                'report_date': rd,
                'report_type': report_type,
                'announcement_date': announcement_date,
                'total_revenue': self._em_safe_float(row, 'TOTAL_OPERATE_INCOME'),
                'operating_cost': self._em_safe_float(row, 'OPERATE_COST'),
                'net_profit': self._em_safe_float(row, 'PARENT_NETPROFIT'),
                'net_profit_deducted': self._em_safe_float(row, 'DEDUCT_PARENT_NETPROFIT'),
                'interest_expense': self._em_safe_float(row, 'INTEREST_EXPENSE',
                                                        fallbacks=['FE_INTEREST_EXPENSE', 'FINANCE_EXPENSE']),
                'eps': self._em_safe_float(row, 'BASIC_EPS'),
                # 以下字段由资产负债表/现金流量表补充
                'total_assets': None,
                'total_liabilities': None,
                'equity_parent': None,
                'current_assets': None,
                'current_liabilities': None,
                'inventory': None,
                'accounts_receivable': None,
                'accounts_payable': None,
                'operating_cash_flow': None,
                'capex': None,
                'roe': None,
            }

            # 匹配资产负债表
            if not balance_df.empty:
                b_match = balance_df[balance_df['report_date'] == rd]
                if not b_match.empty:
                    b_row = b_match.iloc[0]
                    record['total_assets'] = self._em_safe_float(b_row, 'TOTAL_ASSETS')
                    record['total_liabilities'] = self._em_safe_float(b_row, 'TOTAL_LIABILITIES')
                    record['equity_parent'] = self._em_safe_float(b_row, 'TOTAL_PARENT_EQUITY')
                    record['current_assets'] = self._em_safe_float(b_row, 'TOTAL_CURRENT_ASSETS')
                    record['current_liabilities'] = self._em_safe_float(b_row, 'TOTAL_CURRENT_LIAB')
                    record['inventory'] = self._em_safe_float(b_row, 'INVENTORY')
                    record['accounts_receivable'] = self._em_safe_float(b_row, 'ACCOUNTS_RECE')
                    record['accounts_payable'] = self._em_safe_float(b_row, 'ACCOUNTS_PAYABLE')

            # 匹配现金流量表
            if not cashflow_df.empty:
                c_match = cashflow_df[cashflow_df['report_date'] == rd]
                if not c_match.empty:
                    c_row = c_match.iloc[0]
                    # 经营活动现金流量净额
                    record['operating_cash_flow'] = self._em_safe_float(c_row, 'NETCASH_OPERATE')
                    # 购建固定资产、无形资产和其他长期资产支付的现金
                    record['capex'] = self._em_safe_float(c_row, 'CONSTRUCT_LONG_ASSET')

            # 计算 ROE
            if record['net_profit'] and record['equity_parent'] and record['equity_parent'] != 0:
                record['roe'] = round(record['net_profit'] / record['equity_parent'] * 100, 4)

            records.append(record)

        if not records:
            logger.warning(f"[akshare_em] {stock_code} 无可用记录")
            raise RuntimeError(f"AKShare EM 无可用记录: {stock_code}")

        df = pd.DataFrame(records)
        today_fmt = datetime.now().strftime("%Y-%m-%d")
        try:
            with self.db_ops.transaction():
                self._upsert_financial_em(df)
                self.db_ops.update_last_update_date(stock_code, "financial", today_fmt)
            logger.info(f"[akshare_em] {stock_code} 中备财务数据完成, 共 {len(df)} 条")
        except Exception as e:
            logger.error(f"[akshare_em] {stock_code} 中备财务数据写入失败,已回滚: {e}")
            raise

    @staticmethod
    def _em_safe_float(row, col_name, fallbacks=None):
        """从 EM DataFrame 行安全取浮点值, 支持备选字段名"""
        val = row.get(col_name) if hasattr(row, 'get') else None
        if val is None and fallbacks:
            for fb in fallbacks:
                val = row.get(fb) if hasattr(row, 'get') else None
                if val is not None:
                    break
        if val is None or val == '' or (isinstance(val, float) and pd.isna(val)):
            return None
        try:
            result = float(val)
            if pd.isna(result):
                return None
            return result
        except (ValueError, TypeError):
            return None

    # ============ 末备源: BaoStock (2 字段最小兜底) ============

    def collect_from_baostock(self, stock_code: str):
        """末备: 通过 BaoStock 采集净利润 + 主营收入 (2 字段最小兜底)

        覆盖字段 (2 个):
        - net_profit    <- query_profit_data.netProfit (非归母口径! 包含少数股东损益)
        - total_revenue <- query_profit_data.MBRevenue (主营收入)

        口径警告:
        - net_profit 为非归母口径, 与主源/中备的归母净利润不一致, 仅作兜底
        - 不写 roe/eps: baostock roeAvg/epsTTM 为 TTM 比率, 与报告期口径不一致
        - 不写任何资产负债表字段: baostock query_balance_data 只返回比率(currentRatio/liabilityToAsset 等)
        - 不写 operating_cash_flow: baostock query_cash_flow_data 只返回比率(CFOToOR 等)

        末备触发条件: 主源 Sina 失败 + 中备 EM 也失败
        """
        self._bs_ensure_login()
        bs_code = self._to_bs_code(stock_code)
        logger.info(f"[baostock] 末备采集 {stock_code} 财务数据 (2 字段, 非归母口径)")

        records = []
        # 拉取最近 6 年 (与 collect_dividend_data 同口径)
        now = datetime.now()
        with _bs_global_lock:
            for year in range(now.year, now.year - 6, -1):
                for quarter in [4, 3, 2, 1]:
                    if year == now.year and quarter > self._current_quarter():
                        continue

                    record = self._bs_fetch_one_period(bs_code, year, quarter, stock_code)
                    if record is not None:
                        records.append(record)

        if not records:
            logger.info(f"[baostock] {stock_code} 末备无数据")
            return

        df = pd.DataFrame(records)
        # 事务保证: 末备写入原子化 (用 COALESCE 保护已有字段)
        today_fmt = now.strftime("%Y-%m-%d")
        try:
            with self.db_ops.transaction():
                self._upsert_financial_baostock(df)
                self.db_ops.update_last_update_date(stock_code, "financial", today_fmt)
            logger.info(f"[baostock] {stock_code} 末备财务数据完成, 共 {len(df)} 条")
        except Exception as e:
            logger.error(f"[baostock] {stock_code} 末备财务数据写入失败,已回滚: {e}")
            raise

    def _bs_fetch_one_period(self, bs_code: str, year: int, quarter: int, stock_code: str):
        """从 baostock 拉单季数据 (仅利润表 2 字段)"""
        record = {
            "stock_code": stock_code,
            "report_date": self._quarter_to_date(year, quarter),
            "report_type": "合并报表",
            "net_profit": None,
            "total_revenue": None,
            "announcement_date": None,
        }

        # 利润表接口: 取净利润 + 主营收入 + 公告日
        rs = bs.query_profit_data(code=bs_code, year=year, quarter=quarter)
        data = []
        while rs.error_code == '0' and rs.next():
            data.append(rs.get_row_data())
        if data:
            df_p = pd.DataFrame(data, columns=rs.fields)
            row = df_p.iloc[0]
            # netProfit: 包含少数股东损益的净利润 (非归母)
            record["net_profit"] = self._safe_float(row, 'netProfit')
            # MBRevenue: 主营业务收入
            record["total_revenue"] = self._safe_float(row, 'MBRevenue')
            pub_date = row.get('pubDate')
            if pub_date and str(pub_date).strip():
                try:
                    record["announcement_date"] = pd.to_datetime(pub_date).date()
                except Exception:
                    pass

        # 至少有一个字段才返回, 避免写空记录
        has_data = record["net_profit"] is not None or record["total_revenue"] is not None
        return record if has_data else None

    # ============ 内部辅助 ============

    def _upsert_financial(self, df):
        """主源 upsert: 全字段覆盖 (主源数据为准)"""
        conn = self.db_ops.conn
        conn.register("df", df)
        conn.execute("""
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
        conn.unregister("df")

    def _upsert_financial_em(self, df):
        """中备专用 upsert: 用 COALESCE 保护已有非空字段, 避免覆盖主源更准确数据

        覆盖 18 个字段 (除 report_type 外都用 COALESCE 保护)
        """
        conn = self.db_ops.conn
        conn.register("df", df)
        conn.execute("""
        INSERT INTO financial_statements
        (stock_code, report_date, report_type, total_revenue, net_profit,
         total_assets, total_liabilities, operating_cash_flow, eps, roe, equity_parent, announcement_date,
         operating_cost, net_profit_deducted, inventory, accounts_receivable, accounts_payable, capex, interest_expense,
         current_assets, current_liabilities)
        SELECT stock_code, report_date, report_type, total_revenue, net_profit,
               total_assets, total_liabilities, operating_cash_flow, eps, roe, equity_parent, announcement_date,
               operating_cost, net_profit_deducted, inventory, accounts_receivable, accounts_payable, capex, interest_expense,
               current_assets, current_liabilities
        FROM df
        ON CONFLICT (stock_code, report_date, report_type) DO UPDATE SET
            total_revenue = COALESCE(EXCLUDED.total_revenue, financial_statements.total_revenue),
            net_profit = COALESCE(EXCLUDED.net_profit, financial_statements.net_profit),
            total_assets = COALESCE(EXCLUDED.total_assets, financial_statements.total_assets),
            total_liabilities = COALESCE(EXCLUDED.total_liabilities, financial_statements.total_liabilities),
            operating_cash_flow = COALESCE(EXCLUDED.operating_cash_flow, financial_statements.operating_cash_flow),
            eps = COALESCE(EXCLUDED.eps, financial_statements.eps),
            roe = COALESCE(EXCLUDED.roe, financial_statements.roe),
            equity_parent = COALESCE(EXCLUDED.equity_parent, financial_statements.equity_parent),
            announcement_date = COALESCE(EXCLUDED.announcement_date, financial_statements.announcement_date),
            operating_cost = COALESCE(EXCLUDED.operating_cost, financial_statements.operating_cost),
            net_profit_deducted = COALESCE(EXCLUDED.net_profit_deducted, financial_statements.net_profit_deducted),
            inventory = COALESCE(EXCLUDED.inventory, financial_statements.inventory),
            accounts_receivable = COALESCE(EXCLUDED.accounts_receivable, financial_statements.accounts_receivable),
            accounts_payable = COALESCE(EXCLUDED.accounts_payable, financial_statements.accounts_payable),
            capex = COALESCE(EXCLUDED.capex, financial_statements.capex),
            interest_expense = COALESCE(EXCLUDED.interest_expense, financial_statements.interest_expense),
            current_assets = COALESCE(EXCLUDED.current_assets, financial_statements.current_assets),
            current_liabilities = COALESCE(EXCLUDED.current_liabilities, financial_statements.current_liabilities)
        """)
        conn.unregister("df")

    def _upsert_financial_baostock(self, df):
        """末备专用 upsert: 只更新 2 个字段 (net_profit + total_revenue), 用 COALESCE 保护已有数据

        警告: net_profit 为非归母口径, 仅在主源+中备都失败时兜底
        """
        conn = self.db_ops.conn
        conn.register("df", df)
        conn.execute("""
        INSERT INTO financial_statements
        (stock_code, report_date, report_type, net_profit, total_revenue, announcement_date)
        SELECT stock_code, report_date, report_type, net_profit, total_revenue, announcement_date
        FROM df
        ON CONFLICT (stock_code, report_date, report_type) DO UPDATE SET
            net_profit = COALESCE(EXCLUDED.net_profit, financial_statements.net_profit),
            total_revenue = COALESCE(EXCLUDED.total_revenue, financial_statements.total_revenue),
            announcement_date = COALESCE(EXCLUDED.announcement_date, financial_statements.announcement_date)
        """)
        conn.unregister("df")

    @staticmethod
    def _safe_get(row, col_name):
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

    @staticmethod
    def _to_bs_code(stock_code: str) -> str:
        """sh600519 -> sh.600519"""
        return f"{stock_code[:2]}.{stock_code[2:]}"

    @staticmethod
    def _safe_float(row, col_name):
        val = row.get(col_name) if hasattr(row, 'get') else None
        if val is None or val == '' or val == 'None':
            return None
        try:
            result = float(val)
            if pd.isna(result):
                return None
            return result
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _current_quarter() -> int:
        now = datetime.now()
        return (now.month - 1) // 3 + 1

    @staticmethod
    def _quarter_to_date(year: int, quarter: int):
        """季度 -> 报告期末日期"""
        if quarter == 1:
            return datetime(year, 3, 31).date()
        elif quarter == 2:
            return datetime(year, 6, 30).date()
        elif quarter == 3:
            return datetime(year, 9, 30).date()
        elif quarter == 4:
            return datetime(year, 12, 31).date()
        else:
            raise ValueError(f"非法季度: {quarter}")

    @staticmethod
    def _bs_ensure_login():
        """确保 baostock 登录状态 (复用全局锁, 串行化)

        BaoStock bs 模块是进程级单例, 必须串行化
        每次查询前重新登录避免状态丢失
        """
        with _bs_global_lock:
            try:
                bs.logout()
            except Exception:
                pass
            lg = bs.login()
            if lg.error_code != '0':
                raise ConnectionError(f"BaoStock登录失败: {lg.error_msg}")
