"""AKShare 东方财富采集器 - 融资融券 + 公告 + 龙虎榜 + 北向资金

数据源分工:
- 日线行情 -> mootdx_collector (TCP直连, 不封IP)
- 股本/行业/分红 -> baostock_collector (更稳定)
- 估值PE/PB -> tencent_collector (零鉴权)
- 财务数据 -> financial_service (AKShare Sina主 + BaoStock备)
- 融资融券 -> AKShare (BaoStock无此API)
- 北向资金 -> 已失效, 暂不可用

本模块负责:
- 融资融券 (AKShare, BaoStock无此API)
- 公告数据 (AKShare独有)
- 龙虎榜数据 (AKShare独有)
"""
import akshare as ak
import pandas as pd
import logging
from datetime import datetime, timedelta
from .base import BaseCollector, retry
from .rate_limiter import socket_timeout

logger = logging.getLogger(__name__)


class EastmoneyCollector(BaseCollector):
    # 融资融券首次采集的回溯窗口(自然日)。该接口按"交易所 + 日期"返回当日
    # 全部标的明细(数千行), 请求次数即成本, 因此窗口不宜过大。
    MARGIN_LOOKBACK_DAYS = 60
    # 增量采集时强制回补的最近交易日数量(自愈窗口)
    MARGIN_OVERLAP_DAYS = 5
    # 单次 akshare 请求的 socket 超时(秒), 防止半死连接让线程无限阻塞
    AKSHARE_SOCKET_TIMEOUT = 15.0

    def __init__(self, db_ops, start_date: str):
        super().__init__(db_ops, start_date)
        self._init_column_metadata()

    def collect_stock(self, stock_code: str):
        steps = [
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

    def collect_margin_trading(self, stock_code: str):
        """通过AKShare采集融资融券数据(BaoStock无此API)

        数据源特性: stock_margin_detail_sse/szse 按"交易所 + 日期"返回当日
        全部标的明细(数千行), 本地筛选后只留自己那一行 —— 即"每次请求都是
        一次全市场下载"。因此必须做增量: 只请求水位线之后的交易日。

        历史缺陷(已修):
          - 固定回溯 60 个自然日并全量重采, 不使用已有的 update_log 水位;
          - 用自然日驱动, 对周末发无意义请求;
          - 异常被 `except Exception: continue` 完全静默, 且无超时 —— 网络
            半死时线程挂起且无任何日志(实测挂起 25 分钟难以定位);
          - 水位写的是"采集执行日期", 当天数据尚未发布时水位已推进, 语义上
            超前于数据本身, 直接用于增量会永久漏掉这一天(现有 13 只股票的
            margin 数据就停在 2026-09-02 而水位却记到 09-03)。现改为写
            "实际覆盖到的最大交易日期"。
        """
        try:
            logger.info(f"[akshare] 开始采集 {stock_code} 融资融券")

            code = stock_code[2:]
            today = datetime.now().date()
            margin_api = ak.stock_margin_detail_sse if stock_code.startswith('sh') else ak.stock_margin_detail_szse

            # --- 本地交易日历(升序), 用于驱动请求与确定回补窗口 ---
            with self.db_ops.cm.acquire_reader() as reader:
                calendar = [
                    r[0] for r in reader.execute(
                        "SELECT DISTINCT trade_date FROM stock_daily "
                        "WHERE trade_date <= ? ORDER BY trade_date",
                        (today,),
                    ).fetchall()
                ]

            # --- 增量窗口: 水位之后开始, 并强制回补最近 N 个交易日 ---
            # get_last_update_date 返回"上次水位 +1 天"(YYYYMMDD); 无记录时为 start_date
            begin_str = self.db_ops.get_last_update_date(stock_code, "margin", self.start_date)
            try:
                begin = datetime.strptime(begin_str, "%Y%m%d").date()
            except (ValueError, TypeError):
                begin = today

            # 回补锚点: 取最近 N 个交易日中最早的一天, begin 不得晚于它,
            # 使偶发请求失败的日期能在后续几次采集中自动补上
            if calendar:
                anchor_idx = max(0, len(calendar) - self.MARGIN_OVERLAP_DAYS)
                begin = min(begin, calendar[anchor_idx])

            earliest = today - timedelta(days=self.MARGIN_LOOKBACK_DAYS)
            clamped = begin < earliest
            if clamped:
                begin = earliest

            if begin > today:
                logger.info(f"[akshare] {stock_code} 融资融券已是最新(水位={begin_str})")
                return

            # --- 用交易日历裁剪区间, 避免对周末/节假日发无意义请求 ---
            trade_dates = [d for d in calendar if begin <= d <= today]
            if not trade_dates:
                # 兜底: 该区间日线尚未入库时退回自然日遍历
                trade_dates = [begin + timedelta(days=i) for i in range((today - begin).days + 1)]

            logger.info(
                f"[akshare] {stock_code} 融资融券区间 {begin}~{today} "
                f"共 {len(trade_dates)} 个交易日 (水位={begin_str}"
                f"{', 首次采集已限制窗口' if clamped else ''})"
            )

            all_data = []
            failed = 0
            for check_date in trade_dates:
                date_str = check_date.strftime("%Y%m%d")
                try:
                    with socket_timeout(self.AKSHARE_SOCKET_TIMEOUT):
                        df = margin_api(date=date_str)
                    if df is None or df.empty:
                        continue
                    code_col = None
                    for c in ['标的证券代码', '标的代码', '证券代码']:
                        if c in df.columns:
                            code_col = c
                            break
                    df_filtered = df[df[code_col].astype(str) == code] if code_col else pd.DataFrame()
                    if not df_filtered.empty:
                        df_filtered = df_filtered.copy()
                        df_filtered['_trade_date'] = check_date
                        all_data.append(df_filtered)
                except Exception as e:
                    # 不再静默: 失败要看得见, 否则挂起时无从定位
                    failed += 1
                    logger.warning(
                        f"[akshare] {stock_code} 融资融券 {date_str} 请求失败: "
                        f"{type(e).__name__}: {e}"
                    )
                    continue

            if failed:
                logger.warning(
                    f"[akshare] {stock_code} 融资融券 {failed}/{len(trade_dates)} 个交易日请求失败"
                )

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

            # 水位写"实际覆盖到的最大交易日期", 而不是执行日期:
            # 执行日期会超前于数据本身, 使增量窗口跳过尚未发布的交易日, 造成永久缺口
            watermark = pd.to_datetime(df['trade_date']).max().date().strftime("%Y-%m-%d")
            # 事务保证: 融资融券数据写入 + 水位更新 原子化
            try:
                with self.db_ops.transaction():
                    self.db_ops.insert_dataframe("margin_trading", df, ["stock_code", "trade_date"])
                    self.db_ops.update_last_update_date(stock_code, "margin", watermark)
                logger.info(
                    f"[akshare] {stock_code} 融资融券完成，写入 {len(df)} 条, 水位推进到 {watermark}"
                )
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
