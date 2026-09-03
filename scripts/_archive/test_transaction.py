"""事务回滚验证测试

验证 DatabaseOperations.transaction() 的事务原子性:
  1. 事务内全部成功 → 数据应提交
  2. 事务内中途异常 → 数据应全部回滚
  3. 嵌套事务 → 内层异常应回滚外层
  4. CHECKPOINT → WAL 数据应刷入主文件

运行: python -m scripts.test_transaction
"""
import sys
import logging
import tempfile
from pathlib import Path
from datetime import date

# 保证从项目根目录导入
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.operations import DatabaseOperations
from src.database.models import init_tables

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def setup_test_db(db_path: Path):
    """初始化测试数据库,创建必要的表

    新架构: 先初始化 ConnectionManager,再创建 DatabaseOperations
    """
    DatabaseOperations.init_connection_manager(db_path)
    db_ops = DatabaseOperations()
    init_tables(db_ops.conn)
    return db_ops


def _new_db_path():
    """生成临时 DuckDB 文件路径(不创建文件,让 DuckDB 自己创建)"""
    import tempfile
    import uuid
    # 用 uuid 避免冲突,文件由 DuckDB 创建
    tmp_dir = Path(tempfile.gettempdir())
    return tmp_dir / f"test_tx_{uuid.uuid4().hex}.duckdb"


def _cleanup_db(db_path: Path):
    """清理测试数据库(关闭所有连接 + 删除文件)"""
    DatabaseOperations.close_all()
    # 重置共享 ConnectionManager,允许下一次测试重新初始化
    DatabaseOperations._shared_cm = None
    db_path.unlink(missing_ok=True)


def test_1_commit_on_success():
    """测试1: 事务内全部成功,数据应提交"""
    logger.info("=" * 60)
    logger.info("测试1: 事务成功提交")
    logger.info("=" * 60)

    with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as f:
        db_path = Path(f.name)
    f.close()  # 关闭文件句柄,让DuckDB能创建
    db_path.unlink(missing_ok=True)  # 删除空文件,让DuckDB自己创建
    try:
        db_ops = setup_test_db(db_path)
        import pandas as pd

        df = pd.DataFrame({
            "stock_code": ["sh600519"],
            "trade_date": [date(2024, 1, 1)],
            "open": [100.0], "high": [101.0], "low": [99.0],
            "close": [100.5], "volume": [10000], "amount": [1000000.0],
        })

        with db_ops.transaction():
            db_ops.insert_dataframe("stock_daily", df, ["stock_code", "trade_date"])
            db_ops.update_last_update_date("sh600519", "daily", "2024-01-01")

        # 验证: 事务外应能查到数据
        result = db_ops.query("SELECT COUNT(*) as cnt FROM stock_daily WHERE stock_code = 'sh600519'")
        cnt = result.iloc[0]["cnt"]
        assert cnt == 1, f"期望1条,实际{cnt}条"

        log_result = db_ops.query("SELECT last_update_date FROM update_log WHERE stock_code = 'sh600519'")
        assert len(log_result) == 1, "update_log 应有1条记录"

        db_ops.close()
        logger.info("✓ 测试1通过: 事务成功提交,数据已落盘\n")
    finally:
        _cleanup_db(db_path)


def test_2_rollback_on_failure():
    """测试2: 事务内中途异常,数据应全部回滚"""
    logger.info("=" * 60)
    logger.info("测试2: 事务失败回滚")
    logger.info("=" * 60)

    with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as f:
        db_path = Path(f.name)
    f.close()  # 关闭文件句柄,让DuckDB能创建
    db_path.unlink(missing_ok=True)  # 删除空文件,让DuckDB自己创建
    try:
        db_ops = setup_test_db(db_path)
        import pandas as pd

        df = pd.DataFrame({
            "stock_code": ["sh600519"],
            "trade_date": [date(2024, 1, 1)],
            "open": [100.0], "high": [101.0], "low": [99.0],
            "close": [100.5], "volume": [10000], "amount": [1000000.0],
        })

        # 模拟: 写入成功后,更新水位前抛异常
        try:
            with db_ops.transaction():
                db_ops.insert_dataframe("stock_daily", df, ["stock_code", "trade_date"])
                raise RuntimeError("模拟中途失败")
        except RuntimeError as e:
            logger.info(f"  捕获预期异常: {e}")

        # 验证: 事务回滚,stock_daily 应无数据,update_log 也应无数据
        result = db_ops.query("SELECT COUNT(*) as cnt FROM stock_daily WHERE stock_code = 'sh600519'")
        cnt = result.iloc[0]["cnt"]
        assert cnt == 0, f"回滚后应0条,实际{cnt}条"

        log_result = db_ops.query("SELECT COUNT(*) as cnt FROM update_log WHERE stock_code = 'sh600519'")
        log_cnt = log_result.iloc[0]["cnt"]
        assert log_cnt == 0, f"update_log 应0条,实际{log_cnt}条"

        db_ops.close()
        logger.info("✓ 测试2通过: 事务失败回滚,数据未落盘\n")
    finally:
        _cleanup_db(db_path)


