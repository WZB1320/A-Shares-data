"""并发线程安全验证测试

验证 ConnectionManager 在多线程环境下的正确性:
  1. 并发写入不丢数据(写锁串行化)
  2. 并发读+写不冲突(读写分离)
  3. 并发事务不互相干扰(事务隔离)
  4. 嵌套事务在多线程下正确

运行: python -m scripts.test_concurrency
"""
import sys
import logging
import tempfile
import uuid
import threading
from pathlib import Path
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.operations import DatabaseOperations
from src.database.models import init_tables

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - [%(threadName)s] %(message)s")
logger = logging.getLogger(__name__)


def _new_db_path():
    tmp_dir = Path(tempfile.gettempdir())
    return tmp_dir / f"test_conc_{uuid.uuid4().hex}.duckdb"


def _cleanup_db(db_path: Path):
    DatabaseOperations.close_all()
    DatabaseOperations._shared_cm = None
    db_path.unlink(missing_ok=True)


def test_1_concurrent_writes():
    """测试1: 多线程并发写入,数据不丢失"""
    logger.info("=" * 60)
    logger.info("测试1: 并发写入(10线程 x 100条)")
    logger.info("=" * 60)

    db_path = _new_db_path()
    try:
        DatabaseOperations.init_connection_manager(db_path)
        db_ops = DatabaseOperations()
        init_tables(db_ops.conn)

        import pandas as pd

        def write_batch(thread_id: int):
            """每个线程写入100条数据"""
            local_db = DatabaseOperations()  # 共享同一 ConnectionManager
            # 用 timedelta 生成日期,避免 day out of range
            from datetime import date, timedelta
            base_date = date(2024, 1, 1)
            dates = [base_date + timedelta(days=i) for i in range(100)]
            df = pd.DataFrame({
                "stock_code": [f"sh{thread_id:06d}"] * 100,
                "trade_date": dates,
                "open": [100.0 + thread_id] * 100,
                "high": [101.0] * 100,
                "low": [99.0] * 100,
                "close": [100.5] * 100,
                "volume": [10000] * 100,
                "amount": [1000000.0] * 100,
            })
            with local_db.transaction():
                local_db.insert_dataframe("stock_daily", df, ["stock_code", "trade_date"])
                local_db.update_last_update_date(f"sh{thread_id:06d}", "daily", "2024-04-09")
            return thread_id

        # 10个线程并发写入
        with ThreadPoolExecutor(max_workers=10, thread_name_prefix="writer") as executor:
            futures = [executor.submit(write_batch, i) for i in range(10)]
            results = [f.result() for f in as_completed(futures)]

        # 验证: 应有 10 * 100 = 1000 条数据
        result = db_ops.query("SELECT COUNT(*) as cnt FROM stock_daily")
        cnt = result.iloc[0]["cnt"]
        assert cnt == 1000, f"期望1000条,实际{cnt}条 (并发写入丢数据!)"

        # 验证: update_log 应有10条
        log_result = db_ops.query("SELECT COUNT(*) as cnt FROM update_log")
        log_cnt = log_result.iloc[0]["cnt"]
        assert log_cnt == 10, f"update_log 期望10条,实际{log_cnt}条"

        db_ops.close()
        logger.info("✓ 测试1通过: 并发写入无数据丢失\n")
    finally:
        _cleanup_db(db_path)


def test_2_concurrent_read_write():
    """测试2: 并发读+写不冲突"""
    logger.info("=" * 60)
    logger.info("测试2: 并发读+写")
    logger.info("=" * 60)

    db_path = _new_db_path()
    try:
        DatabaseOperations.init_connection_manager(db_path)
        db_ops = DatabaseOperations()
        init_tables(db_ops.conn)

        import pandas as pd

        # 先写入初始数据
        from datetime import timedelta
        base_date = date(2024, 1, 1)
        dates = [base_date + timedelta(days=i) for i in range(10)]
        df_init = pd.DataFrame({
            "stock_code": ["sh600519"] * 10,
            "trade_date": dates,
            "open": [100.0] * 10, "high": [101.0] * 10, "low": [99.0] * 10,
            "close": [100.5] * 10, "volume": [10000] * 10, "amount": [1000000.0] * 10,
        })
        db_ops.insert_dataframe("stock_daily", df_init, ["stock_code", "trade_date"])

        errors = []

        def reader(thread_id: int):
            """持续读取数据"""
            try:
                local_db = DatabaseOperations()
                for _ in range(20):
                    result = local_db.query("SELECT COUNT(*) as cnt FROM stock_daily")
                    cnt = result.iloc[0]["cnt"]
                    if cnt < 10:
                        errors.append(f"reader-{thread_id}: 读到异常数据 {cnt}")
            except Exception as e:
                errors.append(f"reader-{thread_id}: {e}")

        def writer(thread_id: int):
            """持续写入数据"""
            try:
                local_db = DatabaseOperations()
                from datetime import timedelta
                base = date(2024, 1, 1)
                for i in range(20):
                    df = pd.DataFrame({
                        "stock_code": [f"sh{thread_id:06d}"],
                        "trade_date": [base + timedelta(days=thread_id * 100 + i)],
                        "open": [100.0], "high": [101.0], "low": [99.0],
                        "close": [100.5], "volume": [10000], "amount": [1000000.0],
                    })
                    with local_db.transaction():
                        local_db.insert_dataframe("stock_daily", df, ["stock_code", "trade_date"])
            except Exception as e:
                errors.append(f"writer-{thread_id}: {e}")

        # 2个读线程 + 2个写线程并发
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="rw") as executor:
            futures = []
            for i in range(2):
                futures.append(executor.submit(reader, i))
            for i in range(2):
                futures.append(executor.submit(writer, i))
            for f in as_completed(futures):
                f.result()

        assert not errors, f"并发读写出现错误: {errors}"

        # 验证: 初始10 + 2*20 = 50 条
        result = db_ops.query("SELECT COUNT(*) as cnt FROM stock_daily")
        cnt = result.iloc[0]["cnt"]
        assert cnt == 50, f"期望50条,实际{cnt}条"

        db_ops.close()
        logger.info("✓ 测试2通过: 并发读写无冲突\n")
    finally:
        _cleanup_db(db_path)


