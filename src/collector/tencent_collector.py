"""腾讯财经数据采集器 - 获取估值指标(PE/PB/市值等)

优势: 零鉴权、不封IP、字段丰富(88个)
数据: PE(TTM)/PE(静态)/PB/总市值/流通市值/换手率等
"""
import pandas as pd
import logging
import requests
from datetime import datetime
from .base import BaseCollector, retry

logger = logging.getLogger(__name__)


class TencentCollector(BaseCollector):
    """腾讯财经估值数据采集器"""

    # 腾讯财经字段索引映射
    # 依据: 2026-09-29 对 sz000858 / sh600519 / sh600585 逐位实测(响应总段数 88),
    #       并用"昨收 ×1.1 = 涨停价""总市值 = 最新价 × 股本"等恒等式交叉校验。
    # 单位: volume=手, amount=万元, *_mv=亿元, turnover/amplitude/change_pct/volume_ratio=%
    #
    # 历史缺陷(已修): 旧映射整块错位 —— 30 实为行情时间戳、31 实为涨跌额、
    #   38 实为换手率、39 实为 PE(TTM)、43 实为振幅、44 实为流通市值、45 实为总市值;
    #   该接口根本不提供 PE(静态)/EPS/BVPS。后果是 pe_ttm 被写入"涨跌额"
    #   (如海螺水泥 0.21), 在 valuation_indicators 中留下垃圾行。
    FIELD_MAP = {
        3: 'close',           # 最新价
        4: 'prev_close',      # 昨收
        5: 'open',            # 开盘
        6: 'volume',          # 成交量(手)
        7: 'outer_vol',       # 外盘
        8: 'inner_vol',       # 内盘
        9: 'buy1_vol',        # 买一量
        11: 'buy1_price',     # 买一价
        30: 'quote_time',     # 行情时间戳 YYYYMMDDHHMMSS(不是换手率)
        31: 'change',         # 涨跌额(不是 PE)
        32: 'change_pct',     # 涨跌幅 %(不是静态 PE)
        33: 'high',           # 最高价
        34: 'low',            # 最低价
        37: 'amount',         # 成交额(万元)
        38: 'turnover',       # 换手率 %      ← 修正: 原误映射为 total_mv
        39: 'pe_ttm',         # PE(TTM)      ← 修正: 原误映射为 circ_mv
        43: 'amplitude',      # 振幅 %(不是 EPS)
        44: 'circ_mv',        # 流通市值(亿元) ← 修正: 原误映射为 bvps
        45: 'total_mv',       # 总市值(亿元)   ← 修正: 原误映射为 total_shares(且误标"万")
        46: 'pb',             # PB           (原映射即正确)
        47: 'high_limit',     # 涨停价
        48: 'low_limit',      # 跌停价
        49: 'volume_ratio',   # 量比
    }

    def collect_stock(self, stock_code: str):
        steps = [
            ("估值数据(腾讯)", self.collect_valuation_data),
        ]
        for name, method in steps:
            try:
                method(stock_code)
            except Exception as e:
                logger.error(f"{stock_code} {name}采集失败: {e}")

    @retry(max_attempts=3, delay=1.0)
    def collect_valuation_data(self, stock_code: str):
        """通过腾讯财经获取估值数据"""
        logger.info(f"[tencent] 开始采集 {stock_code} 估值数据")

        tencent_code = self._to_tencent_code(stock_code)
        url = f"http://qt.gtimg.cn/q={tencent_code}"

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://gu.qq.com/'
        }

        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = 'gbk'
        text = resp.text.strip()

        if not text or '~' not in text:
            logger.warning(f"[tencent] {stock_code} 未获取到数据")
            return

        # 解析腾讯财经数据格式: v_sh600519="1~贵州茅台~600519~..."
        parts = text.split('~')
        if len(parts) < 50:
            logger.warning(f"[tencent] {stock_code} 数据格式异常")
            return

        data = {}
        for idx, col_name in self.FIELD_MAP.items():
            try:
                val = parts[idx] if idx < len(parts) else None
                if val and val != '':
                    try:
                        data[col_name] = float(val)
                    except (ValueError, TypeError):
                        data[col_name] = val
            except Exception:
                pass

        if not data:
            return

        # 存入估值指标表
        today = datetime.now().strftime("%Y-%m-%d")
        pe_ttm = data.get('pe_ttm')
        pb = data.get('pb')

        # 类型护栏: 非数值(接口异常/字段错位)一律视为无效, 不写库
        if not isinstance(pe_ttm, (int, float)):
            pe_ttm = None
        if not isinstance(pb, (int, float)):
            pb = None

        if pe_ttm is None and pb is None:
            logger.warning(f"[tencent] {stock_code} PE/PB 均无效, 跳过写入")
            return data

        # 孤儿行防护: 仅当该股票当日日线已入库时才写估值。
        # 原因: 估值表通过 (stock_code, trade_date) 与日线关联, 若先于日线写入
        # "今天"的行, 调用方按日期 JOIN 日线时必然取不到 close(返回 null/0)。
        # 估值以本地计算器(基于日线+财报, 先 DELETE 后重写)为权威源, 此处仅为补充。
        has_daily = self.db_ops.conn.execute(
            "SELECT 1 FROM stock_daily WHERE stock_code = ? AND trade_date = ? LIMIT 1",
            (stock_code, today),
        ).fetchone()
        if not has_daily:
            logger.info(
                f"[tencent] {stock_code} 日线尚无 {today}, 跳过估值写入(避免孤儿行)"
            )
            return data

        # 事务保证估值数据写入原子化
        with self.db_ops.transaction():
            self.db_ops.conn.execute("""
            INSERT INTO valuation_indicators (stock_code, trade_date, pe_ttm, pb)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (stock_code, trade_date) DO UPDATE SET
                pe_ttm = COALESCE(EXCLUDED.pe_ttm, valuation_indicators.pe_ttm),
                pb = COALESCE(EXCLUDED.pb, valuation_indicators.pb)
            """, (stock_code, today, pe_ttm, pb))
        logger.info(f"[tencent] {stock_code} 估值: PE(TTM)={pe_ttm}, PB={pb}")

        # 注意: 腾讯接口索引45返回的是流通股本(万股), 不是总股本
        # 总股本由 BaoStock 采集, 此处不再写入 stock_capital 避免覆盖正确数据
        # 历史bug: 之前将流通股本*10000写入stock_capital, 导致total_shares偏小2个数量级

        return data

    @staticmethod
    def _to_tencent_code(stock_code: str) -> str:
        """sh600519 -> sh600519 (腾讯格式)"""
        return stock_code
