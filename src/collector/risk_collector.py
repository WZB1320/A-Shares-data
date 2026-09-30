"""风险面采集器 —— 质押 / 增减持 / 回购 / 限售解禁 / 股东户数 (东方财富 datacenter-web)

⚠️ 数据源说明(2026-09-29 实测):
    走 akshare EM datacenter-web 接口, **不受 push2his 封禁影响**
    (封禁只限 push2/push2his 行情主机)。

数据特性(实测):
    - 质押比例: ak.stock_gpzy_pledge_ratio_em(date=YYYYMMDD) 全市场**周频**快照,
      每周约周五收盘后更新; 最新 1-2 周可能尚未发布, 需按周向前回溯探测。
      单位: 质押比例=%, 质押股数=万股, 质押市值=万元 (落库统一换算为 股/元)。
    - 股东增减持: ak.stock_ggcg_em(symbol='全部') 全市场历史**全量**(约 300 页,
      实测约 3 分钟), 无日期参数。故一次性拉全量、按自选股过滤后 UPSERT,
      以公告日水位做增量语义(重复行由 PK + DO NOTHING 去重)。
    - 股票回购: ak.stock_repurchase_em() 全市场全量一次拉取(约 5500 条),
      单位已实测核实: 数量=股、金额=元 (五粮液 14,707,406 股 × ~75 元 ≈ 11 亿吻合)。
    - 限售解禁: ak.stock_restricted_release_detail_em(start_date, end_date)
      全市场按日期窗口拉取(含未来解禁), 数量=股, 占比=小数(×100 得百分比)。
    - 股东户数: ak.stock_zh_a_gdhs_detail_em(symbol=code) **按股拉取**(22 次调用),
      全历史一次返回, 更新及时(实测至最新公告日)。

⚠️ 翻页漏行风险(来源: a-stock-data 项目 2026-06 实测):
    东财 datacenter 翻页按非唯一字段排序时会重复 1 行、漏 1 行
    (质押表 2212 行实测)。akshare 内部分页我们无法控制排序键,
    所以统一在写入前做 **PK 去重检测**: 源数据出现重复 PK 行时
    记 warning 并去重(说明该表排序不唯一, 结果可能不完整)。

口径:
    - risk_pledge.trade_date = 快照交易日期(周频)
    - risk_holder_change.change_type ∈ {增持, 减持}
    - risk_unlock.free_date = 解禁日(含未来计划), ratio_to_float = 占解禁前流通市值小数
    - risk_holder_num.stat_date = 股东户数统计截止日
    - 无质押记录的股票 = 零质押(健康), 接口层不返回该股行属于正常
"""
import logging
from datetime import date, datetime, timedelta

import pandas as pd

from .base import BaseCollector

logger = logging.getLogger(__name__)

_PLEDGE_VALUE_COLUMNS = [
    "pledge_ratio", "pledge_shares", "pledge_market_value", "pledge_count",
]
_HOLDER_VALUE_COLUMNS = [
    "change_shares", "change_ratio", "hold_after_shares", "hold_after_ratio",
    "change_start_date", "change_end_date",
]
_BUYBACK_VALUE_COLUMNS = [
    "progress", "plan_start_date", "price_cap",
    "shares_lower", "shares_upper", "amount_lower", "amount_upper",
    "done_shares", "done_amount", "done_price_low", "done_price_high",
]
_UNLOCK_VALUE_COLUMNS = [
    "free_shares", "actual_free_shares", "actual_free_value",
    "ratio_to_float", "close_before",
]
_HOLDER_NUM_VALUE_COLUMNS = [
    "holder_num", "holder_num_prev", "holder_num_change", "change_ratio",
    "avg_mktcap", "avg_shares", "total_shares", "notice_date",
]


