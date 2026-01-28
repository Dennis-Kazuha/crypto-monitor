import ccxt
import pandas as pd
import numpy as np
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional
import threading

class SmartMarketScanner:
    """市場掃描器 - 全交易所修正版 (修復 BadSymbol 問題)"""
    
    FEE_SCHEDULE = {
        'binance': {'maker': 0.0002, 'taker': 0.0005},
        'okx': {'maker': 0.0002, 'taker': 0.0005},
        'bybit': {'maker': 0.0002, 'taker': 0.00055},
        'hyperliquid': {'maker': 0.0001, 'taker': 0.00035}
    }
    
    def __init__(self, use_mock=False):
        self.use_mock = use_mock
        self.exchanges = {}
        if not use_mock:
            self._initialize_exchanges()
    
    def _initialize_exchanges(self):
        common_config = {
            'enableRateLimit': True, 
            'timeout': 30000
        }
        
        # 這裡的 defaultType 雖然設了，但有些交易所仍需要符號後綴(:USDT)才能精確識別
        configs = [
            ('binance', {'options': {'defaultType': 'future'}}),
            ('okx', {'options': {'defaultType': 'swap'}}),
            ('bybit', {'options': {'defaultType': 'linear'}}),
            ('hyperliquid', {})
        ]
        
        print("⏳ [System] 正在初始化交易所...")
        
        for name, config in configs:
            try:
                if not hasattr(ccxt, name):
                    print(f"⚠️ [Warning] CCXT 版本不支援 {name}，已跳過。")
                    continue
                
                exchange_class = getattr(ccxt, name)
                self.exchanges[name] = exchange_class({**common_config, **config})
                
            except Exception as e:
                print(f"❌ {name} 初始化失敗: {e}")

        print("⏳ [System] 正在下載合約規格說明書 (Load Markets)...")
        
        def load_market(ex_name):
            try:
                self.exchanges[ex_name].load_markets()
            except Exception as e:
                print(f"⚠️ {ex_name} 市場載入失敗: {e}")

        if self.exchanges:
            with ThreadPoolExecutor(max_workers=5) as executor:
                executor.map(load_market, self.exchanges.keys())
        else:
            print("❌ 沒有任何交易所初始化成功！")

    def get_top_volume_symbols(self, limit=40) -> List[str]:
        if self.use_mock: return ['BTC/USDT', 'ETH/USDT']
        
        fallback = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'DOGE/USDT', 'XRP/USDT', 'BNB/USDT', 'ADA/USDT']
        
        try:
            if 'binance' not in self.exchanges:
                return fallback

            tickers = self.exchanges['binance'].fetch_tickers()
            valid = [t for s, t in tickers.items() if '/USDT' in s and 'BUSD' not in s and t.get('quoteVolume', 0) > 0]
            sorted_tickers = sorted(valid, key=lambda x: x['quoteVolume'], reverse=True)
            return [t['symbol'].split(':')[0] for t in sorted_tickers[:limit]]
        except Exception as e:
            return fallback

    def _get_query_symbol(self, exchange_name: str, symbol: str) -> Optional[str]:
        """
        關鍵修正：針對不同交易所加上正確的 Unified Symbol 後綴
        解決 'BadSymbol' 和 'supports contract markets only' 錯誤
        """
        ex = self.exchanges.get(exchange_name)
        if not ex: return None

        # 基礎符號，例如 "BTC/USDT"
        base_symbol = symbol 
        
        # --- 針對各交易所的特殊轉換規則 ---
        
        if exchange_name == 'okx':
            # OKX Swap 統一格式通常是 SYMBOL/USDT:USDT
            # 這裡我們嘗試轉換，如果 markets 裡有就用，沒有就用原始的
            target = f"{symbol}:USDT"
            if ex.markets and target in ex.markets:
                return target
            # 如果統一格式找不到，回退到舊版拼法 (不建議，但為了相容)
            return f"{symbol.split('/')[0]}-USDT-SWAP"

        elif exchange_name == 'bybit':
            # Bybit Linear 合約必須加上 :USDT 才能區分現貨
            return f"{symbol}:USDT"

        elif exchange_name == 'hyperliquid':
            # Hyperliquid 是 USDC 本位，且通常需要 :USDC 後綴
            return symbol.replace('/USDT', '/USDC') + ":USDC"
            
        elif exchange_name == 'binance':
            # Binance 設定了 defaultType='future' 後，直接用 BTC/USDT 即可
            # 但加上 :USDT 也是安全的標準寫法
            return f"{symbol}:USDT"

        return base_symbol

    def _fetch_orderbook_data(self, exchange_name: str, symbol: str) -> Optional[Dict]:
        try:
            exchange = self.exchanges.get(exchange_name)
            if not exchange: return None
            
            # 使用修正後的符號查詢
            query_symbol = self._get_query_symbol(exchange_name, symbol)
            if not query_symbol: return None

            # 加一層保護：如果符號真的不在市場裡 (例如某幣在該交易所沒上架)，直接返回
            if exchange.markets and query_symbol not in exchange.markets:
                # 再次嘗試寬容模式 (有些交易所 API 接受不標準符號)
                pass 

            orderbook = exchange.fetch_order_book(query_symbol, limit=20)
            
            bid_qty = sum([bid[1] for bid in orderbook['bids']])
            ask_qty = sum([ask[1] for ask in orderbook['asks']])

            return {
                'bid_price': orderbook['bids'][0][0] if orderbook['bids'] else None,
                'ask_price': orderbook['asks'][0][0] if orderbook['asks'] else None,
                'bid_quantity': bid_qty,
                'ask_quantity': ask_qty
            }
        except: return None

    def _fetch_funding_rate(self, exchange_name: str, symbol: str) -> Optional[dict]:
        try:
            exchange = self.exchanges.get(exchange_name)
            if not exchange: return None

            query_symbol = self._get_query_symbol(exchange_name, symbol)
            if not query_symbol: return None

            # 1. 抓取即時費率
            rate_info = exchange.fetch_funding_rate(query_symbol)
            
            # 2. 抓取週期
            interval_hours = 8.0
            
            try:
                market = exchange.market(query_symbol)
                if 'fundingInterval' in market and market['fundingInterval']:
                    val = float(market['fundingInterval'])
                    interval_hours = val / 1000 / 3600 if val > 100 else val
                elif 'info' in market and 'fundingIntervalHours' in market['info']:
                    interval_hours = float(market['info']['fundingIntervalHours'])
            except: pass
            
            if interval_hours == 8.0 or interval_hours <= 0:
                try:
                    history = exchange.fetch_funding_rate_history(query_symbol, limit=3)
                    if history and len(history) >= 2:
                        diff = (history[-1]['timestamp'] - history[-2]['timestamp']) / (1000 * 3600)
                        if 0.5 <= diff <= 24: interval_hours = round(diff, 1)
                except: pass

            return {'rate': float(rate_info['fundingRate']), 'interval_hours': interval_hours if interval_hours > 0 else 8.0}
        except: 
            return None

    def scan_funding_opportunities(self) -> List[Dict]:
        if self.use_mock: return self._generate_mock_opportunities()
        
        print(f"🔍 [Debug] 目前啟用交易所: {list(self.exchanges.keys())}")
        symbols = self.get_top_volume_symbols(limit=40)
        opportunities = []
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(self._scan_single_symbol, symbol): symbol for symbol in symbols}
            for future in as_completed(futures):
                try:
                    res = future.result()
                    if res: opportunities.append(res)
                except: pass
        
        return sorted(opportunities, key=lambda x: x['apr'], reverse=True)

    def _scan_single_symbol(self, symbol: str) -> Optional[Dict]:
        try:
            rates, intervals = {}, {}
            
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
            
            total_cost_rate = fee_rate + abs(spread_loss)
            yield_per_settlement = abs(rate_diff)
            breakeven_times = total_cost_rate / yield_per_settlement if yield_per_settlement > 0 else 999
            
            funding_interval = min(intervals.get(min_ex, 8), intervals.get(max_ex, 8))
            if funding_interval <= 0: funding_interval = 8.0
            
            apr = rate_diff * (24 / funding_interval) * 365 * 100
            depth = min(long_data['ask_quantity'], short_data['bid_quantity'])
            
            return {
                'symbol': symbol, 'long_ex': min_ex, 'short_ex': max_ex,
                'long_price': buy_price, 'short_price': sell_price, 'apr': apr,
                'rate_diff': rate_diff, 'funding_interval': funding_interval,
                'spread': spread_loss * 100, 'fees': fee_rate * 100,
                'total_cost': total_cost_rate * 100, 'breakeven_times': breakeven_times,
                'depth': depth, 'timestamp': datetime.now()
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