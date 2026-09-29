"""主力资金流向采集器 —— 东方财富 push2his

为什么不用 akshare:
    `ak.stock_individual_fund_flow` 内部硬编码 https://push2his.eastmoney.com,
    本机对该主机 https(443) 的连接被远端直接关闭(RemoteDisconnected),
    而同一主机的 http(80) 完全正常。故此处直接用 requests 走 http, 不依赖 akshare。

数据特性(实测):
    单次请求返回该股票最近 121 个交易日的逐日资金流(约半年), 这是接口上限
    (lmt / beg / end 怎么调都一样)。因此策略为: 每次全量拉取这段时间并 UPSERT;
    表内数据随每次运行自然累积超过 121 天(旧数据永不删除)。

⚠️ 限流(实测踩坑):
    该主机对**突发请求**很敏感, 连续快速请求数十次后会**按 IP 临时封禁**
    (现象: 所有东财主机都报 RemoteDisconnected, 而腾讯/新浪/百度正常)。
    因此设了全局最小请求间隔(MIN_INTERVAL) + 指数退避重试(MAX_ATTEMPTS)。
    批量采集请串行、不要加快并发。

klines 字段(逗号分隔 15 段):
    [0] 日期        [1] 主力净额    [2] 小单净额    [3] 中单净额
    [4] 大单净额    [5] 超大单净额  [6] 主力净占比  [7] 小单净占比
    [8] 中单净占比  [9] 大单净占比  [10] 超大单净占比
    [11] 收盘价(仅校验)  [12] 涨跌幅(仅校验)  [13][14] 恒为 0, 未使用

恒等式(采集时自检, 也是自证依据):
    main = large + xlarge                主力净额 = 大单 + 超大单
    main_ratio ≈ large_ratio + xlarge_ratio
    四类单(超大/大/中/小)净额合计 ≈ 0      资金守恒
    [11] 收盘价 应与 stock_daily.close 一致
"""
import logging
import random
import time
from datetime import datetime

import pandas as pd
import requests

from .base import BaseCollector

logger = logging.getLogger(__name__)

_API_URL = "http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_UT = "b2884a393a59ad64002292a3e90d46a5"
_FIELDS1 = "f1,f2,f3,f7"
_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# klines 段位 -> 落库列名(仅前 11 段为业务字段)
_COLUMN_MAP = {
    1: "main_net_amount",
    2: "small_net_amount",
    3: "medium_net_amount",
    4: "large_net_amount",
    5: "xlarge_net_amount",
    6: "main_net_ratio",
    7: "small_net_ratio",
    8: "medium_net_ratio",
    9: "large_net_ratio",
    10: "xlarge_net_ratio",
}

# 落库列顺序(PK 之外), 同时作为 UPSERT 的更新列
_VALUE_COLUMNS = [
    "main_net_amount", "xlarge_net_amount", "large_net_amount",
    "medium_net_amount", "small_net_amount",
    "main_net_ratio", "xlarge_net_ratio", "large_net_ratio",
    "medium_net_ratio", "small_net_ratio",
]


