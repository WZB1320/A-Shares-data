import time
import logging
from functools import wraps
from typing import Optional, Callable, List, Tuple, Any

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

    def collect_with_fallback(
        self,
        stock_code: str,
        sources: List[Tuple[Callable[[], Any], str]],
        step_name: str = "采集",
    ) -> Any:
        """多源降级执行器：按优先级依次尝试，前源抛异常时降级到下一源

        设计参考竞品 a-stock-data v3.9.0 多源降级架构：
        - 抛异常 -> 触发降级到下一源
        - 正常返回（含返回 None / 空 DataFrame）-> 不降级，由各采集方法自行处理空结果

        Args:
            stock_code: 股票代码
            sources: [(callable, source_name)] 按优先级排序的可调用对象列表，
                     每个 callable 是无参函数，内部已绑定 stock_code
            step_name: 步骤名（用于日志）

        Returns:
            第一个成功的源的结果；所有源都失败时抛出最后一个异常
        """
        last_error: Optional[Exception] = None
        for idx, (callable_obj, source_name) in enumerate(sources):
            try:
                result = callable_obj()
                if idx > 0:
                    logger.info(f"[{source_name}] {stock_code} {step_name} 备源成功")
                return result
            except Exception as e:
                last_error = e
                if idx < len(sources) - 1:
                    logger.warning(
                        f"[{source_name}] {stock_code} {step_name} 失败: {e}，降级到下一源"
                    )
                else:
                    logger.error(f"[{source_name}] {stock_code} {step_name} 失败: {e}")
                continue
        # 所有源都失败
        if last_error:
            raise last_error
        return None