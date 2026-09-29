import duckdb
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .connection_manager import ConnectionManager

logger = logging.getLogger(__name__)


class DatabaseOperations:
    """数据库操作封装,提供事务管理 + 线程安全

    DuckDB Python 默认 autocommit=True,每条 SQL 立即落盘。
    本类通过显式 BEGIN/COMMIT/ROLLBACK 实现事务原子性:
      - transaction() 上下文管理器: 要么全成功提交,要么全回滚
      - 支持嵌套调用(内层变为 no-op,由最外层统一提交)
      - commit()/rollback() 显式控制

    线程安全:
      - 写操作(INSERT/UPDATE/DELETE/事务)通过 ConnectionManager._write_lock 串行化
      - 读操作(SELECT)通过线程独立读连接并发执行
      - 同一进程内所有 DatabaseOperations 实例共享同一个 ConnectionManager
    """

    # 类变量: 进程内共享的 ConnectionManager
    _shared_cm: Optional[ConnectionManager] = None
    _cm_lock = threading.Lock()

    @classmethod
    def init_connection_manager(cls, db_path: Path, read_pool_size: int = 8):
        """初始化进程级共享 ConnectionManager

        必须在创建任何 DatabaseOperations 实例前调用一次。
        多次调用会复用已存在的 ConnectionManager(忽略新参数)。
        """
        with cls._cm_lock:
            if cls._shared_cm is None:
                cls._shared_cm = ConnectionManager(db_path, read_pool_size)
                logger.info(f"ConnectionManager 已初始化: {db_path}")
            else:
                logger.debug("ConnectionManager 已存在,复用现有实例")

    @classmethod
    def get_connection_manager(cls) -> ConnectionManager:
        """获取共享的 ConnectionManager"""
        if cls._shared_cm is None:
            raise RuntimeError("ConnectionManager 未初始化,请先调用 init_connection_manager()")
        return cls._shared_cm

    def __init__(self, db_path: Optional[Path] = None):
        """初始化数据库操作

        Args:
            db_path: 数据库路径。若 ConnectionManager 已初始化则忽略此参数。
                     若未初始化,会用此路径初始化 ConnectionManager。
        """
        if DatabaseOperations._shared_cm is None:
            if db_path is None:
                raise ValueError("ConnectionManager 未初始化且未提供 db_path")
            DatabaseOperations.init_connection_manager(db_path)

        self.cm = DatabaseOperations._shared_cm
        # 兼容旧代码: 暴露 conn 属性指向写连接
        # 注意: 直接使用 self.conn 的代码需确保在写锁内或单线程环境
        self._conn_proxy = _WriteConnProxy(self.cm)

    @property
    def conn(self):
        """兼容旧代码: 返回写连接代理

        警告: 直接通过 self.conn 执行写操作不会自动加锁,
        应优先使用 self.transaction() 或 self.insert_dataframe() 等封装方法。
        """
        return self._conn_proxy

    @conn.setter
    def conn(self, value):
        """兼容旧代码: scheduler.py 中有 self.db_ops.conn = duckdb.connect(...) 的用法

        新架构下不应再手动赋值连接,此 setter 仅做兼容:
        - 若 value 为 None,关闭写连接(用于备份前释放锁)
        - 否则忽略(由 ConnectionManager 统一管理)
        """
        if value is None:
            self.cm.close_writers()
        # 其他情况忽略,连接由 ConnectionManager 管理

    def close(self):
        """关闭写连接(读连接由 ConnectionManager 统一管理)

        注意: 此方法仅关闭当前进程的写连接,不影响其他线程的读连接。
        若需关闭所有连接(如进程退出),请调用 close_all()。
        """
        self.cm.close_writers()

    @classmethod
    def close_all(cls):
        """关闭所有连接(写 + 读 + 备份)"""
        if cls._shared_cm:
            cls._shared_cm.close_all()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # 不自动关闭,由 ConnectionManager 统一管理生命周期
        pass

    @property
    def _in_transaction(self):
        return self.cm.in_transaction

    @_in_transaction.setter
    def _in_transaction(self, value):
        self.cm.in_transaction = value

    @contextmanager
    def transaction(self):
        """事务上下文管理器: 保证内部所有操作原子性 + 线程安全

        用法:
            with db_ops.transaction():
                db_ops.insert_dataframe(...)
                db_ops.update_last_update_date(...)

        特性:
          - 嵌套调用安全: 内层 transaction() 不重复开启事务,由最外层统一提交
          - 异常自动回滚并重新抛出,便于上层捕获
          - 提交失败也会回滚并抛出
          - 线程安全: 通过 ConnectionManager._write_lock 串行化写操作
        """
        cm = self.cm

        # 获取写锁(可重入,支持嵌套 transaction 调用)
        cm.write_lock.acquire()
        try:
            # 关键: is_outer 必须在锁内检查,避免多线程竞争
            # 若在锁外检查,其他线程可能已修改 in_transaction 状态
            is_outer = not cm.in_transaction
            if is_outer:
                cm.write_conn.execute("BEGIN")
                cm.in_transaction = True

            try:
                yield
                if is_outer:
                    cm.write_conn.execute("COMMIT")
                    cm.in_transaction = False
            except Exception:
                if is_outer and cm.in_transaction:
                    try:
                        cm.write_conn.execute("ROLLBACK")
                    except Exception as rollback_err:
                        logger.error(f"事务回滚失败: {rollback_err}")
                    finally:
                        cm.in_transaction = False
                raise
        finally:
            cm.write_lock.release()

    def commit(self):
        """显式提交当前事务(若在事务中)"""
        cm = self.cm
        with cm.write_lock:
            if cm.in_transaction:
                cm.write_conn.execute("COMMIT")
                cm.in_transaction = False

    def rollback(self):
        """显式回滚当前事务(若在事务中)"""
        cm = self.cm
        with cm.write_lock:
            if cm.in_transaction:
                try:
                    cm.write_conn.execute("ROLLBACK")
                finally:
                    cm.in_transaction = False

    def checkpoint(self):
        """强制将 WAL 刷入主数据库文件

        备份前必须调用,否则备份文件可能缺少最近未 checkpoint 的数据。
        若当前在事务中,会先提交再 checkpoint,避免事务未提交导致数据不一致。
        """
        self.cm.checkpoint()

    def get_last_update_date(self, stock_code: str, data_type: str, start_date: str) -> str:
        """查询水位线(读操作,使用读连接并发)"""
        with self.cm.acquire_reader() as conn:
            result = conn.execute("""
            SELECT MAX(last_update_date) FROM update_log
            WHERE stock_code = ? AND data_type = ?
            """, (stock_code, data_type)).fetchone()

        if result[0] is None:
            return start_date
        else:
            last_date = datetime.strptime(str(result[0]), "%Y-%m-%d")
            next_date = last_date + timedelta(days=1)
            return next_date.strftime("%Y%m%d")

    def update_last_update_date(self, stock_code: str, data_type: str, end_date: str):
        """更新水位线(写操作,自动加锁)"""
        end_date_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
        with self.cm.write_lock:
            self.cm.write_conn.execute("""
            INSERT INTO update_log (stock_code, data_type, last_update_date, update_time)
            VALUES (?, ?, ?, now())
            ON CONFLICT (stock_code, data_type)
            DO UPDATE SET last_update_date = EXCLUDED.last_update_date,
                          update_time = now()
            """, (stock_code, data_type, end_date_dt))

    def insert_dataframe(
        self,
        table_name: str,
        df,
        conflict_columns: Optional[list] = None,
        update_columns: Optional[list] = None,
    ):
        """插入 DataFrame(写操作,自动加锁)

        Args:
            table_name: 目标表
            df: 待写入数据
            conflict_columns: 冲突键(PK)。给定时走 ON CONFLICT 分支
            update_columns: 冲突时改为更新的列。默认 None = DO NOTHING(幂等追加);
                            给定列表时走 DO UPDATE SET, 用于"重新采到的近期数据
                覆盖旧值"的自愈场景(如资金流当日盘中值需被收盘值替换)。
                注意: 不要包含 conflict_columns 中的键列。
        """
        if df.empty:
            return

        with self.cm.write_lock:
            conn = self.cm.write_conn
            conn.register("df", df)
            columns = ", ".join(df.columns)

            if conflict_columns:
                conflict_str = ", ".join(conflict_columns)
                if update_columns:
                    set_str = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_columns)
                    conflict_action = f"DO UPDATE SET {set_str}"
                else:
                    conflict_action = "DO NOTHING"
                sql = f"""
                INSERT INTO {table_name} ({columns})
                SELECT {columns} FROM df
                ON CONFLICT ({conflict_str}) {conflict_action}
                """
            else:
                sql = f"""
                INSERT INTO {table_name} ({columns})
                SELECT {columns} FROM df
                """

            conn.execute(sql)
            conn.unregister("df")

    def query(self, sql: str, params: Optional[tuple] = None):
        """查询(读操作,使用读连接并发)"""
        with self.cm.acquire_reader() as conn:
            if params:
                return conn.execute(sql, params).fetchdf()
            else:
                return conn.execute(sql).fetchdf()

    def execute_write(self, sql: str, params: Optional[tuple] = None):
        """执行写 SQL(INSERT/UPDATE/DELETE),自动加锁

        供 collector 直接执行单条 SQL 使用,替代直接访问 self.conn。
        """
        with self.cm.write_lock:
            if params:
                self.cm.write_conn.execute(sql, params)
            else:
                self.cm.write_conn.execute(sql)

    def execute_write_many(self, sql: str, params_list):
        """批量执行写 SQL,自动加锁"""
        with self.cm.write_lock:
            self.cm.write_conn.executemany(sql, params_list)