class FundFlowCollector(BaseCollector):
    """主力资金流向采集(东方财富 push2his, 走 http)"""

    HTTP_TIMEOUT = 15.0        # 单次请求超时(秒)
    CLOSE_TOLERANCE = 0.01     # 收盘价交叉校验容差(相对 1%)
    MIN_INTERVAL = 1.5         # 全局最小请求间隔(秒), 防止突发触发 IP 封禁
    MAX_ATTEMPTS = 4           # 单只股票的最大请求次数(含首次)
    RETRY_BASE = 3.0           # 指数退避基数: 3s / 6s / 12s (+抖动)

    # 类级节流游标(跨实例共享): 保证无论多少实例, 请求都按 MIN_INTERVAL 串行
    _last_request_at = 0.0

    @classmethod
    def _throttle(cls) -> None:
        """全局节流: 距上次请求不足 MIN_INTERVAL 则等待"""
        gap = time.time() - cls._last_request_at
        if gap < cls.MIN_INTERVAL:
            time.sleep(cls.MIN_INTERVAL - gap)
        cls._last_request_at = time.time()

    # --- 基础 ---

    @staticmethod
    def _session() -> requests.Session:
        s = requests.Session()
        s.trust_env = False    # 绕过环境代理(否则 localhost/外网都可能被代理拦截)
        s.headers.update({
            "User-Agent": _USER_AGENT,
            "Referer": "https://data.eastmoney.com/",
        })
        return s

    @staticmethod
    def _to_secid(stock_code: str) -> str:
        """sh600519 -> 1.600519 ; sz000858 -> 0.000858"""
        market = "1" if stock_code.startswith("sh") else "0"
        return f"{market}.{stock_code[2:]}"

    # --- 抓取与解析 ---

    def _fetch_klines(self, stock_code: str) -> list:
        """拉取原始 klines 字符串列表; 无数据返回空列表

        带全局节流 + 指数退避重试: 该主机对突发请求会按 IP 临时封禁,
        单次失败多为限流而非永久错误, 退避后通常可以恢复。
        """
        params = {
            "lmt": "0",
            "klt": "101",
            "secid": self._to_secid(stock_code),
            "fields1": _FIELDS1,
            "fields2": _FIELDS2,
            "ut": _UT,
        }
        last_error = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            self._throttle()
            try:
                resp = self._session().get(_API_URL, params=params, timeout=self.HTTP_TIMEOUT)
                resp.raise_for_status()
                payload = resp.json() or {}
                data = payload.get("data") or {}
                return data.get("klines") or []
            except Exception as e:
                last_error = e
                if attempt < self.MAX_ATTEMPTS:
                    wait = self.RETRY_BASE * (2 ** (attempt - 1)) + random.uniform(0, 1.5)
                    logger.warning(
                        f"[fundflow] {stock_code} 第 {attempt} 次请求失败"
                        f"({type(e).__name__}), {wait:.1f}s 后重试"
                    )
                    time.sleep(wait)
        raise last_error

    def _parse(self, stock_code: str, klines: list) -> pd.DataFrame:
        rows = []
        for line in klines:
            parts = str(line).split(",")
            if len(parts) < 13:
                logger.warning(f"[fundflow] {stock_code} 字段数不足({len(parts)}), 跳过: {parts[:2]}")
                continue
            try:
                rec = {
                    "stock_code": stock_code,
                    "trade_date": datetime.strptime(parts[0], "%Y-%m-%d").date(),
                }
                for idx, col in _COLUMN_MAP.items():
                    rec[col] = float(parts[idx])
                rec["_close"] = float(parts[11])        # 仅用于交叉校验, 不落库
                rec["_change_pct"] = float(parts[12])   # 同上
                rows.append(rec)
            except (ValueError, IndexError) as e:
                logger.warning(f"[fundflow] {stock_code} 无法解析 {parts[:2]}: {e}")
        return pd.DataFrame(rows)

    # --- 质量自检 ---

    def _check_identities(self, stock_code: str, df: pd.DataFrame) -> None:
        """恒等式自检: 违约只告警不中断(数据源偶发异常不应阻断整轮采集)"""
        main_minus_parts = (
            df["main_net_amount"] - (df["large_net_amount"] + df["xlarge_net_amount"])
        ).abs()
        bad_main = int((main_minus_parts > 1.0).sum())

        net_total = (
            df["large_net_amount"] + df["xlarge_net_amount"]
            + df["medium_net_amount"] + df["small_net_amount"]
        ).abs()
        scale = (
            df["large_net_amount"].abs() + df["xlarge_net_amount"].abs()
            + df["medium_net_amount"].abs() + df["small_net_amount"].abs()
        )
        bad_conserve = int((net_total > (scale * 1e-3).clip(lower=10.0)).sum())

        ratio_gap = (
            df["main_net_ratio"] - (df["large_net_ratio"] + df["xlarge_net_ratio"])
        ).abs()
        bad_ratio = int((ratio_gap > 0.05).sum())

        if bad_main or bad_conserve or bad_ratio:
            logger.warning(
                f"[fundflow] {stock_code} 恒等式违约: "
                f"主力≠大单+超大单 {bad_main} 行, "
                f"四类单不守恒 {bad_conserve} 行, "
                f"占比不闭合 {bad_ratio} 行 (共 {len(df)} 行)"
            )
        else:
            logger.info(f"[fundflow] {stock_code} 恒等式自检通过 ({len(df)} 行)")

    def _cross_check_close(self, df: pd.DataFrame) -> tuple:
        """用接口返回的收盘价与 stock_daily 逐日比对, 不一致则告警(不落库)"""
        code = df["stock_code"].iloc[0]
        with self.db_ops.cm.acquire_reader() as reader:
            rows = reader.execute(
                "SELECT trade_date, close FROM stock_daily "
                "WHERE stock_code = ? AND trade_date >= ? AND trade_date <= ?",
                (code, df["trade_date"].min(), df["trade_date"].max()),
            ).fetchall()
        close_map = {r[0]: r[1] for r in rows}

        checked = mismatch = 0
        for _, row in df.iterrows():
            daily_close = close_map.get(row["trade_date"])
            if daily_close is None or float(daily_close) == 0.0:
                continue
            checked += 1
            if abs(float(daily_close) - row["_close"]) / float(daily_close) > self.CLOSE_TOLERANCE:
                mismatch += 1
                if mismatch <= 3:
                    logger.warning(
                        f"[fundflow] {code} {row['trade_date']} 收盘价不符: "
                        f"日线={daily_close} 资金流接口={row['_close']}"
                    )
        return checked, mismatch

    # --- 主流程 ---

    def collect_capital_flow(self, stock_code: str) -> None:
        """采集单只股票的主力资金流向(全量 UPSERT)"""
        logger.info(f"[fundflow] 开始采集 {stock_code} 主力资金")
        klines = self._fetch_klines(stock_code)
        if not klines:
            logger.info(f"[fundflow] {stock_code} 无资金流数据(可能非股票标的, 如 ETF)")
            return

        df = self._parse(stock_code, klines)
        if df.empty:
            logger.warning(f"[fundflow] {stock_code} 解析后无有效行")
            return

        self._check_identities(stock_code, df)

        checked, mismatch = self._cross_check_close(df)
        if checked:
            logger.info(
                f"[fundflow] {stock_code} 收盘价交叉校验: "
                f"{checked - mismatch}/{checked} 与日线一致"
            )

        out = df[["stock_code", "trade_date"] + _VALUE_COLUMNS]
        out = out.sort_values("trade_date").reset_index(drop=True)

        # 水位写"实际覆盖到的最大交易日期", 不是执行日期(见 README 的水位语义约定)
        watermark = pd.to_datetime(out["trade_date"]).max().date().strftime("%Y-%m-%d")
        try:
            # 事务保证: 数据写入 + 水位更新 原子化
            with self.db_ops.transaction():
                # UPSERT(DO UPDATE): 当日盘中采到的临时值会被收盘后的最终值覆盖
                self.db_ops.insert_dataframe(
                    "capital_flow", out,
                    conflict_columns=["stock_code", "trade_date"],
                    update_columns=_VALUE_COLUMNS,
                )
                self.db_ops.update_last_update_date(stock_code, "capital_flow", watermark)
            logger.info(
                f"[fundflow] {stock_code} 主力资金完成, 写入 {len(out)} 条 "
                f"({out['trade_date'].min()} ~ {watermark})"
            )
        except Exception as e:
            logger.error(f"[fundflow] {stock_code} 主力资金写入失败, 已回滚: {e}")
            raise
