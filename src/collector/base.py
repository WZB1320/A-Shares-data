import time
import logging
from functools import wraps
from typing import Optional, Callable

logger = logging.getLogger(__name__)


def retry(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0):
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            current_delay = delay

            while attempt < max_attempts:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    attempt += 1
                    if attempt >= max_attempts:
                        logger.error(f"重试 {max_attempts} 次后失败: {e}")
                        raise

                    logger.warning(f"第{attempt} 次失败，{current_delay:.1f} 秒后重试: {e}")
                    time.sleep(current_delay)
                    current_delay *= backoff

        return wrapper

    return decorator


class BaseCollector:
    def __init__(self, db_ops, start_date: str):
        self.db_ops = db_ops
        self.start_date = start_date

    def collect_all(self, stock_codes: list):
        for stock_code in stock_codes:
            try:
                logger.info(f"开始采集 {stock_code}")
                self.collect_stock(stock_code)
                logger.info(f"{stock_code} 采集完成")
            except Exception as e:
                logger.error(f"{stock_code} 采集失败: {e}")

    def collect_stock(self, stock_code: str):
        raise NotImplementedError