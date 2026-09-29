
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent

DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
SCRIPTS_DIR = BASE_DIR / "scripts"
BACKUP_DIR = DATA_DIR / "backups"

DB_PATH = DATA_DIR / "stock_data.duckdb"
LOG_PATH = LOG_DIR / "stock_collector.log"

# 采集清单(权威来源)
#
# 维护约定: 本清单必须与 scripts/init_master.py 的 STOCKS 主数据表保持一一对应。
# 历史上两份清单曾双向脱钩 —— 主数据登记 21 只但此处只采集 13 只, 且有 1 只
# (sh600552) 有日线数据却未登记主数据, 导致调用方查主数据里存在的股票时
# 各接口返回 count=0 / 字段空值。2026-09-29 已对齐为 22 只。
STOCK_CODES = [
    # -- 原有 13 只(历史采集范围) --
    "sh600519",  # 贵州茅台
    "sz002843",  # 华懋新材
    "sz002272",  # 川润股份
    "sh600346",  # 恒力石化
    "sh600309",  # 万华化学
    "sh600887",  # 伊利股份
    "sh600276",  # 恒瑞医药
    "sz002714",  # 牧原股份
    "sz000725",  # 京东方A
    "sh600089",  # 特变电工
    "sz002648",  # 卫星化学
    "sh513700",  # 香港医药ETF
    "sh600552",  # 凯盛科技(有日线但曾漏登记主数据)
    # -- 2026-09-29 补齐: 主数据已登记但从未采集的 9 只 --
    "sh600585",  # 海螺水泥
    "sh600900",  # 长江电力
    "sh601318",  # 中国平安
    "sh601857",  # 中国石油
    "sz000651",  # 格力电器
    "sz000858",  # 五粮液
    "sz002415",  # 海康威视
    "sz002594",  # 比亚迪
    "sz300750",  # 宁德时代
]

START_DATE = "20160510"
LOG_LEVEL = "INFO"

BACKUP_RETENTION_DAYS = 30

for d in [DATA_DIR, LOG_DIR, SCRIPTS_DIR, BACKUP_DIR]:
    d.mkdir(parents=True, exist_ok=True)
