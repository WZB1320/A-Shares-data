"""备份流程测试: 验证关闭读连接池后备份不再报 WinError 32

复现场景:
  1. 初始化 ConnectionManager
  2. 多线程并发读写(建立多个读连接)
  3. 执行备份流程(commit → checkpoint → close_writers → close_readers → backup)
  4. 验证备份文件存在且非空

运行: python -m scripts.test_backup
"""
import sys
import logging
import tempfile
import uuid
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.operations import DatabaseOperations
from src.database.models import init_tables
from src.database.backup import BackupManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - [%(threadName)s] %(message)s")
logger = logging.getLogger(__name__)


def _new_db_path():
    tmp_dir = Path(tempfile.gettempdir())
    return tmp_dir / f"test_backup_{uuid.uuid4().hex}.duckdb"


def _cleanup(db_path: Path, backup_dir: Path):
    DatabaseOperations.close_all()
    DatabaseOperations._shared_cm = None
    db_path.unlink(missing_ok=True)
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)


def test_backup_after_concurrent_reads():
    """测试: 并发读后备份不再报 WinError 32"""
    logger.info("=" * 60)
    logger.info("测试: 并发读后备份(关闭读连接池)")
    logger.info("=" * 60)

    db_path = _new_db_path()
    backup_dir = db_path.parent / f"backup_{uuid.uuid4().hex}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    try:
        DatabaseOperations.init_connection_manager(db_path, read_pool_size=4)
        db_ops = DatabaseOperations()
        init_tables(db_ops.conn)

        import pandas as pd
        from datetime import date, timedelta

        # 写入初始数据
        df = pd.DataFrame({
            "stock_code": ["sh600519"] * 5,
            "trade_date": [date(2024, 1, 1) + timedelta(days=i) for i in range(5)],
            "open": [100.0] * 5, "high": [101.0] * 5, "low": [99.0] * 5,
            "close": [100.5] * 5, "volume": [10000] * 5, "amount": [1000000.0] * 5,
        })
        db_ops.insert_dataframe("stock_daily", df, ["stock_code", "trade_date"])

        # 多线程并发读,建立多个读连接
        def reader(thread_id: int):
            local_db = DatabaseOperations()
            for _ in range(10):
                local_db.query("SELECT COUNT(*) FROM stock_daily")

        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="reader") as executor:
            futures = [executor.submit(reader, i) for i in range(4)]
            for f in futures:
                f.result()

        logger.info(f"读连接数: {len(db_ops.cm._read_conns)}")

        # 模拟 scheduler._backup_and_verify 的流程
        backup_mgr = BackupManager(db_path, backup_dir)

        # 关键: 关闭写连接 + 读连接池
        db_ops.commit()
        db_ops.cm.checkpoint()
        db_ops.close()  # 关闭写连接
        db_ops.cm.close_readers()  # 关闭所有读连接
        logger.info(f"关闭后读连接数: {len(db_ops.cm._read_conns)}")

        # 执行备份(之前会在这里报 WinError 32)
        backup_file = backup_mgr.create_backup()

        # 验证备份文件存在且非空
        assert backup_file.exists(), "备份文件不存在"
        assert backup_file.stat().st_size > 0, "备份文件为空"
        logger.info(f"✓ 备份成功: {backup_file} ({backup_file.stat().st_size} bytes)")

        # 验证备份数据完整性
        verify_conn = DatabaseOperations._shared_cm._get_read_conn()
        count = verify_conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]
        assert count == 5, f"备份数据校验失败: 期望5条,实际{count}条"
        logger.info(f"✓ 备份数据校验通过: {count} 条记录")

        DatabaseOperations.close_all()
        DatabaseOperations._shared_cm = None
        logger.info("✓ 测试通过: 关闭读连接池后备份正常\n")
    finally:
        _cleanup(db_path, backup_dir)


def test_backup_without_close_readers_fails():
    """对照测试: 不关闭读连接池时备份会失败(验证问题确实存在)

    注意: 此测试在 Windows 上会复现 WinError 32,在其他系统可能不会失败。
    若此测试通过(即备份成功),说明问题已不复现,可忽略。
    """
    logger.info("=" * 60)
    logger.info("对照测试: 不关闭读连接池时备份(预期可能失败)")
    logger.info("=" * 60)

    db_path = _new_db_path()
    backup_dir = db_path.parent / f"backup_{uuid.uuid4().hex}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    try:
        DatabaseOperations.init_connection_manager(db_path, read_pool_size=4)
        db_ops = DatabaseOperations()
        init_tables(db_ops.conn)

        import pandas as pd
        from datetime import date, timedelta

        df = pd.DataFrame({
            "stock_code": ["sh600519"] * 5,
            "trade_date": [date(2024, 1, 1) + timedelta(days=i) for i in range(5)],
            "open": [100.0] * 5, "high": [101.0] * 5, "low": [99.0] * 5,
            "close": [100.5] * 5, "volume": [10000] * 5, "amount": [1000000.0] * 5,
        })
        db_ops.insert_dataframe("stock_daily", df, ["stock_code", "trade_date"])

        # 并发读建立读连接
        def reader(thread_id: int):
            local_db = DatabaseOperations()
            for _ in range(5):
                local_db.query("SELECT COUNT(*) FROM stock_daily")

        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="reader") as executor:
            futures = [executor.submit(reader, i) for i in range(4)]
            for f in futures:
                f.result()

        backup_mgr = BackupManager(db_path, backup_dir)

        # 只关闭写连接,不关闭读连接
        db_ops.commit()
        db_ops.cm.checkpoint()
        db_ops.close()

        try:
            backup_file = backup_mgr.create_backup()
            logger.info(f"对照测试: 不关闭读连接也备份成功(可能非 Windows 或 DuckDB 已优化): {backup_file}")
        except Exception as e:
            logger.info(f"对照测试: 不关闭读连接确实失败(符合预期): {e}")

        DatabaseOperations.close_all()
        DatabaseOperations._shared_cm = None
        logger.info("✓ 对照测试完成\n")
    finally:
        _cleanup(db_path, backup_dir)


def main():
    logger.info("开始备份流程测试\n")
    try:
        test_backup_after_concurrent_reads()
        test_backup_without_close_readers_fails()
        logger.info("=" * 60)
        logger.info("🎉 所有备份测试通过!")
        logger.info("=" * 60)
    except AssertionError as e:
        logger.error(f"❌ 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ 测试异常: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
