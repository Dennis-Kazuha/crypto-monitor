import ccxt
import os
from dotenv import load_dotenv

load_dotenv()

def check():
    print("🔍 正在連線交易所查詢 ENSO/USDT 的合約規格 (Windows Local Test)...")
    
    # 初始化不帶 Key (公開查詢)
    exchanges = {
        'binance': ccxt.binance({'options': {'defaultType': 'future'}}),
        'bybit': ccxt.bybit({'options': {'defaultType': 'linear'}}),
        'okx': ccxt.okx({'options': {'defaultType': 'swap'}}),
    }

    # 測試幣種 (你可以改成其他顯示 8h 但其實不是的幣)
    symbol = 'ENSO/USDT'
    
    for name, ex in exchanges.items():
        print(f"\n--- {name.upper()} ---")
        try:
            ex.load_markets()
            
            # 處理 OKX 特殊名稱
            query_symbol = symbol
            if name == 'okx': query_symbol = 'ENSO-USDT-SWAP'

            # 1. 查 Market Metadata (合約規格書)
            # 這是最準的，通常藏在這裡
            try:
                market = ex.market(query_symbol)
                print(f"✅ Market Found: {market['id']}")
                
                # 檢查各種可能的欄位
                print(f"   fundingInterval (raw ms): {market.get('fundingInterval')}")
                if 'info' in market:
                    print(f"   info.fundingIntervalHours: {market['info'].get('fundingIntervalHours')}")
            except Exception as e:
                print(f"❌ Market Not Found: {query_symbol} ({e})")

            # 2. 查即時 Rate (即時報價)
            # 這裡通常只給費率，不給週期
            try:
                rate = ex.fetch_funding_rate(query_symbol)
                print(f"✅ Rate Info: {rate['fundingRate']}")
                print(f"   rate.fundingInterval: {rate.get('fundingInterval')}")
            except Exception as e:
                print(f"❌ Rate Fetch Failed: {e}")

        except Exception as e:
            print(f"❌ Init Failed: {e}")

if __name__ == "__main__":
    check()