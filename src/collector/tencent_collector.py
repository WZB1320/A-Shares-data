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

    # 腾讯财经字段索引映射 (实测2026年5月)
    FIELD_MAP = {
        3: 'close',           # 最新价
        4: 'prev_close',      # 昨收
        5: 'open',            # 开盘
        6: 'volume',          # 成交量(手)
        7: 'outer_vol',       # 外盘
        8: 'inner_vol',       # 内盘
        9: 'buy1_vol',        # 买一量
        11: 'buy1_price',     # 买一价
        30: 'turnover',       # 换手率
        31: 'pe_ttm',         # PE(TTM)
        32: 'pe_static',      # PE(静态) - 注意: 网上很多教程说32=PB是错的
        38: 'total_mv',       # 总市值(万)
        39: 'circ_mv',        # 流通市值(万)
        43: 'eps',            # EPS
        44: 'bvps',           # 每股净资产
        45: 'total_shares',   # 总股本
        46: 'pb',             # PB - 正确索引是46，不是43
        47: 'high_limit',     # 涨停价
        48: 'low_limit',      # 跌停价
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
        total_mv = data.get('total_mv')
        turnover = data.get('turnover')

        if pe_ttm or pb:
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
