import ccxt
import pandas as pd
import numpy as np
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional
import threading

class SmartMarketScanner:
    """市場掃描器 - 歷史驗證增強版"""
    
    FEE_SCHEDULE = {
        'binance': {'maker': 0.0002, 'taker': 0.0005},
        'bybit': {'maker': 0.0002, 'taker': 0.00055},
        'okx': {'maker': 0.0002, 'taker': 0.0005}
    }
    
    def __init__(self, use_mock=False):
        self.use_mock = use_mock
        self.exchanges = {}
        if not use_mock:
            self._initialize_exchanges()
    
    def _initialize_exchanges(self):
        try:
            # 增加 timeout 避免連線過久
            self.exchanges['binance'] = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}, 'timeout': 30000})
            self.exchanges['bybit'] = ccxt.bybit({'enableRateLimit': True, 'options': {'defaultType': 'linear'}, 'timeout': 30000})
            self.exchanges['okx'] = ccxt.okx({'enableRateLimit': True, 'options': {'defaultType': 'swap'}, 'timeout': 30000})
            
            print("⏳ [System] 正在載入交易所合約規格...")
            # 並行載入加速
            def load(ex):
                try: ex.load_markets()
                except: pass
            
            with ThreadPoolExecutor(max_workers=3) as executor:
                executor.map(load, self.exchanges.values())
                    
        except Exception as e:
            print(f"❌ 初始化失敗: {e}")
    
    def get_top_volume_symbols(self, limit=30) -> List[str]:
        if self.use_mock: return ['BTC/USDT', 'ETH/USDT']
        try:
            tickers = self.exchanges['binance'].fetch_tickers()
            valid = [t for s, t in tickers.items() if '/USDT' in s and 'BUSD' not in s and t.get('quoteVolume', 0) > 0]
            sorted_tickers = sorted(valid, key=lambda x: x['quoteVolume'], reverse=True)
            return [t['symbol'].split(':')[0] for t in sorted_tickers[:limit]]
        except:
            return ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']

    def _fetch_orderbook_data(self, exchange_name: str, symbol: str) -> Optional[Dict]:
        try:
            exchange = self.exchanges[exchange_name]
            query_symbol = symbol
            if exchange_name == 'okx': query_symbol = f"{symbol.split('/')[0]}-USDT-SWAP"
            
            # Bybit 防呆
            if exchange_name == 'bybit' and query_symbol not in exchange.markets:
                return None

            orderbook = exchange.fetch_order_book(query_symbol, limit=20)
            return {
                'bid_price': orderbook['bids'][0][0] if orderbook['bids'] else None,
                'ask_price': orderbook['asks'][0][0] if orderbook['asks'] else None,
                'bid_quantity': sum([bid[1] for bid in orderbook['bids']]),
                'ask_quantity': sum([ask[1] for ask in orderbook['asks']])
            }
        except: return None

    def _fetch_funding_rate(self, exchange_name: str, symbol: str) -> Optional[dict]:
        try:
            exchange = self.exchanges[exchange_name]
            query_symbol = symbol
            if exchange_name == 'okx': query_symbol = f"{symbol.split('/')[0]}-USDT-SWAP"
            
            # Bybit 符號檢查
            if exchange_name == 'bybit' and query_symbol not in exchange.markets:
                return None

            # 1. 抓取即時費率
            rate_info = exchange.fetch_funding_rate(query_symbol)
            
            # 2. 抓取週期 (預設 8 小時)
            interval_hours = 8.0
            
            # 策略 A: 先查 Market Metadata (速度快)
            try:
                market = exchange.market(query_symbol)
                if 'fundingInterval' in market and market['fundingInterval']:
                    interval_hours = float(market['fundingInterval']) / 1000 / 3600
                elif 'info' in market and 'fundingIntervalHours' in market['info']:
                    interval_hours = float(market['info']['fundingIntervalHours'])
            except: pass
            
            # 策略 B: (大絕招) 查歷史紀錄 (最準確)
            # 如果 Metadata 查不到，或是回傳預設的 8 小時，我們就用歷史紀錄來驗算
            # 特別是針對 RIVER 這種可能是 1 小時但 API 沒寫的幣
            try:
                # 只對可疑的 8 小時或 0 進行驗算，節省資源
                if interval_hours == 8.0 or interval_hours <= 0:
                    # 抓最近 3 筆歷史結算紀錄
                    history = exchange.fetch_funding_rate_history(query_symbol, limit=3)
                    if history and len(history) >= 2:
                        # 取最後兩次的時間差
                        t1 = history[-2]['timestamp']
                        t2 = history[-1]['timestamp']
                        diff_hours = (t2 - t1) / (1000 * 3600)
                        
                        # 如果算出來是 1, 2, 4 等合理的整數，就採信它
                        if 0.5 <= diff_hours <= 24:
                            interval_hours = round(diff_hours, 1) # 例如 1.0
                            # print(f"🔍 [Debug] {exchange_name} {symbol} 透過歷史紀錄修正週期為: {interval_hours}h")
            except: 
                pass

            # 最後防呆: 真的算不出來就只好回傳 8
            if interval_hours <= 0: interval_hours = 8.0

            return {'rate': float(rate_info['fundingRate']), 'interval_hours': interval_hours}
        except: 
            return None

    def scan_funding_opportunities(self) -> List[Dict]:
        if self.use_mock: return self._generate_mock_opportunities()
        symbols = self.get_top_volume_symbols()
        opportunities = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(self._scan_single_symbol, symbol): symbol for symbol in symbols}
            for future in as_completed(futures):
                res = future.result()
                if res: opportunities.append(res)
        return sorted(opportunities, key=lambda x: x['apr'], reverse=True)

    def _scan_single_symbol(self, symbol: str) -> Optional[Dict]:
        try:
            rates, intervals = {}, {}
            # 依序抓取各交易所
            for ex_name in self.exchanges.keys():
                res = self._fetch_funding_rate(ex_name, symbol)
                if res:
                    rates[ex_name], intervals[ex_name] = res['rate'], res['interval_hours']
            
            if len(rates) < 2: return None
            
            sorted_rates = sorted(rates.items(), key=lambda x: x[1])
            min_ex, min_rate = sorted_rates[0]
            max_ex, max_rate = sorted_rates[-1]
            rate_diff = max_rate - min_rate
            
            long_data = self._fetch_orderbook_data(min_ex, symbol)
            short_data = self._fetch_orderbook_data(max_ex, symbol)
            
            if not long_data or not short_data: return None
            
            buy_price, sell_price = long_data['ask_price'], short_data['bid_price']
            fee_rate = (self.FEE_SCHEDULE.get(min_ex, {'taker': 0.0005})['taker'] + 
                        self.FEE_SCHEDULE.get(max_ex, {'taker': 0.0005})['taker'])
            
            spread_loss = (buy_price - sell_price) / sell_price
            
            # 總成本 = 手續費 + 價差絕對值
            total_cost_rate = fee_rate + abs(spread_loss)
            yield_per_settlement = abs(rate_diff)
            
            breakeven_times = total_cost_rate / yield_per_settlement if yield_per_settlement > 0 else 999
            
            # 取最小週期 (例如 RIVER 如果是 1h，這裡就會抓到 1h)
            funding_interval = min(intervals.get(min_ex, 8), intervals.get(max_ex, 8))
            if funding_interval <= 0: funding_interval = 8.0
            
            # APR 計算修正：根據真實週期
            # 如果是 1h: 24次/天
            # 如果是 8h: 3次/天
            times_per_day = 24 / funding_interval
            apr = rate_diff * times_per_day * 365 * 100
            
            return {
                'symbol': symbol, 'long_ex': min_ex, 'short_ex': max_ex,
                'long_price': buy_price, 'short_price': sell_price,
                'apr': apr,
                'rate_diff': rate_diff, 'funding_interval': funding_interval,
                'spread': spread_loss * 100, 'fees': fee_rate * 100,
                'total_cost': total_cost_rate * 100,
                'breakeven_times': breakeven_times,
                'depth': min(long_data['ask_quantity'], short_data['bid_quantity']),
                'timestamp': datetime.now()
            }
        except: return None

    def _generate_mock_opportunities(self) -> List[Dict]:
        return [{
            'symbol': 'BTC/USDT', 'long_ex': 'binance', 'short_ex': 'bybit',
            'long_price': 42150.5, 'short_price': 42148.2, 'apr': 25.8,
            'rate_diff': 0.0006, 'funding_interval': 8, 'spread': 0.005, 
            'fees': 0.11, 'total_cost': 0.115, 'breakeven_times': 1.9, 
            'depth': 120.5, 'timestamp': datetime.now()
        }]