"""真实采集压测: 对比串行 vs 并发耗时

选取 12 只股票,分别用:
  1. 串行模式: 逐只采集
  2. 并发模式: ThreadPoolExecutor 并行采集

对比总耗时、平均耗时、加速比。

注意:
  - 为避免数据污染,使用独立的临时数据库
  - 采集真实数据(网络请求),耗时受网络环境影响
  - 并发模式下 AKShare 限流为 2 calls/s,避免被封 IP

用法:
  python -m scripts.benchmark_concurrency              # 完整压测
  python -m scripts.benchmark_concurrency --serial-only # 仅串行
  python -m scripts.benchmark_concurrency --concurrent-only --serial-time 2502.2  # 仅并发,复用串行耗时
"""
import sys
import logging
import time
import tempfile
import uuid
import shutil
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent.parent))

# 在导入其他模块前,先覆盖配置路径,避免污染生产数据库
import src.config as config

_tmp_dir = Path(tempfile.gettempdir()) / f"bench_{uuid.uuid4().hex}"
_tmp_dir.mkdir(parents=True, exist_ok=True)
config.DB_PATH = _tmp_dir / "bench.duckdb"
config.BACKUP_DIR = _tmp_dir / "backups"
for d in [config.DB_PATH.parent, config.BACKUP_DIR]:
    d.mkdir(parents=True, exist_ok=True)

from src.config import STOCK_CODES, START_DATE
from src.database.operations import DatabaseOperations
from src.database.models import init_tables
from src.collector.multi_source_collector import MultiSourceCollector
from src.collector.baostock_collector import BaostockCollector
from src.collector.rate_limiter import set_akshare_rate

logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(levelname)s - [%(threadName)s] %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _cleanup():
    """清理临时数据库和 parquet 文件"""
    DatabaseOperations.close_all()
    DatabaseOperations._shared_cm = None
    if _tmp_dir.exists():
        shutil.rmtree(_tmp_dir, ignore_errors=True)


def collect_one_stock(db_ops, stock_code: str):
    """采集单只股票(供串行和并发模式共用)"""
    collector = MultiSourceCollector(db_ops, START_DATE)
    collector.collect_stock(stock_code)


def run_serial(stock_codes: list) -> float:
    """串行采集"""
    logger.info("=" * 60)
    logger.info(f"串行模式: 采集 {len(stock_codes)} 只股票")
    logger.info("=" * 60)

    DatabaseOperations.init_connection_manager(config.DB_PATH)
    db_ops = DatabaseOperations()
    init_tables(db_ops.conn)

    # 串行模式不限流(AKShare 单线程调用不会被封)
    set_akshare_rate(max_calls=2, period=1.0)

    start = time.time()
    for i, code in enumerate(stock_codes, 1):
        t0 = time.time()
        try:
            collect_one_stock(db_ops, code)
            logger.info(f"[串行] {i}/{len(stock_codes)} {code} 完成 ({time.time()-t0:.1f}s)")
        except Exception as e:
            logger.error(f"[串行] {i}/{len(stock_codes)} {code} 失败: {e}")
    elapsed = time.time() - start

    BaostockCollector.global_logout()
    DatabaseOperations.close_all()
    DatabaseOperations._shared_cm = None

    logger.info(f"串行总耗时: {elapsed:.1f}s\n")
    return elapsed


def run_concurrent(stock_codes: list, max_workers: int = 4) -> float:
    """并发采集"""
    logger.info("=" * 60)
    logger.info(f"并发模式: 采集 {len(stock_codes)} 只股票 (max_workers={max_workers})")
    logger.info("=" * 60)

    DatabaseOperations.init_connection_manager(config.DB_PATH, read_pool_size=max_workers + 2)
    db_ops = DatabaseOperations()
    init_tables(db_ops.conn)

    # 并发模式限流: 降低 AKShare 调用频率防封
    akshare_max = max(1, 2 // max_workers)
    set_akshare_rate(max_calls=akshare_max, period=1.0)

    start = time.time()
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="bench") as executor:
        futures = {
            executor.submit(collect_one_stock, db_ops, code): code
            for code in stock_codes
        }
        completed = 0
        for future in as_completed(futures):
            code = futures[future]
            completed += 1
            try:
                future.result()
                logger.info(f"[并发] {completed}/{len(stock_codes)} {code} 完成")
            except Exception as e:
                logger.error(f"[并发] {completed}/{len(stock_codes)} {code} 失败: {e}")
    elapsed = time.time() - start

    BaostockCollector.global_logout()
    DatabaseOperations.close_all()
    DatabaseOperations._shared_cm = None

    logger.info(f"并发总耗时: {elapsed:.1f}s\n")
    return elapsed


def main():
    parser = argparse.ArgumentParser(description="并发压测脚本")
    parser.add_argument("--serial-only", action="store_true", help="仅运行串行模式")
    parser.add_argument("--concurrent-only", action="store_true", help="仅运行并发模式")
    parser.add_argument("--serial-time", type=float, default=0, help="复用已有串行耗时(秒),配合 --concurrent-only")
    parser.add_argument("--workers", type=int, default=4, help="并发线程数")
    args = parser.parse_args()

    # 选取测试股票(取前 12 只)
    test_codes = STOCK_CODES[:12]
    logger.info(f"压测股票: {test_codes}")
    logger.info(f"临时数据库: {config.DB_PATH}\n")

    serial_time = args.serial_time
    try:
        if not args.concurrent_only:
            # 串行采集
            serial_time = run_serial(test_codes)

        if not args.serial_only:
            # 清理临时数据,避免影响并发测试
            _cleanup()
            config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)

            # 并发采集
            concurrent_time = run_concurrent(test_codes, max_workers=args.workers)

            # 汇总
            logger.info("=" * 60)
            logger.info("压测结果汇总")
            logger.info("=" * 60)
            logger.info(f"股票数量:     {len(test_codes)}")
            logger.info(f"串行耗时:     {serial_time:.1f}s")
            logger.info(f"并发耗时:     {concurrent_time:.1f}s (workers={args.workers})")
            if concurrent_time > 0:
                speedup = serial_time / concurrent_time
                logger.info(f"加速比:       {speedup:.2f}x")
            logger.info(f"平均每只(串行): {serial_time/len(test_codes):.1f}s")
            if concurrent_time > 0:
                logger.info(f"平均每只(并发): {concurrent_time/len(test_codes):.1f}s")
            logger.info("=" * 60)

    finally:
        _cleanup()


if __name__ == "__main__":
    main()