class RiskCollector(BaseCollector):
    """风险面采集(股权质押 + 股东增减持, 东方财富 datacenter)"""

    # 质押快照向前回溯探测的最大周数(数据周频, 假期可能顺延)
    PLEDGE_LOOKBACK_WEEKS = 4
    # 全市场增减持全量拉取后与上次水位重复度高, 拉取间隔不再叠加限流
    GGC_PAGES_MIN_INTERVAL = 1.0

    # --- 工具 ---

    @staticmethod
    def _to_stock_code(em_code: str) -> str:
        """'000858' -> 'sz000858' ; '600519' -> 'sh600519'"""
        em_code = str(em_code).strip()
        return ("sh" if em_code.startswith(("6", "9", "5")) else "sz") + em_code

    @staticmethod
    def _parse_date(val):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return None
        if isinstance(val, (date, datetime)):
            return val if isinstance(val, date) and not isinstance(val, datetime) else val.date()
        s = str(val).strip()
        if not s or s in ("--", "-", "nan", "NaT"):
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _parse_float(val):
        try:
            f = float(val)
        except (TypeError, ValueError):
            return None
        if pd.isna(f):
            return None
        return f

    @staticmethod
    def _dedupe_pk(df: pd.DataFrame, pk_cols: list, table: str) -> pd.DataFrame:
        """PK 去重检测(防东财 datacenter 翻页漏行, 见模块 docstring)。

        源数据出现重复 PK 行 → 说明该表分页排序不唯一, 结果可能不完整,
        记 warning 后去重(保最后一行, 通常是最新值)。
        """
        if df.empty:
            return df
        dup_mask = df.duplicated(subset=pk_cols, keep="last")
        if dup_mask.any():
            logger.warning(
                f"[risk] {table} 源数据检测到 {int(dup_mask.sum())} 行重复 PK "
                f"({'+'.join(pk_cols)}) —— 接口分页排序不唯一, 结果可能不完整, 已去重"
            )
        return df[~dup_mask].reset_index(drop=True)

    # --- 质押比例(周频全市场快照) ---

    def _fetch_pledge_snapshot(self):
        """按周向前回溯找最新一期全市场质押比例快照, 返回 (trade_date, df) 或 (None, None)"""
        from .rate_limiter import akshare_rate_limited

        @akshare_rate_limited
        def _fetch(yyyymmdd):
            import akshare as ak
            return ak.stock_gpzy_pledge_ratio_em(date=yyyymmdd)

        today = datetime.now().date()
        # 从上一个周五开始回溯(数据约每周五更新)
        days_since_friday = (today.weekday() - 4) % 7 or 7
        probe = today - timedelta(days=days_since_friday)
        for week in range(self.PLEDGE_LOOKBACK_WEEKS):
            ymd = probe.strftime("%Y%m%d")
            try:
                df = _fetch(ymd)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[risk] 质押快照 {ymd} 拉取失败: {type(e).__name__}: {str(e)[:80]}")
                df = None
            if df is not None and len(df):
                logger.info(f"[risk] 质押快照命中 {probe} ({len(df)} 只)")
                return probe, df
            logger.info(f"[risk] 质押快照 {probe} 无数据, 向前回溯一周")
            probe -= timedelta(days=7)
        return None, None

    def _parse_pledge(self, snapshot_date, df, target_codes: set) -> pd.DataFrame:
        rows = []
        for _, r in df.iterrows():
            code = self._to_stock_code(r.get("股票代码", ""))
            if code not in target_codes:
                continue
            shares_wan = self._parse_float(r.get("质押股数"))       # 万股
            mv_wan = self._parse_float(r.get("质押市值"))           # 万元
            rows.append({
                "stock_code": code,
                "trade_date": snapshot_date,
                "pledge_ratio": self._parse_float(r.get("质押比例")),
                "pledge_shares": int(round(shares_wan * 1e4)) if shares_wan is not None else None,
                "pledge_market_value": mv_wan * 1e4 if mv_wan is not None else None,
                "pledge_count": int(r["质押笔数"]) if self._parse_float(r.get("质押笔数")) is not None else None,
            })
        return pd.DataFrame(rows)

    def collect_pledge(self, target_codes=None) -> int:
        """采集最新一期质押比例快照(全市场拉一次, 过滤自选股), 返回写入行数"""
        target_codes = set(target_codes or [])
        snapshot_date, df = self._fetch_pledge_snapshot()
        if snapshot_date is None:
            logger.warning("[risk] 近 4 周均无质押快照, 跳过")
            return 0
        out = self._parse_pledge(snapshot_date, df, target_codes)
        if out.empty:
            logger.info("[risk] 质押快照无自选股记录(全部零质押?)")
            return 0
        out = self._dedupe_pk(out, ["stock_code", "trade_date"], "risk_pledge")
        out = out.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
        with self.db_ops.transaction():
            self.db_ops.insert_dataframe(
                "risk_pledge", out,
                conflict_columns=["stock_code", "trade_date"],
                update_columns=_PLEDGE_VALUE_COLUMNS,
            )
            for code in out["stock_code"].unique():
                self.db_ops.update_last_update_date(code, "risk_pledge", snapshot_date.isoformat())
        logger.info(f"[risk] 质押快照写入 {len(out)} 行 (快照日 {snapshot_date})")
        return len(out)

    # --- 股东增减持(全市场全量, 过滤自选股) ---

    def _fetch_holder_change(self) -> pd.DataFrame:
        from .rate_limiter import akshare_rate_limited

        @akshare_rate_limited
        def _fetch():
            import akshare as ak
            return ak.stock_ggcg_em(symbol="全部")

        df = _fetch()
        if df is None or df.empty:
            return pd.DataFrame()
        # 列名兼容: 不同 akshare 版本可能用 股票代码/公告日期 或 代码/公告日
        code_col = "代码" if "代码" in df.columns else "股票代码"
        ann_col = "公告日" if "公告日" in df.columns else "公告日期"
        df = df.rename(columns={code_col: "_em_code", ann_col: "_ann_date"})
        return df

    def _parse_holder_change(self, df, target_codes: set) -> pd.DataFrame:
        rows = []
        for _, r in df.iterrows():
            code = self._to_stock_code(r.get("_em_code", ""))
            if code not in target_codes:
                continue
            change_type = str(r.get("持股变动信息-增减") or "").strip()
            if change_type not in ("增持", "减持"):
                continue
            shares_wan = self._parse_float(r.get("持股变动信息-变动数量"))  # 万股
            after_wan = self._parse_float(r.get("变动后持股情况-持股总数"))  # 万股
            shares = int(round(shares_wan * 1e4)) if shares_wan is not None else 0
            holder = str(r.get("股东名称") or "").strip()
            if not holder or not shares:
                continue
            rows.append({
                "stock_code": code,
                "announcement_date": self._parse_date(r.get("_ann_date")),
                "holder_name": holder[:200],
                "change_type": change_type,
                "change_shares": shares,
                "change_ratio": self._parse_float(r.get("持股变动信息-占总股本比例")),
                "hold_after_shares": int(round(after_wan * 1e4)) if after_wan is not None else None,
                "hold_after_ratio": self._parse_float(r.get("变动后持股情况-占总股本比例")),
                "change_start_date": self._parse_date(r.get("变动开始日")),
                "change_end_date": self._parse_date(r.get("变动截止日")),
            })
        out = pd.DataFrame(rows)
        if out.empty:
            return out
        # 剔除无法定位公告日的行(PK 要求 announcement_date NOT NULL)
        return out[out["announcement_date"].notna()]

    def collect_holder_change(self, target_codes=None) -> int:
        """采集股东增减持(全市场全量拉取一次, 过滤自选股), 返回写入行数"""
        target_codes = set(target_codes or [])
        raw = self._fetch_holder_change()
        if raw.empty:
            logger.warning("[risk] 增减持接口无数据")
            return 0
        out = self._parse_holder_change(raw, target_codes)
        if out.empty:
            logger.info("[risk] 增减持无自选股记录")
            return 0
        out = self._dedupe_pk(
            out,
            ["stock_code", "announcement_date", "holder_name", "change_type", "change_shares"],
            "risk_holder_change",
        )
        out = out.sort_values(
            ["stock_code", "announcement_date", "holder_name"]
        ).reset_index(drop=True)
        with self.db_ops.transaction():
            self.db_ops.insert_dataframe(
                "risk_holder_change", out,
                conflict_columns=["stock_code", "announcement_date",
                                  "holder_name", "change_type", "change_shares"],
                update_columns=_HOLDER_VALUE_COLUMNS,
            )
            for code, mx in out.groupby("stock_code")["announcement_date"].max().items():
                self.db_ops.update_last_update_date(code, "risk_holder_change",
                                                    mx.isoformat())
        logger.info(f"[risk] 增减持写入 {len(out)} 行 "
                    f"(覆盖 {out['stock_code'].nunique()} 只, "
                    f"最新公告 {out['announcement_date'].max()})")
        return len(out)

    # --- 股票回购(全市场全量, 过滤自选股) ---

    def collect_buyback(self, target_codes=None) -> int:
        """采集股票回购方案与进度(全市场一次拉全, 过滤自选股), 返回写入行数"""
        from .rate_limiter import akshare_rate_limited

        @akshare_rate_limited
        def _fetch():
            import akshare as ak
            return ak.stock_repurchase_em()

        target_codes = set(target_codes or [])
        raw = _fetch()
        if raw is None or raw.empty:
            logger.warning("[risk] 回购接口无数据")
            return 0
        rows = []
        for _, r in raw.iterrows():
            code = self._to_stock_code(r.get("股票代码", ""))
            if code not in target_codes:
                continue
            ann = self._parse_date(r.get("最新公告日期"))
            progress = str(r.get("实施进度") or "").strip()
            if not ann or not progress:
                continue
            rows.append({
                "stock_code": code,
                "announcement_date": ann,
                "progress": progress,
                "plan_start_date": self._parse_date(r.get("回购起始时间")),
                "price_cap": self._parse_float(r.get("计划回购价格区间")),
                "shares_lower": self._parse_float(r.get("计划回购数量区间-下限")),
                "shares_upper": self._parse_float(r.get("计划回购数量区间-上限")),
                "amount_lower": self._parse_float(r.get("计划回购金额区间-下限")),
                "amount_upper": self._parse_float(r.get("计划回购金额区间-上限")),
                "done_shares": self._parse_float(r.get("已回购股份数量")),
                "done_amount": self._parse_float(r.get("已回购金额")),
                "done_price_low": self._parse_float(r.get("已回购股份价格区间-下限")),
                "done_price_high": self._parse_float(r.get("已回购股份价格区间-上限")),
            })
        out = pd.DataFrame(rows)
        if out.empty:
            logger.info("[risk] 回购无自选股记录")
            return 0
        out = self._dedupe_pk(
            out, ["stock_code", "announcement_date", "progress"], "risk_buyback")
        out = out.sort_values(["stock_code", "announcement_date"]).reset_index(drop=True)
        with self.db_ops.transaction():
            self.db_ops.insert_dataframe(
                "risk_buyback", out,
                conflict_columns=["stock_code", "announcement_date", "progress"],
                update_columns=_BUYBACK_VALUE_COLUMNS,
            )
            for code, mx in out.groupby("stock_code")["announcement_date"].max().items():
                self.db_ops.update_last_update_date(code, "risk_buyback", mx.isoformat())
        logger.info(f"[risk] 回购写入 {len(out)} 行 (覆盖 {out['stock_code'].nunique()} 只)")
        return len(out)

    # --- 限售解禁(全市场按日期窗口, 含未来计划) ---

    UNLOCK_PAST_DAYS = 730   # 历史解禁窗口(2 年)
    UNLOCK_FORWARD_DAYS = 365  # 未来解禁窗口(1 年)

    def collect_unlock(self, target_codes=None) -> int:
        """采集限售解禁(历史 + 未来计划), 返回写入行数"""
        from .rate_limiter import akshare_rate_limited

        @akshare_rate_limited
        def _fetch(start, end):
            import akshare as ak
            return ak.stock_restricted_release_detail_em(
                start_date=start, end_date=end)

        target_codes = set(target_codes or [])
        today = datetime.now().date()
        start = (today - timedelta(days=self.UNLOCK_PAST_DAYS)).strftime("%Y%m%d")
        end = (today + timedelta(days=self.UNLOCK_FORWARD_DAYS)).strftime("%Y%m%d")
        raw = _fetch(start, end)
        if raw is None or raw.empty:
            logger.warning("[risk] 解禁接口无数据")
            return 0
        rows = []
        for _, r in raw.iterrows():
            code = self._to_stock_code(r.get("股票代码", ""))
            if code not in target_codes:
                continue
            free_date = self._parse_date(r.get("解禁时间"))
            free_type = str(r.get("限售股类型") or "").strip()
            if not free_date or not free_type:
                continue
            rows.append({
                "stock_code": code,
                "free_date": free_date,
                "free_type": free_type[:100],
                "free_shares": self._parse_float(r.get("解禁数量")),
                "actual_free_shares": self._parse_float(r.get("实际解禁数量")),
                "actual_free_value": self._parse_float(r.get("实际解禁市值")),
                "ratio_to_float": self._parse_float(r.get("占解禁前流通市值比例")),
                "close_before": self._parse_float(r.get("解禁前一交易日收盘价")),
            })
        out = pd.DataFrame(rows)
        if out.empty:
            logger.info("[risk] 解禁窗口内无自选股记录")
            return 0
        out = self._dedupe_pk(
            out, ["stock_code", "free_date", "free_type"], "risk_unlock")
        out = out.sort_values(["stock_code", "free_date"]).reset_index(drop=True)
        with self.db_ops.transaction():
            self.db_ops.insert_dataframe(
                "risk_unlock", out,
                conflict_columns=["stock_code", "free_date", "free_type"],
                update_columns=_UNLOCK_VALUE_COLUMNS,
            )
            # 不写 update_log 水位: 本表是"整窗口全量拉取 + PK 幂等", 没有增量水位语义。
            # 若写 max(free_date) 会得到未来日期(未来解禁计划), 使水位超前于现实;
            # 写拉取日期又与实际数据不符。二者都违反"水位=实际入库数据最大日期"的铁律,
            # 故此处不写(该表不依赖 update_log 做增量判断)。
        logger.info(f"[risk] 解禁写入 {len(out)} 行 "
                    f"(覆盖 {out['stock_code'].nunique()} 只, 窗口 {start}~{end})")
        return len(out)

    # --- 股东户数(按股拉取, 全历史) ---

    def collect_holder_num(self, target_codes=None) -> int:
        """采集股东户数历史(每股一次调用, 共 N 次), 返回写入行数"""
        from .rate_limiter import akshare_rate_limited

        @akshare_rate_limited
        def _fetch(em_code):
            import akshare as ak
            return ak.stock_zh_a_gdhs_detail_em(symbol=em_code)

        target_codes = list(target_codes or [])
        rows = []
        for code in target_codes:
            if code == "sh513700":  # ETF 无股东户数
                continue
            em_code = code[2:]
            try:
                df = _fetch(em_code)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[risk] {code} 股东户数拉取失败: {type(e).__name__}: {str(e)[:80]}")
                continue
            if df is None or df.empty:
                logger.info(f"[risk] {code} 无股东户数记录")
                continue
            for _, r in df.iterrows():
                stat = self._parse_date(r.get("股东户数统计截止日"))
                if not stat:
                    continue
                rows.append({
                    "stock_code": code,
                    "stat_date": stat,
                    "holder_num": self._parse_float(r.get("股东户数-本次")),
                    "holder_num_prev": self._parse_float(r.get("股东户数-上次")),
                    "holder_num_change": self._parse_float(r.get("股东户数-增减")),
                    "change_ratio": self._parse_float(r.get("股东户数-增减比例")),
                    "avg_mktcap": self._parse_float(r.get("户均持股市值")),
                    "avg_shares": self._parse_float(r.get("户均持股数量")),
                    "total_shares": self._parse_float(r.get("总股本")),
                    "notice_date": self._parse_date(r.get("股东户数公告日期")),
                })
        out = pd.DataFrame(rows)
        if out.empty:
            logger.info("[risk] 股东户数无数据")
            return 0
        out = self._dedupe_pk(out, ["stock_code", "stat_date"], "risk_holder_num")
        out = out.sort_values(["stock_code", "stat_date"]).reset_index(drop=True)
        with self.db_ops.transaction():
            self.db_ops.insert_dataframe(
                "risk_holder_num", out,
                conflict_columns=["stock_code", "stat_date"],
                update_columns=_HOLDER_NUM_VALUE_COLUMNS,
            )
            for code, mx in out.groupby("stock_code")["stat_date"].max().items():
                self.db_ops.update_last_update_date(code, "risk_holder_num",
                                                    mx.isoformat())
        logger.info(f"[risk] 股东户数写入 {len(out)} 行 (覆盖 {out['stock_code'].nunique()} 只)")
        return len(out)

    # --- 编排入口 ---

    def collect_risk_all(self, target_codes=None) -> None:
        """风险面全量采集: 质押快照 + 增减持 + 回购 + 解禁 + 股东户数

        单个子项失败不阻塞其余子项, 但**必须带堆栈**(exc_info) —— 曾因只打印
        一行 message 且被上层吞掉, 导致"解禁写 0 行"这种静默失败很难被发现。
        """
        codes = list(target_codes or [])
        if not codes:
            return
        failed = []
        for name, fn in (
            ("质押", self.collect_pledge),
            ("增减持", self.collect_holder_change),
            ("回购", self.collect_buyback),
            ("解禁", self.collect_unlock),
            ("股东户数", self.collect_holder_num),
        ):
            try:
                fn(codes)
            except Exception as e:  # noqa: BLE001
                failed.append(name)
                logger.error(f"[risk] {name}采集失败: {type(e).__name__}: {e}",
                             exc_info=True)
        if failed:
            logger.error(f"[risk] 本轮有子项失败: {' '.join(failed)}")
