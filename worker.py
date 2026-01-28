import time
from market_scanner import SmartMarketScanner
from database import init_db, save_latest_data
from datetime import datetime

def run_worker():
    print(f"[{datetime.now()}] 🚀 後台掃描服務啟動...")
    init_db()
    scanner = SmartMarketScanner(use_mock=False)
    
    while True:
        try:
            print(f"[{datetime.now()}] 🔍 開始執行全市場掃描...")
            opportunities = scanner.scan_funding_opportunities()
            
            if opportunities:
                save_latest_data(opportunities)
                print(f"[{datetime.now()}] ✅ 掃描完成並存入資料庫 (共 {len(opportunities)} 筆)。")
            else:
                # [新增這行] 告訴我掃描結果是空的
                print(f"[{datetime.now()}] ⚠️ 掃描完成，但未發現任何套利機會 (或是交易所連線不足)。")
                
        except Exception as e:
            print(f"[{datetime.now()}] ❌ 錯誤: {e}")
        
        time.sleep(60)

if __name__ == "__main__":
    run_worker()
