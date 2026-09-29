"""AKShare 限流器 - 防止并发请求被封 IP

AKShare 底层调用东方财富/新浪等 HTTP API,高频请求会被封 IP。
本模块提供:
  - 全局速率限制器: 限制 AKShare 调用频率
  - 线程安全: 通过 Lock 保证多线程环境下速率控制正确
"""
import socket
import threading
import time
import logging
from contextlib import contextmanager
from functools import wraps
from typing import Callable

logger = logging.getLogger(__name__)


class RateLimiter:
    """令牌桶限流器(线程安全)

    用法:
        limiter = RateLimiter(max_calls=2, period=1.0)  # 每秒最多2次
        limiter.acquire()  # 阻塞直到获得令牌
        ak.some_api(...)
    """

    def __init__(self, max_calls: int = 2, period: float = 1.0):
        """
        Args:
            max_calls: period 时间窗口内最大调用次数
            period: 时间窗口(秒)
        """
        self.max_calls = max_calls
        self.period = period
        self._lock = threading.Lock()
        self._timestamps = []  # 最近调用时间戳列表

    def acquire(self):
        """获取一个令牌(阻塞直到符合速率限制)"""
        with self._lock:
            now = time.time()
            # 清理过期时间戳
            cutoff = now - self.period
            self._timestamps = [t for t in self._timestamps if t > cutoff]

            if len(self._timestamps) >= self.max_calls:
                # 需要等待最早的时间戳过期
                wait_time = self._timestamps[0] + self.period - now
                if wait_time > 0:
                    logger.debug(f"[RateLimiter] 限流等待 {wait_time:.2f}s")
                    time.sleep(wait_time)
                    # 重新计算
                    now = time.time()
                    cutoff = now - self.period
                    self._timestamps = [t for t in self._timestamps if t > cutoff]

            self._timestamps.append(now)

    def call(self, func: Callable, *args, **kwargs):
        """限流调用函数"""
        self.acquire()
        return func(*args, **kwargs)


# 全局 AKShare 限流器实例
# AKShare 默认调用东方财富/新浪 API,建议保守: 每秒2次
_akshare_limiter = RateLimiter(max_calls=2, period=1.0)


def akshare_rate_limited(func: Callable):
    """AKShare 调用限流装饰器

    用法:
        @akshare_rate_limited
        def my_akshare_call():
            return ak.some_api(...)
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        _akshare_limiter.acquire()
        return func(*args, **kwargs)
    return wrapper


def get_akshare_limiter() -> RateLimiter:
    """获取全局 AKShare 限流器"""
    return _akshare_limiter


def set_akshare_rate(max_calls: int = 2, period: float = 1.0):
    """调整 AKShare 限流参数

    Args:
        max_calls: period 时间窗口内最大调用次数
        period: 时间窗口(秒)
    """
    global _akshare_limiter
    _akshare_limiter = RateLimiter(max_calls=max_calls, period=period)
    logger.info(f"AKShare 限流参数调整: {max_calls}次/{period}秒")


@contextmanager
def socket_timeout(seconds: float):
    """临时为全局 socket 设置默认超时, 退出时恢复原值。

    用途: akshare 内部用 requests 发 HTTP 请求, 但不暴露 timeout 参数。
    网络半死连接会让调用线程无限期阻塞 —— 实测融资融券采集曾挂起 25 分钟,
    且因为异常被静默吞掉而完全无日志。requests 在未显式传 timeout 时会
    回退到 socket 默认超时, 因此在这里设置是有效的。

    注意: 该设置是进程级的, 会影响并发线程新建的连接。采集场景下以
    "宁可超时失败后重试/跳过, 不可无限挂起"为准, 因此可接受。
    """
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(seconds)
    try:
        yield
    finally:
        socket.setdefaulttimeout(previous)
