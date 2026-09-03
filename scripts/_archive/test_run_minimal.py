"""最小化运行测试: 只采集 1 只股票,验证整个采集→计算→备份流程

用法: python -m scripts.test_run_minimal
"""
import sys
import logging
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import LOG_LEVEL
from src.scheduler import Scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - [%(threadName)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    logger.info("=" * 60)
    logger.info("最小化运行测试: 采集 1 只股票 (sh600519)")
    logger.info("=" * 60)

    start = time.time()
    try:
        # 只采集 1 只股票,验证整个流程
        scheduler = Scheduler(max_workers=2)
        scheduler.run(stock_codes=["sh600519"])
        scheduler.close()
        elapsed = time.time() - start
        logger.info("=" * 60)
        logger.info(f"✓ 运行成功! 总耗时: {elapsed:.1f}s")
        logger.info("=" * 60)
    except Exception as e:
        elapsed = time.time() - start
        logger.error("=" * 60)
        logger.error(f"✗ 运行失败! 耗时: {elapsed:.1f}s")
        logger.error(f"错误: {e}", exc_info=True)
        logger.error("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
