
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent

DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
SCRIPTS_DIR = BASE_DIR / "scripts"
BACKUP_DIR = DATA_DIR / "backups"

DB_PATH = DATA_DIR / "stock_data.duckdb"
LOG_PATH = LOG_DIR / "stock_collector.log"

STOCK_CODES = [
    "sh600519",
    "sz002843",
    "sz002272",
    "sh600346",
    "sh600309",
    "sh600887",
    "sh600276",
    "sz002714",
    "sz000725",
    "sh600089",
    "sz002648",
    "sh513700",
    "sh600552",
]

START_DATE = "20160510"
LOG_LEVEL = "INFO"

BACKUP_RETENTION_DAYS = 30

for d in [DATA_DIR, LOG_DIR, SCRIPTS_DIR, BACKUP_DIR]:
    d.mkdir(parents=True, exist_ok=True)
