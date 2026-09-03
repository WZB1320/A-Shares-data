import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from src.config import DB_PATH, BACKUP_DIR, START_DATE, STOCK_CODES
from src.database.models import init_tables
from src.database.operations import DatabaseOperations
from src.database.backup import BackupManager
from src.collector.multi_source_collector import MultiSourceCollector
from src.collector.baostock_collector import BaostockCollector
from src.collector.rate_limiter import set_akshare_rate
from src.indicators.technical import TechnicalIndicatorCalculator
from src.indicators.financial import FinancialIndicatorCalculator
from src.indicators.valuation import ValuationIndicatorCalculator

logger = logging.getLogger(__name__)


class Scheduler:
    """数据采集与计算调度器

    并发模型:
      - 采集阶段: ThreadPoolExecutor 并行采集多只股票
        每只股票的采集任务独立,网络请求并行
        写入通过 ConnectionManager 的写锁串行化,保证数据一致性
      - 计算阶段: 串行执行(指标计算依赖已采集数据,且 CPU 密集型)
      - 备份阶段: 关闭写连接释放锁,独立连接做 CHECKPOINT + 备份

    线程安全保证:
      - DatabaseOperations 通过 ConnectionManager 共享写连接 + 写锁
      - mootdx client 通过 threading.local 每线程独立
      - BaoStock bs 模块通过全局锁串行化
      - AKShare 通过 RateLimiter 限流防封 IP
    """

    def __init__(self, max_workers: int = 4):
        """初始化调度器

        Args:
            max_workers: 采集阶段最大并发线程数
                         建议 2-4,过大易触发数据源限流
        """
        # 初始化共享 ConnectionManager
        DatabaseOperations.init_connection_manager(DB_PATH, read_pool_size=max_workers + 2)

        self.db_ops = DatabaseOperations()
        self.backup_manager = BackupManager(DB_PATH, BACKUP_DIR)
        self.max_workers = max_workers

        # 采集器在 run() 中按线程创建,避免共享非线程安全资源
        # 此处仅创建一个用于初始化表结构
        init_tables(self.db_ops.conn)

        # 指标计算器(计算阶段串行,可共享 db_ops)
        self.tech_calc = TechnicalIndicatorCalculator(self.db_ops)
        self.fin_calc = FinancialIndicatorCalculator(self.db_ops)
        self.val_calc = ValuationIndicatorCalculator(self.db_ops)

        # AKShare 限流: 并发越高,限流越保守
        akshare_max_calls = max(1, 2 // max_workers) if max_workers > 0 else 2
        set_akshare_rate(max_calls=akshare_max_calls, period=1.0)

    def run(self, stock_codes=None):
        if stock_codes is None:
            stock_codes = STOCK_CODES

        logger.info(f"启动数据采集与计算流程 (并发数={self.max_workers}, 股票数={len(stock_codes)})")

        # ========== 阶段1: 并行采集 ==========
        self._collect_parallel(stock_codes)

        # ========== 阶段1.5: 整体北向资金(不依赖个股,只执行一次) ==========
        self._collect_northbound_market_flow()

        # ========== 阶段2: 串行计算指标 ==========
        self._calculate_indicators(stock_codes)

        # ========== 阶段3: 备份 ==========
        self._backup_and_verify()

        logger.info("流程完成")

    def _collect_parallel(self, stock_codes: list):
        """并行采集阶段

        每只股票一个任务,通过 ThreadPoolExecutor 并行执行。
        采集器实例每线程独立创建,避免共享非线程安全资源。
        """
        logger.info(f"开始并行采集 {len(stock_codes)} 只股票")

        # 创建线程独立的采集器
        # 由于 MultiSourceCollector 内部持有 mootdx/baostock/tencent/akshare 采集器
        # 而这些采集器现在都是线程安全的(threading.local / 全局锁 / 无状态)
        # 所以可以共享同一个 collector 实例
        collector = MultiSourceCollector(self.db_ops, START_DATE)

        with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="collector") as executor:
            futures = {
                executor.submit(self._collect_one_stock, collector, code): code
                for code in stock_codes
            }

            completed = 0
            for future in as_completed(futures):
                code = futures[future]
                try:
                    future.result()
                    completed += 1
                    logger.info(f"采集进度: {completed}/{len(stock_codes)} ({code} 完成)")
                except Exception as e:
                    completed += 1
                    logger.error(f"{code} 采集流程异常: {e}", exc_info=True)

        # 采集完成后统一登出 BaoStock
        BaostockCollector.global_logout()

    def _collect_northbound_market_flow(self):
        """采集北向资金整体流向(独立于个股,只执行一次)"""
        try:
            collector = MultiSourceCollector(self.db_ops, START_DATE)
            collector.akshare.collect_northbound_market_flow()
        except Exception as e:
            logger.error(f"北向资金整体流向采集失败: {e}", exc_info=True)

    def _collect_one_stock(self, collector: MultiSourceCollector, stock_code: str):
        """采集单只股票的所有数据(在线程中执行)"""
        try:
            collector.collect_stock(stock_code)
        except Exception as e:
            logger.error(f"{stock_code} 采集异常: {e}", exc_info=True)
            raise

    def _calculate_indicators(self, stock_codes: list):
        """串行计算指标阶段

        指标计算依赖已采集的完整数据,且为 CPU 密集型操作,
        并发收益有限且会增加锁竞争,故保持串行。
        """
        logger.info(f"开始串行计算 {len(stock_codes)} 只股票的指标")

        for stock_code in stock_codes:
            try:
                self._calculate_daily_basic(stock_code)
            except Exception as e:
                logger.error(f"{stock_code} 日线基础指标计算失败: {e}")

            try:
                self.tech_calc.calculate_for_stock(stock_code)
            except Exception as e:
                logger.error(f"{stock_code} 技术指标计算失败: {e}")

            try:
                self.fin_calc.calculate_for_stock(stock_code)
            except Exception as e:
                logger.error(f"{stock_code} 财务指标计算失败: {e}")

            try:
                self.val_calc.calculate_for_stock(stock_code)
            except Exception as e:
                logger.error(f"{stock_code} 估值指标计算失败: {e}")

            try:
                self._calculate_northbound_accumulation(stock_code)
            except Exception as e:
                logger.error(f"{stock_code} 北向资金累计计算失败: {e}")

        logger.info("指标计算阶段完成")

    def _backup_and_verify(self):
        """备份与校验阶段

        关键操作:
        1. 提交所有未提交事务(兜底)
        2. CHECKPOINT 将 WAL 刷入主文件
        3. 关闭写连接 + 读连接池释放文件句柄
        4. 备份(独立连接做 CHECKPOINT)
        5. 重新打开连接做完整性校验
        """
        logger.info("开始备份阶段")

        # 备份前关键操作:
        # 1. 提交所有未提交事务(虽然各采集器已自行提交,这里兜底)
        # 2. CHECKPOINT 将 WAL 刷入主文件,保证备份完整
        # 3. 关闭写连接 + 读连接池释放文件句柄,避免备份时文件占用(WinError 32)
        try:
            self.db_ops.commit()  # 兜底提交,若已在事务外则是 no-op
        except Exception as e:
            logger.warning(f"备份前提交事务失败: {e}")

        # CHECKPOINT 必须在关闭写连接前执行,否则 WAL 数据可能未刷入主文件
        try:
            self.db_ops.cm.checkpoint()
        except Exception as e:
            logger.warning(f"备份前 CHECKPOINT 失败: {e}")

        # 关闭写连接 + 读连接池,彻底释放数据库文件句柄
        self.db_ops.close()  # 关闭写连接
        self.db_ops.cm.close_readers()  # 关闭所有读连接
        time.sleep(0.5)

        # 备份: create_backup 内部会执行 CHECKPOINT
        # 此处传入 None,由 backup_manager 用独立连接做 CHECKPOINT
        try:
            self.backup_manager.create_backup()
        except Exception as e:
            logger.warning(f"数据库备份失败: {e}")

        # 完整性校验(用读连接即可,acquire_reader 会自动重建连接)
        try:
            with self.db_ops.cm.acquire_reader() as conn:
                self.backup_manager.check_integrity(conn)
        except Exception as e:
            logger.warning(f"数据完整性检查失败: {e}")

    def _calculate_daily_basic(self, stock_code: str):
        df = self.db_ops.query("""
        SELECT stock_code, trade_date, open, high, low, close
        FROM stock_daily
        WHERE stock_code = ?
        ORDER BY trade_date
        """, (stock_code,))

        if df.empty:
            return

        df = df.sort_values('trade_date').reset_index(drop=True)
        df['prev_close'] = df['close'].shift(1)
        df['change_pct'] = (df['close'] - df['prev_close']) / df['prev_close'] * 100
        df['amplitude'] = (df['high'] - df['low']) / df['prev_close'] * 100
        df['body_size'] = abs(df['close'] - df['open'])
        df['upper_shadow'] = df['high'] - df[['open', 'close']].max(axis=1)
        df['lower_shadow'] = df[['open', 'close']].min(axis=1) - df['low']
        df['high_20'] = df['high'].rolling(window=20, min_periods=1).max()
        df['low_20'] = df['low'].rolling(window=20, min_periods=1).min()
        df['high_60'] = df['high'].rolling(window=60, min_periods=1).max()
        df['low_60'] = df['low'].rolling(window=60, min_periods=1).min()

        # 事务保证: 全表 UPDATE 原子化
        with self.db_ops.transaction():
            self.db_ops.conn.register("df_update", df)
            self.db_ops.conn.execute("""
            UPDATE stock_daily
            SET prev_close = df_update.prev_close,
                change_pct = df_update.change_pct,
                amplitude = df_update.amplitude,
                body_size = df_update.body_size,
                upper_shadow = df_update.upper_shadow,
                lower_shadow = df_update.lower_shadow,
                high_20 = df_update.high_20,
                low_20 = df_update.low_20,
                high_60 = df_update.high_60,
                low_60 = df_update.low_60
            FROM df_update
            WHERE stock_daily.stock_code = df_update.stock_code
              AND stock_daily.trade_date = df_update.trade_date
            """)
            self.db_ops.conn.unregister("df_update")

    def _calculate_northbound_accumulation(self, stock_code: str):
        try:
            logger.info(f"计算 {stock_code} 北向资金累计流入")

            df = self.db_ops.query("""
            SELECT stock_code, trade_date, net_inflow
            FROM northbound_flow
            WHERE stock_code = ?
            ORDER BY trade_date
            """, (stock_code,))

            if df.empty or len(df) < 5:
                logger.info(f"{stock_code} 北向资金数据不足")
                return

            df = df.sort_values('trade_date').reset_index(drop=True)
            df['inflow_5d'] = df['net_inflow'].rolling(5, min_periods=1).sum()
            df['inflow_10d'] = df['net_inflow'].rolling(10, min_periods=1).sum()
            df['inflow_30d'] = df['net_inflow'].rolling(30, min_periods=1).sum()

            # 事务保证: 累计值 UPDATE 原子化
            with self.db_ops.transaction():
                self.db_ops.conn.register("df_nb_acc", df)
                self.db_ops.conn.execute("""
                INSERT INTO northbound_flow
                (stock_code, trade_date, net_inflow, inflow_5d, inflow_10d, inflow_30d)
                SELECT stock_code, trade_date, net_inflow, inflow_5d, inflow_10d, inflow_30d
                FROM df_nb_acc
                ON CONFLICT (stock_code, trade_date) DO UPDATE
                SET inflow_5d = EXCLUDED.inflow_5d,
                    inflow_10d = EXCLUDED.inflow_10d,
                    inflow_30d = EXCLUDED.inflow_30d
                """)
                self.db_ops.conn.unregister("df_nb_acc")

            logger.info(f"{stock_code} 北向资金累计流入计算完成")

        except Exception as e:
            logger.error(f"{stock_code} 北向资金累计流入计算失败：{str(e)}")

    def close(self):
        """关闭所有连接(进程退出时调用)"""
        DatabaseOperations.close_all()
