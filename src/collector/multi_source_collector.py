"""多数据源编排器 - 统一调度各数据源采集器

数据源分工:
- mootdx: 日线行情 (TCP直连, 最稳定)
- baostock: 股本/行业/分红 (免费稳定)
- tencent: 估值PE/PB (零鉴权)
- akshare: 融资融券/公告/龙虎榜 (独有数据)
- financial_service: 财务数据 (主源 AKShare Sina + 备源 BaoStock)

降级链:
- 日线行情: mootdx主 -> AKShare备
- 财务数据: AKShare Sina主 -> BaoStock备 (字段最小集, 用 COALESCE 保护已有数据)
"""
import logging
from .base import BaseCollector
from .mootdx_collector import MootdxCollector
from .baostock_collector import BaostockCollector
from .tencent_collector import TencentCollector
from .eastmoney import EastmoneyCollector
from .fundflow_collector import FundFlowCollector
from .financial_service import FinancialService

logger = logging.getLogger(__name__)


class MultiSourceCollector(BaseCollector):
    """多数据源编排器 - 按数据类型选择最优数据源"""

    def __init__(self, db_ops, start_date: str):
        super().__init__(db_ops, start_date)
        self.mootdx = MootdxCollector(db_ops, start_date)
        self.baostock = BaostockCollector(db_ops, start_date)
        self.tencent = TencentCollector(db_ops, start_date)
        self.akshare = EastmoneyCollector(db_ops, start_date)
        self.fundflow = FundFlowCollector(db_ops, start_date)
        self.financial_service = FinancialService(db_ops, start_date)

    def collect_stock(self, stock_code: str):
        """按优先级依次采集各类型数据"""
        steps = [
            # 日线行情: mootdx主, AKShare备(内置于mootdx)
            ("日线行情", self._collect_daily),

            # 股本/行业/分红: BaoStock
            ("股本数据", self._collect_capital),
            ("行业数据", self._collect_industry),
            ("分红数据", self._collect_dividend),

            # 财务数据: AKShare Sina主 -> BaoStock备 (走降级基类)
            ("财务数据", self._collect_financial),
            ("财务补充", self._collect_financial_supplement),
            ("资产负债表补充", self._collect_balance_sheet),

            # 估值数据: 腾讯财经
            ("估值数据", self._collect_valuation),

            # 融资融券: AKShare (BaoStock无此API)
            ("融资融券", self._collect_margin),

            # AKShare独有数据
            ("公告数据", self._collect_announcements),
            ("龙虎榜", self._collect_dragon_tiger),

            # 北向资金: AKShare (仅沪深港通标的有数据)
            ("北向资金", self._collect_northbound),

            # 主力资金流向: 东方财富 push2his (自写 requests, 走 http)
            ("主力资金", self._collect_capital_flow),
        ]

        for name, method in steps:
            try:
                method(stock_code)
            except Exception as e:
                logger.error(f"{stock_code} {name}采集失败: {e}")

    def _collect_daily(self, stock_code: str):
        """日线行情: mootdx -> AKShare回退"""
        try:
            self.mootdx.collect_daily_data(stock_code)
        except Exception as e:
            logger.warning(f"[mootdx] {stock_code} 日线失败, 尝试AKShare: {e}")
            self.mootdx._fallback_akshare(stock_code)

    def _collect_capital(self, stock_code: str):
        """股本数据: BaoStock"""
        self.baostock.collect_capital_data(stock_code)

    def _collect_industry(self, stock_code: str):
        """行业数据: BaoStock"""
        self.baostock.collect_industry_data(stock_code)

    def _collect_dividend(self, stock_code: str):
        """分红数据: BaoStock"""
        self.baostock.collect_dividend_data(stock_code)

    def _collect_financial(self, stock_code: str):
        """财务数据: AKShare Sina主 -> AKShare EM中备 -> BaoStock末备 (3 源降级)

        - 主源 Sina: 字段最全 (19 字段), 全字段覆盖
        - 中备 EM:   字段接近主源 (18 字段), 用 COALESCE 保护已有数据
        - 末备 BaoStock: 仅 net_profit + total_revenue 2 字段, 非归母口径兜底
        """
        self.collect_with_fallback(
            stock_code,
            sources=[
                (lambda: self.financial_service.collect_from_akshare(stock_code), "akshare_sina"),
                (lambda: self.financial_service.collect_from_akshare_em(stock_code), "akshare_em"),
                (lambda: self.financial_service.collect_from_baostock(stock_code), "baostock"),
            ],
            step_name="财务数据",
        )

    def _collect_financial_supplement(self, stock_code: str):
        """财务补充: AKShare EM (扣非净利润/利息支出)"""
        self.financial_service.update_missing_financial_fields(stock_code)

    def _collect_balance_sheet(self, stock_code: str):
        """资产负债表补充: AKShare EM (流动资产/流动负债)"""
        self.financial_service.update_balance_sheet_fields(stock_code)

    def _collect_valuation(self, stock_code: str):
        """估值数据: 腾讯财经"""
        self.tencent.collect_valuation_data(stock_code)

    def _collect_margin(self, stock_code: str):
        """融资融券: AKShare"""
        self.akshare.collect_margin_trading(stock_code)

    def _collect_announcements(self, stock_code: str):
        """公告: AKShare"""
        self.akshare.collect_announcements(stock_code)

    def _collect_dragon_tiger(self, stock_code: str):
        """龙虎榜: AKShare"""
        self.akshare.collect_dragon_tiger(stock_code)

    def _collect_northbound(self, stock_code: str):
        """北向资金: AKShare (仅沪深港通标的有数据)"""
        self.akshare.collect_northbound_flow(stock_code)

    def _collect_capital_flow(self, stock_code: str):
        """主力资金流向: 东方财富 push2his"""
        self.fundflow.collect_capital_flow(stock_code)

    def close(self):
        """关闭所有数据源连接"""
        self.mootdx.close()
        self.baostock.close()
