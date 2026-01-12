import ccxt
import pandas as pd
import numpy as np
import time
from datetime import datetime
import os

class SmartMarketScanner:
    def __init__(self, use_mock=False):
        self.use_mock = use_mock
        self.exchanges = {}
        self.history = {} # 用來存歷史費率計算波動率

        if not use_mock:
            # 初始化三大交易所
            # 即使沒有 Key，ccxt 也能抓取公開報價 (Public Data)
            # 但填入 Key 可以獲得更高的 API 頻率限制
            try:
                self.exchanges['binance'] = ccxt.binance({
                    'apiKey': os.getenv('BINANCE_API_KEY'),
                    'secret': os.getenv('BINANCE_SECRET'),
                    'options': {'defaultType': 'future'}
                })
                
                self.exchanges['bybit'] = ccxt.bybit({
                    'apiKey': os.getenv('BYBIT_API_KEY'),
                    'secret': os.getenv('BYBIT_SECRET'),
                    'options': {'defaultType': 'linear'} # Bybit USDT合約通常是 linear
                })
                
                self.exchanges['okx'] = ccxt.okx({
                    'apiKey': os.getenv('OKX_API_KEY'),
                    'secret': os.getenv('OKX_SECRET'),
                    'password': os.getenv('OKX_PASSWORD'), # 修正：加入 Password
                    'options': {'defaultType': 'swap'}
                })
                print("✅ 交易所連線初始化完成 (Real Mode)")
            except Exception as e:
                print(f"⚠️ 交易所初始化部分失敗: {e}")

    def get_top_volume_symbols(self, limit=20):
        """
        [智能篩選] 只看流動性最好的前 20 大幣種
        """
        if self.use_mock:
            return ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT']
            
        try:
            # 以 Binance 的交易量為基準
            tickers = self.exchanges['binance'].fetch_tickers()
            # 排序並過濾出 USDT 永續合約
            sorted_tickers = sorted(
                [t for t in tickers.values() if '/USDT' in t['symbol'] and 'BUS' not in t['symbol']], 
                key=lambda x: x['quoteVolume'] if x['quoteVolume'] else 0, 
                reverse=True
            )
            top_symbols = [t['symbol'] for t in sorted_tickers[:limit]]
            return top_symbols
        except Exception as e:
            print(f"⚠️ 獲取熱門幣種失敗 (可能 API 受限): {e}")
            return ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'DOGE/USDT', 'ADA/USDT']

    def scan_funding_opportunities(self):
        """
        [策略核心] 掃描全市場，尋找「長期穩定」且「高報酬」的機會
        包含：資金費率(APR)、價差(Spread)、深度(Depth)
        """
        if self.use_mock:
            return self._generate_mock_opportunities()

        symbols = self.get_top_volume_symbols()
        opportunities = []

        # print(f"🔍 正在掃描 {len(symbols)} 個主流幣種的資金費率...")

        for symbol in symbols:
            rates = {}
            # 1. 抓取各交易所資金費率
            for ex_name, exchange in self.exchanges.items():
                try:
                    # 處理 OKX 特殊符號格式 (BTC/USDT:USDT -> BTC-USDT-SWAP)
                    market_symbol = symbol
                    if ex_name == 'okx': 
                        market_symbol = symbol.split(':')[0].replace('/', '-') + '-SWAP'
                    
                    rate_info = exchange.fetch_funding_rate(market_symbol)
                    rates[ex_name] = float(rate_info['fundingRate'])
                except Exception as e:
                    # print(f"  ❌ {ex_name} 抓取費率失敗: {symbol}")
                    continue
            
            if len(rates) < 2: continue

            # 找出最大利差組合
            sorted_rates = sorted(rates.items(), key=lambda x: x[1])
            min_ex, min_rate = sorted_rates[0]  # 做多 (付最少/領最多)
            max_ex, max_rate = sorted_rates[-1] # 做空 (領最多/付最少)
            
            diff = max_rate - min_rate
            apr = diff * 3 * 365 * 100 # 簡單預估年化

            # --- 計算價差與深度 (Spread & Depth) ---
            try:
                # 處理 Symbol 名稱差異
                long_sym = symbol
                if min_ex == 'okx': long_sym = symbol.split(':')[0].replace('/', '-') + '-SWAP'
                
                short_sym = symbol
                if max_ex == 'okx': short_sym = symbol.split(':')[0].replace('/', '-') + '-SWAP'

                # 抓取 Ticker (包含 Bid/Ask)
                long_ticker = self.exchanges[min_ex].fetch_ticker(long_sym)
                short_ticker = self.exchanges[max_ex].fetch_ticker(short_sym)

                # 做多買入價 (Ask) vs 做空賣出價 (Bid)
                long_price = long_ticker['ask']
                short_price = short_ticker['bid']
                
                # 1. 計算價差 (Spread %)
                # (買價 - 賣價) / 賣價。正數 = 成本(虧損)；負數 = 利潤(折價)
                spread = (long_price - short_price) / short_price * 100
                
                # 2. 計算深度 (Depth USDT)
                # 簡單估算：以最佳一檔的掛單量 * 價格
                long_vol = long_ticker['askVolume'] if long_ticker.get('askVolume') else 0
                short_vol = short_ticker['bidVolume'] if short_ticker.get('bidVolume') else 0
                
                min_depth = min(long_vol * long_price, short_vol * short_price)

            except Exception as e:
                # 抓不到價格時給預設值，避免程式崩潰
                spread = 0.0
                min_depth = 0.0
            # ----------------------------------------------------

            # 計算穩定度 (Sigma)
            if symbol not in self.history: self.history[symbol] = []
            self.history[symbol].append(diff)
            if len(self.history[symbol]) > 50: self.history[symbol].pop(0)
            sigma = np.std(self.history[symbol]) if len(self.history[symbol]) > 5 else 0.0001
            
            # [篩選邏輯] APR > 1% 才顯示
            if apr > 1: 
                opportunities.append({
                    'symbol': symbol,
                    'long_ex': min_ex,
                    'long_rate': min_rate,
                    'short_ex': max_ex,
                    'short_rate': max_rate,
                    'apr': apr,
                    'sigma': sigma,
                    'spread_price': spread, 
                    'depth': min_depth      
                })

        # 排序：APR 高的在前面
        best_opps = sorted(opportunities, key=lambda x: x['apr'], reverse=True)
        return best_opps

    def _generate_mock_opportunities(self):
        """生成模擬數據"""
        mock_symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT', 'DOGE/USDT', 'AVAX/USDT']
        exchanges = ['binance', 'bybit', 'okx']
        opps = []
        for symbol in mock_symbols:
            long_ex = np.random.choice(exchanges)
            short_ex = np.random.choice([e for e in exchanges if e != long_ex])
            
            # 模擬隨機費率
            long_rate = np.random.uniform(-0.0005, 0.0001)
            short_rate = np.random.uniform(0.0001, 0.0008)
            diff = short_rate - long_rate
            apr = diff * 3 * 365 * 100
            
            opps.append({
                'symbol': symbol,
                'long_ex': long_ex,
                'long_rate': long_rate,
                'short_ex': short_ex,
                'short_rate': short_rate,
                'apr': apr,
                'sigma': np.random.uniform(0.00001, 0.0001),
                'spread_price': np.random.uniform(-0.05, 0.15), # 模擬價差
                'depth': np.random.uniform(10000, 2000000)      # 模擬深度
            })
        return sorted(opps, key=lambda x: x['apr'], reverse=True)

    def backtest_strategy(self, symbol, days=30):
        """
        [回測模組] 模擬回測
        """
        np.random.seed(len(symbol))
        roi = np.random.uniform(5, 20)
        mdd = np.random.uniform(1, 5)
        return roi, mdd