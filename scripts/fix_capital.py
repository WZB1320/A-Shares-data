import akshare as ak
import duckdb
import logging
import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

conn = duckdb.connect('stock_data.duckdb')

missing = ['sh600519', 'sh600309', 'sz000725']

for stock_code in missing:
    code = stock_code[2:]
    logger.info(f"Retrying total shares for {stock_code}")
    
    for attempt in range(5):
        try:
            time.sleep(2)
            df = ak.stock_individual_info_em(symbol=code)
            
            total_shares_row = df[df['item'] == '总股本']
            if not total_shares_row.empty:
                val = total_shares_row.iloc[0]['value']
                total_shares = int(float(val))
                
                conn.execute("""
                    INSERT INTO stock_capital (stock_code, record_date, total_shares)
                    VALUES (?, CURRENT_DATE, ?)
                    ON CONFLICT (stock_code, record_date) DO UPDATE SET total_shares = EXCLUDED.total_shares
                """, (stock_code, total_shares))
                logger.info(f"Saved {stock_code}: {total_shares}")
                break
            else:
                logger.warning(f"No total shares row for {stock_code}")
                break
        except Exception as e:
            logger.warning(f"Attempt {attempt+1}/5 failed for {stock_code}: {e}")
            time.sleep(3)

conn.close()
logger.info("Done")