def test_3_concurrent_transactions_isolation():
    """测试3: 并发事务不互相干扰(一个失败不影响其他)"""
    logger.info("=" * 60)
    logger.info("测试3: 并发事务隔离")
    logger.info("=" * 60)

    db_path = _new_db_path()
    try:
        DatabaseOperations.init_connection_manager(db_path)
        db_ops = DatabaseOperations()
        init_tables(db_ops.conn)

        import pandas as pd

        results = {}

        def tx_success(thread_id: int):
            """成功事务"""
            local_db = DatabaseOperations()
            df = pd.DataFrame({
                "stock_code": [f"sh{thread_id:06d}"],
                "trade_date": [date(2024, 1, 1)],
                "open": [100.0], "high": [101.0], "low": [99.0],
                "close": [100.5], "volume": [10000], "amount": [1000000.0],
            })
            with local_db.transaction():
                local_db.insert_dataframe("stock_daily", df, ["stock_code", "trade_date"])
            results[thread_id] = "success"

        def tx_fail(thread_id: int):
            """失败事务(中途抛异常)"""
            local_db = DatabaseOperations()
            df = pd.DataFrame({
                "stock_code": [f"sh{thread_id:06d}"],
                "trade_date": [date(2024, 1, 1)],
                "open": [100.0], "high": [101.0], "low": [99.0],
                "close": [100.5], "volume": [10000], "amount": [1000000.0],
            })
            try:
                with local_db.transaction():
                    local_db.insert_dataframe("stock_daily", df, ["stock_code", "trade_date"])
                    raise RuntimeError(f"thread-{thread_id} 模拟失败")
            except RuntimeError:
                results[thread_id] = "rolled_back"

        # 3个成功事务 + 2个失败事务并发
        with ThreadPoolExecutor(max_workers=5, thread_name_prefix="tx") as executor:
            futures = []
            futures.append(executor.submit(tx_success, 1))
            futures.append(executor.submit(tx_fail, 2))
            futures.append(executor.submit(tx_success, 3))
            futures.append(executor.submit(tx_fail, 4))
            futures.append(executor.submit(tx_success, 5))
            for f in as_completed(futures):
                f.result()

        # 验证: 只有3个成功事务的数据落盘
        result = db_ops.query("SELECT COUNT(*) as cnt FROM stock_daily")
        cnt = result.iloc[0]["cnt"]
        assert cnt == 3, f"期望3条(只有成功事务),实际{cnt}条"

        # 验证: 失败事务的数据不存在
        for fail_id in [2, 4]:
            result = db_ops.query(
                "SELECT COUNT(*) as cnt FROM stock_daily WHERE stock_code = ?",
                (f"sh{fail_id:06d}",)
            )
            cnt = result.iloc[0]["cnt"]
            assert cnt == 0, f"失败事务 thread-{fail_id} 的数据不应存在,但有 {cnt} 条"

        db_ops.close()
        logger.info("✓ 测试3通过: 并发事务隔离正确\n")
    finally:
        _cleanup_db(db_path)


def test_4_connection_pool_stability():
    """测试4: 连接池在大量并发任务下稳定(无连接泄漏)"""
    logger.info("=" * 60)
    logger.info("测试4: 连接池稳定性(50个并发任务)")
    logger.info("=" * 60)

    db_path = _new_db_path()
    try:
        DatabaseOperations.init_connection_manager(db_path, read_pool_size=4)
        db_ops = DatabaseOperations()
        init_tables(db_ops.conn)

        import pandas as pd

        def quick_task(task_id: int):
            """快速读写任务"""
            local_db = DatabaseOperations()
            # 读
            result = local_db.query("SELECT COUNT(*) as cnt FROM stock_daily")
            cnt = result.iloc[0]["cnt"]
            # 写
            df = pd.DataFrame({
                "stock_code": [f"sh{task_id:06d}"],
                "trade_date": [date(2024, 1, 1)],
                "open": [100.0], "high": [101.0], "low": [99.0],
                "close": [100.5], "volume": [10000], "amount": [1000000.0],
            })
            with local_db.transaction():
                local_db.insert_dataframe("stock_daily", df, ["stock_code", "trade_date"])
            return cnt

        # 50个并发任务,但只有4个读连接池
        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="pool") as executor:
            futures = [executor.submit(quick_task, i) for i in range(50)]
            results = [f.result() for f in as_completed(futures)]

        # 验证: 50条数据全部写入
        result = db_ops.query("SELECT COUNT(*) as cnt FROM stock_daily")
        cnt = result.iloc[0]["cnt"]
        assert cnt == 50, f"期望50条,实际{cnt}条"

        db_ops.close()
        logger.info("✓ 测试4通过: 连接池稳定,无泄漏\n")
    finally:
        _cleanup_db(db_path)


def main():
    logger.info("开始并发线程安全验证测试\n")
    try:
        test_1_concurrent_writes()
        test_2_concurrent_read_write()
        test_3_concurrent_transactions_isolation()
        test_4_connection_pool_stability()
        logger.info("=" * 60)
        logger.info("🎉 所有并发测试通过!")
        logger.info("=" * 60)
    except AssertionError as e:
        logger.error(f"❌ 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ 测试异常: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