def test_3_nested_transaction():
    """测试3: 嵌套事务,内层异常应回滚外层"""
    logger.info("=" * 60)
    logger.info("测试3: 嵌套事务回滚")
    logger.info("=" * 60)

    with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as f:
        db_path = Path(f.name)
    f.close()  # 关闭文件句柄,让DuckDB能创建
    db_path.unlink(missing_ok=True)  # 删除空文件,让DuckDB自己创建
    try:
        db_ops = setup_test_db(db_path)
        import pandas as pd

        df = pd.DataFrame({
            "stock_code": ["sh600519"],
            "trade_date": [date(2024, 1, 1)],
            "open": [100.0], "high": [101.0], "low": [99.0],
            "close": [100.5], "volume": [10000], "amount": [1000000.0],
        })

        # 外层事务 + 内层事务(嵌套)
        try:
            with db_ops.transaction():
                db_ops.insert_dataframe("stock_daily", df, ["stock_code", "trade_date"])
                # 内层事务(应为 no-op,不重复 BEGIN)
                with db_ops.transaction():
                    db_ops.update_last_update_date("sh600519", "daily", "2024-01-01")
                    raise RuntimeError("内层异常")
        except RuntimeError as e:
            logger.info(f"  捕获预期异常: {e}")

        # 验证: 整体回滚
        result = db_ops.query("SELECT COUNT(*) as cnt FROM stock_daily WHERE stock_code = 'sh600519'")
        cnt = result.iloc[0]["cnt"]
        assert cnt == 0, f"嵌套回滚后应0条,实际{cnt}条"

        db_ops.close()
        logger.info("✓ 测试3通过: 嵌套事务回滚正确\n")
    finally:
        _cleanup_db(db_path)


def test_4_checkpoint():
    """测试4: CHECKPOINT 后数据应刷入主文件"""
    logger.info("=" * 60)
    logger.info("测试4: CHECKPOINT 刷盘")
    logger.info("=" * 60)

    with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as f:
        db_path = Path(f.name)
    f.close()  # 关闭文件句柄,让DuckDB能创建
    db_path.unlink(missing_ok=True)  # 删除空文件,让DuckDB自己创建
    try:
        db_ops = setup_test_db(db_path)
        import pandas as pd

        df = pd.DataFrame({
            "stock_code": ["sh600519"],
            "trade_date": [date(2024, 1, 1)],
            "open": [100.0], "high": [101.0], "low": [99.0],
            "close": [100.5], "volume": [10000], "amount": [1000000.0],
        })

        with db_ops.transaction():
            db_ops.insert_dataframe("stock_daily", df, ["stock_code", "trade_date"])

        # CHECKPOINT
        db_ops.checkpoint()

        # 关闭连接后重新打开,验证数据持久化
        db_ops.close()
        # 重置 ConnectionManager 以便重新初始化
        DatabaseOperations._shared_cm = None
        DatabaseOperations.init_connection_manager(db_path)
        db_ops2 = DatabaseOperations()
        result = db_ops2.query("SELECT COUNT(*) as cnt FROM stock_daily WHERE stock_code = 'sh600519'")
        cnt = result.iloc[0]["cnt"]
        assert cnt == 1, f"CHECKPOINT后应1条,实际{cnt}条"

        db_ops2.close()
        logger.info("✓ 测试4通过: CHECKPOINT 后数据已刷入主文件\n")
    finally:
        _cleanup_db(db_path)


def test_5_delete_insert_atomicity():
    """测试5: DELETE + INSERT 原子性(模拟指标计算场景)"""
    logger.info("=" * 60)
    logger.info("测试5: DELETE+INSERT 原子性")
    logger.info("=" * 60)

    with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as f:
        db_path = Path(f.name)
    f.close()  # 关闭文件句柄,让DuckDB能创建
    db_path.unlink(missing_ok=True)  # 删除空文件,让DuckDB自己创建
    try:
        db_ops = setup_test_db(db_path)
        import pandas as pd

        # 先插入2条技术指标
        df_init = pd.DataFrame({
            "stock_code": ["sh600519", "sh600519"],
            "trade_date": [date(2024, 1, 1), date(2024, 1, 2)],
            "ma5": [100.0, 101.0],
        })
        db_ops.insert_dataframe("technical_indicators", df_init, ["stock_code", "trade_date"])
        db_ops.commit()

        cnt_before = db_ops.query("SELECT COUNT(*) as cnt FROM technical_indicators").iloc[0]["cnt"]
        assert cnt_before == 2, f"初始应有2条,实际{cnt_before}"

        # 模拟: DELETE 成功,INSERT 失败 → 应整体回滚,数据不丢
        df_new = pd.DataFrame({
            "stock_code": ["sh600519"],
            "trade_date": [date(2024, 1, 3)],
            "ma5": [102.0],
        })
        try:
            with db_ops.transaction():
                db_ops.conn.execute("DELETE FROM technical_indicators WHERE stock_code = 'sh600519'")
                db_ops.insert_dataframe("technical_indicators", df_new, ["stock_code", "trade_date"])
                raise RuntimeError("模拟INSERT后失败")
        except RuntimeError as e:
            logger.info(f"  捕获预期异常: {e}")

        # 验证: 回滚后原2条数据应仍在
        cnt_after = db_ops.query("SELECT COUNT(*) as cnt FROM technical_indicators").iloc[0]["cnt"]
        assert cnt_after == 2, f"回滚后应仍2条,实际{cnt_after}条 (DELETE未回滚导致数据丢失!)"

        db_ops.close()
        logger.info("✓ 测试5通过: DELETE+INSERT 原子性保证,数据未丢失\n")
    finally:
        _cleanup_db(db_path)


def main():
    logger.info("开始事务回滚验证测试\n")
    try:
        test_1_commit_on_success()
        test_2_rollback_on_failure()
        test_3_nested_transaction()
        test_4_checkpoint()
        test_5_delete_insert_atomicity()
        logger.info("=" * 60)
        logger.info("🎉 所有测试通过!")
        logger.info("=" * 60)
    except AssertionError as e:
        logger.error(f"❌ 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ 测试异常: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
