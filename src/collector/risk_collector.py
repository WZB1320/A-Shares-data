"""风险面采集器 —— 股权质押 / 股东增减持 (东方财富 datacenter-web)

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

口径:
    - risk_pledge.trade_date = 快照交易日期(周频)
    - risk_holder_change.change_type ∈ {增持, 减持}
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

    # --- 编排入口 ---

    def collect_risk_all(self, target_codes=None) -> None:
        """风险面全量采集: 质押快照 + 增减持"""
        codes = list(target_codes or [])
        if not codes:
            return
        try:
            self.collect_pledge(codes)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[risk] 质押采集失败: {e}")
        try:
            self.collect_holder_change(codes)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[risk] 增减持采集失败: {e}")
