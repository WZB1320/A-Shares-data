"""AKShare 东方财富采集器 - 财务数据 + AKShare独有数据

数据源分工:
- 日线行情 -> mootdx_collector (TCP直连, 不封IP)
- 股本/行业/分红 -> baostock_collector (更稳定)
- 估值PE/PB -> tencent_collector (零鉴权)
- 融资融券 -> AKShare (BaoStock无此API)
- 北向资金 -> 已失效, 暂不可用

本模块负责:
- 财务数据 (AKShare Sina三大报表, 字段最全)
- 财务补充字段 (东方财富EM接口, 补充扣非净利润等)
- 融资融券 (AKShare, BaoStock无此API)
- 公告数据 (AKShare独有)
- 龙虎榜数据 (AKShare独有)
"""
import akshare as ak
import pandas as pd
import logging
from datetime import datetime
from .base import BaseCollector, retry

logger = logging.getLogger(__name__)


class EastmoneyCollector(BaseCollector):
    def __init__(self, db_ops, start_date: str):
        super().__init__(db_ops, start_date)
        self._init_column_metadata()

    def collect_stock(self, stock_code: str):
        steps = [
            ("财务数据", self.collect_financial_data),
            ("财务字段补充", self._update_missing_financial_fields),
            ("资产负债表补充", self._update_balance_sheet_fields),
            ("融资融券", self.collect_margin_trading),
            ("公告数据", self.collect_announcements),
            ("龙虎榜", self.collect_dragon_tiger),
            ("北向资金", self.collect_northbound_flow),
        ]
        for name, method in steps:
            try:
                method(stock_code)
            except Exception as e:
                logger.error(f"{stock_code} {name}采集失败: {e}")

    def _update_missing_financial_fields(self, stock_code: str):
        """通过东方财富EM补充扣非净利润、利息支出等字段"""
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
            # 避免中途失败导致部分字段已更新、部分未更新
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

    def collect_financial_data(self, stock_code: str):
        """通过AKShare Sina采集三大报表数据"""
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

    def _upsert_financial(self, df):
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

    def _update_balance_sheet_fields(self, stock_code: str):
        """通过东方财富EM资产负债表补充流动资产/流动负债字段

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

    def collect_margin_trading(self, stock_code: str):
        """通过AKShare采集融资融券数据(BaoStock无此API)"""
        try:
            logger.info(f"[akshare] 开始采集 {stock_code} 融资融券")

            code = stock_code[2:]
            today = datetime.now()
            margin_api = ak.stock_margin_detail_sse if stock_code.startswith('sh') else ak.stock_margin_detail_szse

            all_data = []
            for i in range(60):
                check_date = today - pd.Timedelta(days=i)
                date_str = check_date.strftime("%Y%m%d")
                try:
                    df = margin_api(date=date_str)
                    if df.empty:
                        continue
                    code_col = None
                    for c in ['标的证券代码', '标的代码', '证券代码']:
                        if c in df.columns:
                            code_col = c
                            break
                    df_filtered = df[df[code_col].astype(str) == code] if code_col else pd.DataFrame()
                    if not df_filtered.empty:
                        df_filtered = df_filtered.copy()
                        df_filtered['_trade_date'] = check_date.date()
                        all_data.append(df_filtered)
                except Exception:
                    continue

            if not all_data:
                logger.info(f"{stock_code} 没有融资融券数据")
                return

            df = pd.concat(all_data, ignore_index=True)
            df['trade_date'] = pd.to_datetime(df['_trade_date']).dt.date
            df = df.rename(columns={
                "融资余额": "rz_balance",
                "融券余额": "rq_balance"
            })
            df['stock_code'] = stock_code
            df = df.sort_values('trade_date').reset_index(drop=True)

            if 'rz_balance' in df.columns:
                df['rz_change'] = df['rz_balance'].diff()
                df['rz_change_pct'] = df['rz_change'] / df['rz_balance'].shift(1) * 100

            if 'rq_balance' in df.columns:
                df['rq_change'] = df['rq_balance'].diff()
                df['rq_change_pct'] = df['rq_change'] / df['rq_balance'].shift(1) * 100

            df['total_balance'] = df.get('rz_balance', 0) + df.get('rq_balance', 0)
            df['total_change'] = df.get('rz_change', 0) + df.get('rq_change', 0)
            df['total_change_pct'] = df['total_change'] / df['total_balance'].shift(1) * 100

            today_fmt = datetime.now().strftime("%Y-%m-%d")
            # 事务保证: 融资融券数据写入 + 水位更新 原子化
            try:
                with self.db_ops.transaction():
                    self.db_ops.insert_dataframe("margin_trading", df, ["stock_code", "trade_date"])
                    self.db_ops.update_last_update_date(stock_code, "margin", today_fmt)
                logger.info(f"[akshare] {stock_code} 融资融券完成，新增 {len(df)} 条")
            except Exception as e:
                logger.error(f"[akshare] {stock_code} 融资融券写入失败,已回滚: {e}")
                raise

        except Exception as e:
            logger.error(f"[akshare] {stock_code} 融资融券采集失败：{str(e)}")

    def collect_announcements(self, stock_code: str):
        """通过AKShare采集公告数据"""
        last_update = self.db_ops.get_last_update_date(stock_code, "announcement", self.start_date)
        today_ymd = datetime.now().strftime("%Y%m%d")

        if last_update > today_ymd:
            logger.info(f"{stock_code} 公告数据已是最新")
            return

        try:
            start_fmt = f"{last_update[:4]}-{last_update[4:6]}-{last_update[6:8]}"
            end_fmt = datetime.now().strftime("%Y-%m-%d")
            logger.info(f"[akshare] 开始采集 {stock_code} 公告数据")

            code = stock_code[2:]
            df = ak.stock_individual_notice_report(
                security=code,
                begin_date=start_fmt,
                end_date=end_fmt
            )

            if df.empty:
                logger.info(f"{stock_code} 没有新公告数据")
                return

            df = df.rename(columns={
                "公告标题": "title",
                "公告类型": "announcement_type",
                "公告日期": "announcement_date",
                "网址": "pdf_url"
            })
            df["stock_code"] = stock_code
            df["announcement_date"] = pd.to_datetime(df["announcement_date"]).dt.date

            today_fmt = datetime.now().strftime("%Y-%m-%d")
            # 事务保证: 公告数据写入 + 水位更新 原子化
            try:
                with self.db_ops.transaction():
                    self.db_ops.insert_dataframe("announcements", df, ["stock_code", "announcement_date", "title"])
                    self.db_ops.update_last_update_date(stock_code, "announcement", today_fmt)
                logger.info(f"[akshare] {stock_code} 公告数据完成，新增 {len(df)} 条")
            except Exception as e:
                logger.error(f"[akshare] {stock_code} 公告数据写入失败,已回滚: {e}")
                raise

        except Exception as e:
            logger.error(f"[akshare] {stock_code} 公告数据采集失败：{str(e)}")

    def collect_dragon_tiger(self, stock_code: str):
        """通过AKShare采集龙虎榜数据"""
        try:
            logger.info(f"[akshare] 开始采集 {stock_code} 龙虎榜")

            code = stock_code[2:]
            today = datetime.now().strftime("%Y%m%d")

            try:
                df_lhb = ak.stock_lhb_detail_em(start_date=today, end_date=today)

                if not df_lhb.empty:
                    df_lhb = df_lhb[df_lhb['代码'].astype(str) == code]

                    if not df_lhb.empty:
                        # 事务保证: 龙虎榜批量写入原子化,避免部分成功部分失败
                        try:
                            with self.db_ops.transaction():
                                for _, row in df_lhb.iterrows():
                                    try:
                                        self.db_ops.conn.execute("""
                                        INSERT INTO dragon_tiger 
                                        (stock_code, trade_date, list_type, reason, buy_amount, sell_amount, net_amount)
                                        VALUES (?, ?, ?, ?, ?, ?, ?)
                                        ON CONFLICT (stock_code, trade_date, list_type) DO UPDATE SET
                                            reason = EXCLUDED.reason,
                                            buy_amount = EXCLUDED.buy_amount,
                                            sell_amount = EXCLUDED.sell_amount,
                                            net_amount = EXCLUDED.net_amount
                                        """, (
                                            stock_code,
                                            pd.to_datetime(row['上榜日']).date(),
                                            '上榜',
                                            row.get('上榜原因'),
                                            self._safe_get(row, '龙虎榜买入额'),
                                            self._safe_get(row, '龙虎榜卖出额'),
                                            self._safe_get(row, '龙虎榜净买额')
                                        ))
                                    except Exception as e:
                                        logger.error(f"{stock_code} 龙虎榜单条插入失败：{str(e)}")
                                        raise  # 触发外层事务回滚
                            logger.info(f"[akshare] {stock_code} 龙虎榜数据完成")
                        except Exception as e:
                            logger.error(f"[akshare] {stock_code} 龙虎榜事务回滚: {e}")
            except Exception as e:
                logger.debug(f"获取龙虎榜详细数据失败：{str(e)}")

        except Exception as e:
            logger.error(f"[akshare] {stock_code} 龙虎榜采集失败：{str(e)}")

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

    def collect_northbound_flow(self, stock_code: str):
        """采集北向资金数据(使用 stock_hsgt_individual_em 接口)

        注意: 仅沪深港通标的股票有数据,非标的股票(如部分中小盘)会静默跳过
        """
        from .rate_limiter import akshare_rate_limited

        @akshare_rate_limited
        def _fetch(code):
            return ak.stock_hsgt_individual_em(symbol=code)

        try:
            code = stock_code[2:]
            df = _fetch(code)

            if df is None or df.empty:
                logger.info(f"[akshare] {stock_code} 无北向资金数据(可能非沪深港通标的)")
                return

            # 字段映射
            df = df.rename(columns={
                "持股日期": "trade_date",
                "持股数量": "holding_shares",
                "持股市值": "holding_value",
                "持股数量占A股百分比": "holding_ratio",
                "今日增持资金": "net_inflow",
            })

            df["stock_code"] = stock_code
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date

            # 按日期排序计算累计净流入
            df = df.sort_values("trade_date").reset_index(drop=True)
            if "net_inflow" in df.columns:
                df["net_inflow"] = pd.to_numeric(df["net_inflow"], errors="coerce").fillna(0.0)
                df["inflow_5d"] = df["net_inflow"].rolling(5, min_periods=1).sum()
                df["inflow_10d"] = df["net_inflow"].rolling(10, min_periods=1).sum()
                df["inflow_30d"] = df["net_inflow"].rolling(30, min_periods=1).sum()
            else:
                df["inflow_5d"] = None
                df["inflow_10d"] = None
                df["inflow_30d"] = None

            # 选取目标列
            cols = ["stock_code", "trade_date", "net_inflow", "holding_shares",
                    "holding_value", "holding_ratio", "inflow_5d", "inflow_10d", "inflow_30d"]
            df = df[[c for c in cols if c in df.columns]].copy()

            today_fmt = datetime.now().strftime("%Y-%m-%d")
            try:
                with self.db_ops.transaction():
                    self.db_ops.conn.register("df_nb", df)
                    self.db_ops.conn.execute("""
                        INSERT INTO northbound_flow
                        (stock_code, trade_date, net_inflow, holding_shares, holding_value,
                         holding_ratio, inflow_5d, inflow_10d, inflow_30d)
                        SELECT stock_code, trade_date, net_inflow, holding_shares, holding_value,
                               holding_ratio, inflow_5d, inflow_10d, inflow_30d
                        FROM df_nb
                        ON CONFLICT (stock_code, trade_date) DO UPDATE SET
                            net_inflow = EXCLUDED.net_inflow,
                            holding_shares = EXCLUDED.holding_shares,
                            holding_value = EXCLUDED.holding_value,
                            holding_ratio = EXCLUDED.holding_ratio,
                            inflow_5d = EXCLUDED.inflow_5d,
                            inflow_10d = EXCLUDED.inflow_10d,
                            inflow_30d = EXCLUDED.inflow_30d
                    """)
                    self.db_ops.conn.unregister("df_nb")
                    self.db_ops.update_last_update_date(stock_code, "northbound", today_fmt)
                logger.info(f"[akshare] {stock_code} 北向资金完成,新增 {len(df)} 条")
            except Exception as e:
                logger.error(f"[akshare] {stock_code} 北向资金写入失败,已回滚: {e}")
                raise
        except Exception as e:
            # 非沪深港通标的会返回 None,属于正常情况,降级为 info
            msg = str(e)
            if "NoneType" in msg or "non-subscriptable" in msg:
                logger.info(f"[akshare] {stock_code} 非沪深港通标的,无北向资金数据")
            else:
                logger.warning(f"[akshare] {stock_code} 北向资金采集失败: {e}")

    def collect_northbound_market_flow(self):
        """采集北向资金整体流向(沪深港通合计)

        数据源: ak.stock_hsgt_hist_em
        注意: stock_hsgt_individual_em 个股接口数据只到2024-08-16,
        此接口提供整体北向资金流向,数据到最新日期
        """
        from .rate_limiter import akshare_rate_limited

        @akshare_rate_limited
        def _fetch():
            return ak.stock_hsgt_hist_em(symbol="北向资金")

        try:
            df = _fetch()
            if df is None or df.empty:
                logger.warning("[akshare] 北向资金整体流向数据为空")
                return

            # 字段映射
            df = df.rename(columns={
                "日期": "trade_date",
                "当日成交净买额": "net_buy_amount",
                "买入成交额": "buy_amount",
                "卖出成交额": "sell_amount",
                "历史累计净买额": "cumulative_net_buy",
                "当日资金流入": "daily_inflow",
                "当日余额": "daily_balance",
                "持股市值": "holding_market_value",
            })

            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date

            # 选取目标列
            cols = ["trade_date", "net_buy_amount", "buy_amount", "sell_amount",
                    "cumulative_net_buy", "daily_inflow", "daily_balance", "holding_market_value"]
            df = df[[c for c in cols if c in df.columns]].copy()

            # 数值转换
            for col in cols[1:]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            try:
                with self.db_ops.transaction():
                    self.db_ops.conn.register("df_nmf", df)
                    self.db_ops.conn.execute("""
                        INSERT INTO northbound_market_flow
                        (trade_date, net_buy_amount, buy_amount, sell_amount,
                         cumulative_net_buy, daily_inflow, daily_balance, holding_market_value)
                        SELECT trade_date, net_buy_amount, buy_amount, sell_amount,
                               cumulative_net_buy, daily_inflow, daily_balance, holding_market_value
                        FROM df_nmf
                        ON CONFLICT (trade_date) DO UPDATE SET
                            net_buy_amount = EXCLUDED.net_buy_amount,
                            buy_amount = EXCLUDED.buy_amount,
                            sell_amount = EXCLUDED.sell_amount,
                            cumulative_net_buy = EXCLUDED.cumulative_net_buy,
                            daily_inflow = EXCLUDED.daily_inflow,
                            daily_balance = EXCLUDED.daily_balance,
                            holding_market_value = EXCLUDED.holding_market_value
                    """)
                    self.db_ops.conn.unregister("df_nmf")
                logger.info(f"[akshare] 北向资金整体流向完成,共 {len(df)} 条")
            except Exception as e:
                logger.error(f"[akshare] 北向资金整体流向写入失败,已回滚: {e}")
                raise
        except Exception as e:
            logger.warning(f"[akshare] 北向资金整体流向采集失败: {e}")

    def _init_column_metadata(self):
        conn = self.db_ops.conn
        metadata = [
            ("stock_daily", "stock_code", "股票代码", None),
            ("stock_daily", "trade_date", "交易日期", None),
            ("stock_daily", "open", "开盘价", "元"),
            ("stock_daily", "high", "最高价", "元"),
            ("stock_daily", "low", "最低价", "元"),
            ("stock_daily", "close", "收盘价", "元"),
            ("stock_daily", "volume", "成交量", "股"),
            ("stock_daily", "amount", "成交额", "元"),
            ("stock_daily", "adjust_factor", "复权因子", None),
            ("financial_statements", "stock_code", "股票代码", None),
            ("financial_statements", "report_date", "报告期", None),
            ("financial_statements", "report_type", "报告类型", None),
            ("financial_statements", "total_revenue", "营业收入", "元"),
            ("financial_statements", "net_profit", "净利润", "元"),
            ("financial_statements", "total_assets", "总资产", "元"),
            ("financial_statements", "total_liabilities", "总负债", "元"),
        ]

        # 事务保证: 元数据批量写入原子化
        with self.db_ops.transaction():
            for table_name, column_name, description, unit in metadata:
                try:
                    conn.execute("""
                    INSERT OR REPLACE INTO column_metadata 
                    (table_name, column_name, description, unit)
                    VALUES (?, ?, ?, ?)
                    """, (table_name, column_name, description, unit))
                except Exception as e:
                    logger.debug(f"元数据插入跳过: {e}")
