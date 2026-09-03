"""BaoStock 数据采集器 - 获取分红、行业分类、股本数据

优势: 免费无需注册、数据质量高、稳定维护中
数据: 分红送股、行业分类(申万)、股本

注意: BaoStock的财务数据(盈利能力/资产负债/现金流)主要是比率指标,
不适合直接存入financial_statements表, 财务数据仍由AKShare Sina接口提供

线程安全:
  - BaoStock 的 bs 模块是全局单例(login/logout 影响整个进程),必须串行化
  - 通过 _bs_global_lock 全局锁保证同一时刻只有一个线程能调用 bs API
  - 每次查询前重新 login,避免状态丢失
"""
import baostock as bs
import pandas as pd
import logging
import threading
from datetime import datetime
from .base import BaseCollector, retry

logger = logging.getLogger(__name__)

# 全局锁: BaoStock 的 bs 模块是进程级单例,必须串行化
_bs_global_lock = threading.Lock()


class BaostockCollector(BaseCollector):
    def __init__(self, db_ops, start_date: str):
        super().__init__(db_ops, start_date)
        self._logged_in = False

    def _ensure_login(self):
        """确保BaoStock登录状态(加全局锁,串行化)

        BaoStock 的 bs 模块是全局单例,多线程并发调用会导致状态混乱。
        每次查询前重新登录以避免状态丢失。
        """
        with _bs_global_lock:
            try:
                # 先尝试登出再登录, 避免状态冲突
                bs.logout()
            except Exception:
                pass
            lg = bs.login()
            if lg.error_code != '0':
                raise ConnectionError(f"BaoStock登录失败: {lg.error_msg}")
            self._logged_in = True

    def close(self):
        # BaoStock 是全局单例,不在实例层面关闭
        # 由 scheduler 在所有采集完成后统一 logout
        pass

    @classmethod
    def global_logout(cls):
        """全局登出(由 scheduler 在所有采集完成后调用)"""
        try:
            bs.logout()
        except Exception:
            pass

    def collect_stock(self, stock_code: str):
        self._ensure_login()
        steps = [
            ("股本数据(baostock)", self.collect_capital_data),
            ("行业数据(baostock)", self.collect_industry_data),
            ("分红数据(baostock)", self.collect_dividend_data),
        ]
        for name, method in steps:
            try:
                method(stock_code)
            except Exception as e:
                logger.error(f"{stock_code} {name}采集失败: {e}")

    def collect_capital_data(self, stock_code: str):
        """通过BaoStock采集股本数据(盈利能力接口中包含totalShare)"""
        self._ensure_login()
        logger.info(f"[baostock] 开始采集 {stock_code} 股本数据")

        bs_code = self._to_bs_code(stock_code)

        # BaoStock bs 模块全局单例,所有 bs API 调用必须在锁内
        total_shares = None
        with _bs_global_lock:
            now = datetime.now()
            for year_offset in range(0, 3):
                year = now.year - year_offset
                for quarter in [4, 3, 2, 1]:
                    if year == now.year and quarter > self._current_quarter():
                        continue

                    rs = bs.query_profit_data(code=bs_code, year=year, quarter=quarter)
                    data = []
                    while rs.error_code == '0' and rs.next():
                        data.append(rs.get_row_data())

                    if not data:
                        continue

                    df = pd.DataFrame(data, columns=rs.fields)
                    row = df.iloc[0]
                    total_shares = self._safe_float(row, 'totalShare')
                    if total_shares:
                        total_shares = int(total_shares)
                        break
                if total_shares:
                    break

        if total_shares:
            today = datetime.now().strftime("%Y-%m-%d")
            # 事务保证单条写入原子化
            with self.db_ops.transaction():
                self.db_ops.conn.execute("""
                INSERT INTO stock_capital (stock_code, record_date, total_shares)
                VALUES (?, ?, ?)
                ON CONFLICT (stock_code, record_date) DO UPDATE SET total_shares = EXCLUDED.total_shares
                """, (stock_code, today, total_shares))
            logger.info(f"[baostock] {stock_code} 总股本完成: {total_shares}")
        else:
            logger.warning(f"[baostock] {stock_code} 未获取到股本数据")

    def collect_industry_data(self, stock_code: str):
        """通过BaoStock采集行业分类(证监会行业分类)"""
        self._ensure_login()
        logger.info(f"[baostock] 开始采集 {stock_code} 行业信息")

        bs_code = self._to_bs_code(stock_code)

        # query_stock_industry在0.9.x可能有user_id bug
        try:
            industry_name = None
            industry_classification = None

            # BaoStock bs 模块全局单例,所有 bs API 调用必须在锁内
            with _bs_global_lock:
                rs = bs.query_stock_industry()
                if rs.error_code != '0':
                    logger.warning(f"[baostock] 行业查询失败: {rs.error_msg}")
                    return

                data = []
                while rs.error_code == '0' and rs.next():
                    data.append(rs.get_row_data())

                if not data:
                    logger.warning(f"[baostock] 未获取到行业数据")
                    return

                df = pd.DataFrame(data, columns=rs.fields)
                matched = df[df['code'] == bs_code]

                if matched.empty:
                    logger.warning(f"[baostock] {stock_code} 未匹配到行业信息")
                    return

                row = matched.iloc[0]
                industry_name = row.get('industry', '')
                industry_classification = row.get('industryClassification', '')

            if not industry_name:
                return

            today = datetime.now().strftime("%Y-%m-%d")
            # 事务保证写入原子化
            with self.db_ops.transaction():
                self.db_ops.conn.execute("""
                INSERT INTO stock_industry (stock_code, industry_name, industry_level, source, update_date)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (stock_code) DO UPDATE SET
                    industry_name = EXCLUDED.industry_name,
                    industry_level = EXCLUDED.industry_level,
                    source = EXCLUDED.source,
                    update_date = EXCLUDED.update_date
                """, (stock_code, industry_name, industry_classification, "BaoStock", today))
            logger.info(f"[baostock] {stock_code} 行业信息完成: {industry_name}")

        except AttributeError as e:
            # BaoStock 0.9.x query_stock_industry的user_id bug
            logger.warning(f"[baostock] query_stock_industry bug, 回退到AKShare: {e}")
            self._fallback_industry_akshare(stock_code)

    def _fallback_industry_akshare(self, stock_code: str):
        """AKShare回退: 通过巨潮获取行业信息"""
        try:
            import akshare as ak
            code = stock_code[2:]
            df = ak.stock_profile_cninfo(symbol=code)
            industry_name = df['所属行业'].iloc[0]
            if industry_name:
                today = datetime.now().strftime("%Y-%m-%d")
                # 事务保证写入原子化
                with self.db_ops.transaction():
                    self.db_ops.conn.execute("""
                    INSERT INTO stock_industry (stock_code, industry_name, industry_level, source, update_date)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (stock_code) DO UPDATE SET
                        industry_name = EXCLUDED.industry_name,
                        update_date = EXCLUDED.update_date
                    """, (stock_code, industry_name, "巨潮行业", "CNInfo", today))
                logger.info(f"[akshare回退] {stock_code} 行业信息完成: {industry_name}")
        except Exception as e:
            logger.warning(f"[akshare回退] {stock_code} 行业信息采集失败: {e}")

    def collect_dividend_data(self, stock_code: str):
        """通过BaoStock采集分红数据"""
        self._ensure_login()
        logger.info(f"[baostock] 开始采集 {stock_code} 分红数据")

        bs_code = self._to_bs_code(stock_code)

        # BaoStock bs 模块全局单例,所有 bs API 调用必须在锁内
        all_data = []
        rs_fields = None
        with _bs_global_lock:
            for y in range(datetime.now().year, datetime.now().year - 6, -1):
                rs = bs.query_dividend_data(code=bs_code, year=str(y), yearType='report')
                data = []
                while rs.error_code == '0' and rs.next():
                    data.append(rs.get_row_data())
                if data:
                    all_data.extend(data)
                    rs_fields = rs.fields

        if not all_data:
            logger.info(f"[baostock] {stock_code} 没有分红数据")
            return

        # 获取字段名(从最后一次成功的rs)
        df = pd.DataFrame(all_data, columns=rs_fields)
        dividend_data = []

        for _, row in df.iterrows():
            # dividCashPsBeforeTax: 税前每股派息(元)
            cash_before_tax = self._safe_float(row, 'dividCashPsBeforeTax')
            if cash_before_tax is None or cash_before_tax == 0:
                continue

            # 除权除息日: dividOperateDate
            ex_date = row.get('dividOperateDate', '')
            if not ex_date or ex_date.strip() == '':
                continue

            try:
                dividend_date = pd.to_datetime(ex_date).date()
            except Exception:
                continue

            # 公告日期: dividPlanAnnounceDate
            announcement_date = row.get('dividPlanAnnounceDate', '')
            if announcement_date and announcement_date.strip() != '':
                try:
                    announcement_date = pd.to_datetime(announcement_date).date()
                except Exception:
                    announcement_date = None
            else:
                announcement_date = None

            # BaoStock的dividCashPsBeforeTax已经是每股派息金额(元), 不是每10股
            cash_per_share = round(cash_before_tax, 4)

            dividend_data.append({
                'stock_code': stock_code,
                'dividend_date': dividend_date,
                'cash_per_share': cash_per_share,
                'announcement_date': announcement_date
            })

        if not dividend_data:
            return

        df_div = pd.DataFrame(dividend_data)
        # 去重
        df_div = df_div.drop_duplicates(subset=['stock_code', 'dividend_date'], keep='last')
        # 事务保证批量写入原子化
        with self.db_ops.transaction():
            self.db_ops.insert_dataframe("dividends", df_div, ["stock_code", "dividend_date"])
        logger.info(f"[baostock] {stock_code} 分红完成，新增 {len(df_div)} 条")

    # ---- 工具方法 ----

    @staticmethod
    def _to_bs_code(stock_code: str) -> str:
        """sh600519 -> sh.600519"""
        return f"{stock_code[:2]}.{stock_code[2:]}"

    @staticmethod
    def _from_bs_code(bs_code: str) -> str:
        """sh.600519 -> sh600519"""
        return bs_code.replace('.', '', 1)

    @staticmethod
    def _current_quarter() -> int:
        now = datetime.now()
        return (now.month - 1) // 3 + 1

    @staticmethod
    def _safe_float(row, col_name):
        val = row.get(col_name) if hasattr(row, 'get') else None
        if val is None or val == '' or val == 'None':
            return None
        try:
            result = float(val)
            if pd.isna(result):
                return None
            return result
        except (ValueError, TypeError):
            return None