class _WriteConnProxy:
    """写连接代理: 兼容旧代码中 self.db_ops.conn.execute() 的用法

    所有操作都会自动获取写锁,保证线程安全。
    注意: 在 transaction() 上下文内调用时,由于 RLock 可重入,不会死锁。
    """

    def __init__(self, cm: ConnectionManager):
        self._cm = cm

    @property
    def _conn(self):
        return self._cm.write_conn

    def execute(self, sql, params=None):
        # 若已在事务中,直接执行(事务已持锁)
        if self._cm.in_transaction:
            if params:
                return self._conn.execute(sql, params)
            else:
                return self._conn.execute(sql)
        # 否则加锁执行
        with self._cm.write_lock:
            if params:
                return self._conn.execute(sql, params)
            else:
                return self._conn.execute(sql)

    def register(self, name, obj):
        if self._cm.in_transaction:
            return self._conn.register(name, obj)
        with self._cm.write_lock:
            return self._conn.register(name, obj)

    def unregister(self, name):
        if self._cm.in_transaction:
            return self._conn.unregister(name)
        with self._cm.write_lock:
            return self._conn.unregister(name)

    def close(self):
        # 兼容旧代码: 不实际关闭,由 ConnectionManager 管理
        pass

    def __getattr__(self, name):
        # 透传其他属性访问(如 cursor 等)
        return getattr(self._conn, name)
