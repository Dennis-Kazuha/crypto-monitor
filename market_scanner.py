import ccxt
import pandas as pd
import numpy as np
import time
from datetime import datetime
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional
import threading

class SmartMarketScanner:
    """市場掃描器 - 支持無 API Key 查詢公開數據"""
    
    FEE_SCHEDULE = {
        'binance': {'maker': 0.0002, 'taker': 0.0005},
        'bybit': {'maker': 0.0001, 'taker': 0.0006},
        'okx': {'maker': 0.0002, 'taker': 0.0005}
    }
    
    def __init__(self, use_mock=False):
        self.use_mock = use_mock
        self.exchanges = {}
        self.history = {}
        self.cache = {}
        self.cache_lock = threading.Lock()
        
        if not use_mock:
            self._initialize_exchanges()
    
    def _initialize_exchanges(self):
        """初始化交易所（無需 API Key）"""
        try:
            # Binance
            self.exchanges['binance'] = ccxt.binance({
                'enableRateLimit': True,
                'options': {'defaultType': 'future'},
                'timeout': 30000
            })
            
            # Bybit
            self.exchanges['bybit'] = ccxt.bybit({
                'enableRateLimit': True,
                'options': {'defaultType': 'linear'},
                'timeout': 30000
            })
            
            # OKX
            self.exchanges['okx'] = ccxt.okx({
                'enableRateLimit': True,
                'options': {'defaultType': 'swap'},
                'timeout': 30000
            })
            
            print(f"✅ 初始化 {len(self.exchanges)} 個交易所")
        except Exception as e:
            print(f"❌ 初始化失敗: {e}")
    
    def get_top_volume_symbols(self, limit=30) -> List[str]:
        """獲取高交易量幣種 (已修復符號過濾問題)"""
        cache_key = 'top_symbols'
        
        if cache_key in self.cache:
            cached_time, cached_data = self.cache[cache_key]
            if time.time() - cached_time < 300:
                return cached_data
        
        if self.use_mock:
            return ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
        
        try:
            # 確保至少有初始化 Binance (作為篩選基準)
            if 'binance' not in self.exchanges:
                return ['BTC/USDT', 'ETH/USDT']
            
            # 抓取所有 Ticker
            tickers = self.exchanges['binance'].fetch_tickers()
            
            # [關鍵修正] 
            # 1. 允許符號中包含 :USDT (因為合約通常長這樣 BTC/USDT:USDT)
            # 2. 確保是 USDT 結算的合約
            valid_tickers = []
            for symbol, t in tickers.items():
                # 過濾掉非 USDT、BUSD 對、以及沒有成交量的
                if '/USDT' not in symbol: continue
                if 'BUSD' in symbol: continue
                if t.get('quoteVolume', 0) <= 0: continue
                
                valid_tickers.append(t)
            
            # 依成交量排序
            sorted_tickers = sorted(valid_tickers, key=lambda x: x['quoteVolume'], reverse=True)
            
            # 取出符號，並做簡單清洗 (把 :USDT 拿掉以便跨交易所比對)
            # 例如 BTC/USDT:USDT -> BTC/USDT
            result = []
            for t in sorted_tickers[:limit]:
                clean_symbol = t['symbol'].split(':')[0] 
                result.append(clean_symbol)
            
            # 寫入緩存
            with self.cache_lock:
                self.cache[cache_key] = (time.time(), result)
            
            return result

        except Exception as e:
            print(f"獲取幣種失敗: {e}")
            # 發生錯誤時的回退機制
            return ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT']
      
    
    def _fetch_orderbook_price(self, exchange_name: str, symbol: str, side: str) -> Optional[Dict]:
        """獲取盤口價格"""
        try:
            exchange = self.exchanges[exchange_name]
            
            query_symbol = symbol
            if exchange_name == 'okx':
                query_symbol = f"{symbol.split('/')[0]}-USDT-SWAP"
            
            orderbook = exchange.fetch_order_book(query_symbol, limit=5)
            
            if side == 'long':
                if orderbook['asks']:
                    price = orderbook['asks'][0][0]
                    depth = sum([ask[0] * ask[1] for ask in orderbook['asks'][:5]])
                else:
                    return None
            else:
                if orderbook['bids']:
                    price = orderbook['bids'][0][0]
                    depth = sum([bid[0] * bid[1] for bid in orderbook['bids'][:5]])
                else:
                    return None
            
            return {'price': price, 'depth': depth}
        except Exception as e:
            return None
    
    def _fetch_funding_rate(self, exchange_name: str, symbol: str) -> Optional[dict]:
        """獲取資金費率"""
        try:
            exchange = self.exchanges[exchange_name]
            
            query_symbol = symbol
            if exchange_name == 'okx':
                query_symbol = f"{symbol.split('/')[0]}-USDT-SWAP"
            
            rate_info = exchange.fetch_funding_rate(query_symbol)
            funding_rate = float(rate_info['fundingRate'])
            
            interval_hours = 8
            if 'fundingIntervalHours' in rate_info:
                interval_hours = rate_info['fundingIntervalHours']
            
            return {
                'rate': funding_rate,
                'interval_hours': int(interval_hours)
            }
        except Exception as e:
            return None
    
    def _calculate_fees(self, long_ex: str, short_ex: str) -> float:
        """計算手續費"""
        long_fee = self.FEE_SCHEDULE.get(long_ex, {'maker': 0.0002, 'taker': 0.0005})
        short_fee = self.FEE_SCHEDULE.get(short_ex, {'maker': 0.0002, 'taker': 0.0005})
        
        return long_fee['taker'] + short_fee['taker'] + long_fee['maker'] + short_fee['maker']
    
    def scan_funding_opportunities(self) -> List[Dict]:
        """掃描套利機會"""
        if self.use_mock:
            return self._generate_mock_opportunities()
        
        if not self.exchanges:
            print("❌ 沒有交易所連接")
            return []
        
        print(f"\n🔍 開始掃描...")
        symbols = self.get_top_volume_symbols()
        print(f"📊 掃描 {len(symbols)} 個幣種")
        
        opportunities = []
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(self._scan_single_symbol, symbol): symbol for symbol in symbols}
            
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    result = future.result()
                    if result:
                        opportunities.append(result)
                        print(f"✅ {symbol}: APR {result['apr']:.2f}%")
                except Exception as e:
                    print(f"❌ {symbol}: {e}")
        
        print(f"\n✅ 找到 {len(opportunities)} 個機會")
        return sorted(opportunities, key=lambda x: x['apr'], reverse=True)
    
    def _scan_single_symbol(self, symbol: str) -> Optional[Dict]:
        """掃描單個幣種"""
        try:
            # 1. 獲取資金費率
            rates = {}
            intervals = {}
            
            for ex_name in self.exchanges.keys():
                result = self._fetch_funding_rate(ex_name, symbol)
                if result:
                    rates[ex_name] = result['rate']
                    intervals[ex_name] = result['interval_hours']
            
            if len(rates) < 2:
                return None
            
            # 2. 找最高和最低費率
            sorted_rates = sorted(rates.items(), key=lambda x: x[1])
            min_ex, min_rate = sorted_rates[0]
            max_ex, max_rate = sorted_rates[-1]
            
            rate_diff = max_rate - min_rate
            
            # 3. 計算 APR
            funding_interval = min(intervals.get(min_ex, 8), intervals.get(max_ex, 8))
            times_per_day = 24 / funding_interval
            apr = rate_diff * times_per_day * 365 * 100
            
            # 4. 獲取盤口
            long_book = self._fetch_orderbook_price(min_ex, symbol, 'long')
            short_book = self._fetch_orderbook_price(max_ex, symbol, 'short')
            
            if not long_book or not short_book:
                return None
            
            long_price = long_book['price']
            short_price = short_book['price']
            
            # 5. 計算成本
            spread_cost = (long_price - short_price) / short_price if short_price > 0 else 0.01
            fee_cost = self._calculate_fees(min_ex, max_ex)
            total_cost = spread_cost + fee_cost
            
            # 6. 回本天數
            daily_yield = rate_diff * times_per_day
            
            if total_cost <= 0:
                breakeven_days = 0.0
            elif daily_yield > 0.000001:
                breakeven_days = total_cost / daily_yield
            else:
                breakeven_days = 999
            
            # 7. 深度
            depth = min(long_book['depth'], short_book['depth'])
            
            return {
                'symbol': symbol,
                'long_ex': min_ex,
                'short_ex': max_ex,
                'long_price': long_price,
                'short_price': short_price,
                'apr': apr,
                'rate_diff': rate_diff,
                'funding_interval': funding_interval,
                'times_per_day': times_per_day,
                'spread': spread_cost * 100,
                'fees': fee_cost * 100,
                'total_cost': total_cost * 100,
                'breakeven_days': breakeven_days,
                'depth': depth,
                'timestamp': datetime.now()
            }
        
        except Exception as e:
            return None
    
    def _generate_mock_opportunities(self) -> List[Dict]:
        """模擬數據"""
        return [
            {
                'symbol': 'BTC/USDT',
                'long_ex': 'binance',
                'short_ex': 'bybit',
                'long_price': 42150.5,
                'short_price': 42148.2,
                'apr': 25.8,
                'rate_diff': 0.0006,
                'funding_interval': 8,
                'times_per_day': 3,
                'spread': 0.005,
                'fees': 0.14,
                'total_cost': 0.145,
                'breakeven_days': 0.8,
                'depth': 8500000,
                'timestamp': datetime.now()
            }
        ]
