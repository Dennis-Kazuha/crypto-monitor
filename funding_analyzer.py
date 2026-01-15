"""
資金費率深度分析器
基於幣安資金費率計算機制的完整實現

核心邏輯：
1. 溢價指數（Premium Index）
2. 衝擊買賣價格（Impact Bid/Ask）
3. 時間加權移動平均（TWAP）
4. 深度加權取樣
"""

import ccxt
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import deque
import time

class FundingRateAnalyzer:
    """
    資金費率深度分析器
    
    實現幣安資金費率計算邏輯：
    - 指數價格（多交易所加權平均）
    - 溢價指數（基於衝擊價格）
    - 時間加權移動平均（TWAP）
    - 資金費率預測
    """
    
    # 衝擊保證金額標準（USD）
    IMPACT_NOTIONAL = {
        'BTC': 50000,    # BTC 約 5萬美金
        'ETH': 40000,    # ETH 約 4萬
        'BNB': 10000,    # BNB 等主流幣
        'SOL': 10000,
        'default_high': 10000,   # 熱門山寨幣 1萬
        'default_mid': 5000,     # 中等幣種 5千
        'default_low': 1000      # 冷門幣種 1千
    }
    
    # 基礎利率（通常是 0.01%）
    BASE_RATE = 0.0001  # 0.01%
    
    # 資金費率限制範圍
    RATE_CLAMP = 0.0005  # ±0.05%
    
    def __init__(self, exchanges: Dict[str, ccxt.Exchange]):
        """
        初始化分析器
        
        Args:
            exchanges: 交易所實例字典 {'binance': exchange_obj, ...}
        """
        self.exchanges = exchanges
        self.premium_history = {}  # 溢價指數歷史（用於TWAP）
        self.max_history_size = 5760  # 8小時 = 5760個5秒
        
    def get_impact_notional(self, symbol: str) -> float:
        """
        獲取衝擊保證金額
        
        不同幣種有不同的標準化交易量
        計算公式：200 / 最大槓桿的初始保證金比率
        """
        base_currency = symbol.split('/')[0]
        
        # 檢查是否有專屬配置
        if base_currency in self.IMPACT_NOTIONAL:
            return self.IMPACT_NOTIONAL[base_currency]
        
        # 根據幣種市值/流動性估算
        # 這裡簡化處理，實際可以通過API獲取
        return self.IMPACT_NOTIONAL['default_mid']
    
    def calculate_impact_price(
        self, 
        orderbook: Dict, 
        side: str, 
        notional_amount: float
    ) -> Optional[float]:
        """
        計算衝擊價格
        
        模擬用一定數量的市價單買入/賣出後的平均成交價
        
        Args:
            orderbook: 訂單簿 {'bids': [[price, size], ...], 'asks': [...]}
            side: 'buy' 或 'sell'
            notional_amount: 衝擊保證金額（USD）
        
        Returns:
            平均成交價格，如果深度不足則返回 None
        """
        try:
            if side == 'buy':
                # 買入：從賣盤（asks）吃單
                orders = orderbook['asks']
            else:
                # 賣出：從買盤（bids）吃單
                orders = orderbook['bids']
            
            if not orders:
                return None
            
            total_cost = 0  # 總成本
            total_qty = 0   # 總數量
            remaining = notional_amount
            
            for price, size in orders:
                price = float(price)
                size = float(size)
                
                # 該價位的總金額
                order_value = price * size
                
                if remaining >= order_value:
                    # 全部吃掉這一檔
                    total_cost += order_value
                    total_qty += size
                    remaining -= order_value
                else:
                    # 部分吃掉
                    partial_qty = remaining / price
                    total_cost += remaining
                    total_qty += partial_qty
                    remaining = 0
                    break
            
            if total_qty == 0:
                return None
            
            # 平均成交價
            avg_price = total_cost / total_qty
            return avg_price
            
        except Exception as e:
            print(f"計算衝擊價格失敗: {e}")
            return None
    
    def calculate_spot_index_price(
        self, 
        symbol: str, 
        exchanges_list: List[str] = None
    ) -> Optional[float]:
        """
        計算指數價格（多交易所現貨價格加權平均）
        
        模擬幣安從多家交易所取得現貨價格並加權平均
        權重根據成交量分配
        
        Args:
            symbol: 交易對
            exchanges_list: 參與計算的交易所列表
        
        Returns:
            加權平均後的指數價格
        """
        if exchanges_list is None:
            exchanges_list = list(self.exchanges.keys())
        
        prices = []
        volumes = []
        
        for ex_name in exchanges_list:
            try:
                exchange = self.exchanges[ex_name]
                ticker = exchange.fetch_ticker(symbol)
                
                price = ticker.get('last')
                volume = ticker.get('quoteVolume', 0)
                
                if price and volume and volume > 0:
                    prices.append(float(price))
                    volumes.append(float(volume))
            except:
                continue
        
        if not prices:
            return None
        
        # 按成交量加權平均
        total_volume = sum(volumes)
        if total_volume == 0:
            return np.mean(prices)
        
        weighted_price = sum(p * v for p, v in zip(prices, volumes)) / total_volume
        return weighted_price
    
    def calculate_premium_index(
        self,
        symbol: str,
        exchange_name: str
    ) -> Optional[Dict]:
        """
        計算溢價指數（Premium Index）
        
        公式：
        溢價指數 = [max(0, 衝擊買方價格 - 指數價格) - 
                   max(0, 指數價格 - 衝擊賣方價格)] / 指數價格
        
        Args:
            symbol: 交易對
            exchange_name: 交易所名稱
        
        Returns:
            {
                'premium_index': float,
                'impact_bid': float,
                'impact_ask': float,
                'spot_index': float,
                'timestamp': datetime
            }
        """
        try:
            exchange = self.exchanges[exchange_name]
            
            # 1. 獲取指數價格（多交易所加權平均）
            spot_index = self.calculate_spot_index_price(symbol)
            if not spot_index:
                return None
            
            # 2. 獲取訂單簿
            query_symbol = symbol
            if exchange_name == 'okx':
                query_symbol = f"{symbol.split('/')[0]}-USDT-SWAP"
            
            orderbook = exchange.fetch_order_book(query_symbol, limit=50)
            
            # 3. 獲取衝擊保證金額
            notional = self.get_impact_notional(symbol)
            
            # 4. 計算衝擊買賣價格
            impact_bid = self.calculate_impact_price(orderbook, 'sell', notional)
            impact_ask = self.calculate_impact_price(orderbook, 'buy', notional)
            
            if not impact_bid or not impact_ask:
                return None
            
            # 5. 計算溢價指數
            buy_premium = max(0, impact_ask - spot_index)
            sell_premium = max(0, spot_index - impact_bid)
            
            premium_index = (buy_premium - sell_premium) / spot_index
            
            return {
                'premium_index': premium_index,
                'impact_bid': impact_bid,
                'impact_ask': impact_ask,
                'spot_index': spot_index,
                'orderbook_depth': len(orderbook['bids']) + len(orderbook['asks']),
                'timestamp': datetime.now()
            }
            
        except Exception as e:
            print(f"計算溢價指數失敗 {exchange_name} {symbol}: {e}")
            return None
    
    def calculate_funding_rate(
        self,
        premium_index: float,
        base_rate: float = None
    ) -> float:
        """
        計算資金費率
        
        公式：
        資金費率 = 溢價指數 + clamp(基礎利率 - 溢價指數, -0.05%, 0.05%)
        
        Args:
            premium_index: 溢價指數
            base_rate: 基礎利率（默認 0.01%）
        
        Returns:
            資金費率
        """
        if base_rate is None:
            base_rate = self.BASE_RATE
        
        # clamp(基礎利率 - 溢價指數, -0.05%, 0.05%)
        clamped = np.clip(
            base_rate - premium_index,
            -self.RATE_CLAMP,
            self.RATE_CLAMP
        )
        
        funding_rate = premium_index + clamped
        
        return funding_rate
    
    def update_premium_history(
        self,
        symbol: str,
        exchange_name: str,
        premium_data: Dict
    ):
        """
        更新溢價指數歷史（用於TWAP）
        
        每5秒取樣一次，保存8小時數據
        """
        key = f"{exchange_name}:{symbol}"
        
        if key not in self.premium_history:
            self.premium_history[key] = deque(maxlen=self.max_history_size)
        
        self.premium_history[key].append({
            'premium_index': premium_data['premium_index'],
            'timestamp': premium_data['timestamp']
        })
    
    def calculate_twap_premium(
        self,
        symbol: str,
        exchange_name: str
    ) -> Optional[float]:
        """
        計算時間加權移動平均（TWAP）溢價指數
        
        公式：
        TWAP = (1×溢價1 + 2×溢價2 + ... + n×溢價n) / (1 + 2 + ... + n)
        
        越接近當下的數據權重越高
        
        Args:
            symbol: 交易對
            exchange_name: 交易所名稱
        
        Returns:
            TWAP 溢價指數
        """
        key = f"{exchange_name}:{symbol}"
        
        if key not in self.premium_history:
            return None
        
        history = list(self.premium_history[key])
        
        if not history:
            return None
        
        n = len(history)
        
        # 時間加權：1, 2, 3, ..., n
        weighted_sum = sum(
            (i + 1) * data['premium_index']
            for i, data in enumerate(history)
        )
        
        # 權重總和：1 + 2 + ... + n = n(n+1)/2
        weight_sum = n * (n + 1) / 2
        
        twap = weighted_sum / weight_sum
        
        return twap
    
    def get_predicted_funding_rate(
        self,
        symbol: str,
        exchange_name: str
    ) -> Optional[Dict]:
        """
        獲取預測資金費率
        
        基於當前溢價指數和歷史TWAP
        
        Returns:
            {
                'current_premium': float,       # 當前溢價指數
                'twap_premium': float,          # TWAP溢價指數
                'predicted_rate': float,        # 預測資金費率
                'actual_rate': float,           # 實際資金費率（API）
                'deviation': float,             # 偏差
                'confidence': str               # 置信度
            }
        """
        try:
            # 1. 計算當前溢價指數
            premium_data = self.calculate_premium_index(symbol, exchange_name)
            if not premium_data:
                return None
            
            # 2. 更新歷史
            self.update_premium_history(symbol, exchange_name, premium_data)
            
            # 3. 計算TWAP（如果有足夠歷史數據）
            twap_premium = self.calculate_twap_premium(symbol, exchange_name)
            
            # 4. 使用TWAP計算預測費率（如果有），否則用當前溢價
            premium_for_calc = twap_premium if twap_premium is not None else premium_data['premium_index']
            predicted_rate = self.calculate_funding_rate(premium_for_calc)
            
            # 5. 獲取實際資金費率（從API）
            exchange = self.exchanges[exchange_name]
            query_symbol = symbol
            if exchange_name == 'okx':
                query_symbol = f"{symbol.split('/')[0]}-USDT-SWAP"
            
            rate_info = exchange.fetch_funding_rate(query_symbol)
            actual_rate = float(rate_info['fundingRate'])
            
            # 6. 計算偏差
            deviation = abs(predicted_rate - actual_rate)
            
            # 7. 評估置信度
            if deviation < 0.0001:  # <0.01%
                confidence = "高"
            elif deviation < 0.0003:  # <0.03%
                confidence = "中"
            else:
                confidence = "低"
            
            return {
                'symbol': symbol,
                'exchange': exchange_name,
                'current_premium': premium_data['premium_index'],
                'twap_premium': twap_premium,
                'predicted_rate': predicted_rate,
                'actual_rate': actual_rate,
                'deviation': deviation,
                'confidence': confidence,
                'impact_bid': premium_data['impact_bid'],
                'impact_ask': premium_data['impact_ask'],
                'spot_index': premium_data['spot_index'],
                'orderbook_depth': premium_data['orderbook_depth'],
                'timestamp': premium_data['timestamp']
            }
            
        except Exception as e:
            print(f"獲取預測資金費率失敗 {exchange_name} {symbol}: {e}")
            return None
    
    def analyze_funding_stability(
        self,
        symbol: str,
        exchange_name: str,
        lookback_minutes: int = 60
    ) -> Optional[Dict]:
        """
        分析資金費率穩定性
        
        Args:
            symbol: 交易對
            exchange_name: 交易所名稱
            lookback_minutes: 回看時間（分鐘）
        
        Returns:
            {
                'mean': float,           # 平均溢價指數
                'std': float,            # 標準差
                'min': float,            # 最小值
                'max': float,            # 最大值
                'trend': str,            # 趨勢（上升/下降/穩定）
                'stability_score': float # 穩定性評分（0-1）
            }
        """
        key = f"{exchange_name}:{symbol}"
        
        if key not in self.premium_history:
            return None
        
        history = list(self.premium_history[key])
        
        if not history:
            return None
        
        # 只看最近N分鐘的數據（每5秒一個點）
        lookback_points = lookback_minutes * 12  # 60分鐘 = 720個點
        recent_history = history[-lookback_points:] if len(history) > lookback_points else history
        
        if len(recent_history) < 10:  # 至少要有10個數據點
            return None
        
        premiums = [d['premium_index'] for d in recent_history]
        
        mean = np.mean(premiums)
        std = np.std(premiums)
        min_val = np.min(premiums)
        max_val = np.max(premiums)
        
        # 計算趨勢（簡單線性回歸斜率）
        x = np.arange(len(premiums))
        slope = np.polyfit(x, premiums, 1)[0]
        
        if slope > 0.00001:
            trend = "上升"
        elif slope < -0.00001:
            trend = "下降"
        else:
            trend = "穩定"
        
        # 穩定性評分（標準差越小越穩定）
        # 0.0001 (0.01%) 以下算非常穩定
        stability_score = max(0, 1 - std / 0.001)
        
        return {
            'mean': mean,
            'std': std,
            'min': min_val,
            'max': max_val,
            'range': max_val - min_val,
            'trend': trend,
            'slope': slope,
            'stability_score': stability_score,
            'sample_count': len(premiums)
        }
