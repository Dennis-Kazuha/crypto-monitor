import time
from dataclasses import dataclass
import os
import ccxt

@dataclass
class AccountState:
    name: str
    balance: float       # 錢包餘額
    unrealized_pnl: float # 未實現盈虧
    used_margin: float    # 已用保證金
    
    @property
    def equity(self):
        return self.balance + self.unrealized_pnl
    
    @property
    def margin_level(self):
        """風險率: 越小越安全，越大越危險 (>80% 危險)"""
        if self.equity <= 0: return 0.99 # 防止除以零
        return self.used_margin / self.equity

class DynamicRiskGuard:
    def __init__(self, use_mock=False):
        self.use_mock = use_mock
        self.accounts = {}
        self.exchanges = {}

        if not use_mock:
            # 初始化交易所 (需填寫 API Key 才能讀取餘額)
            try:
                if os.getenv('BINANCE_API_KEY'):
                    self.exchanges['binance'] = ccxt.binance({
                        'apiKey': os.getenv('BINANCE_API_KEY'),
                        'secret': os.getenv('BINANCE_SECRET'),
                        'options': {'defaultType': 'future'}
                    })
                
                if os.getenv('BYBIT_API_KEY'):
                    self.exchanges['bybit'] = ccxt.bybit({
                        'apiKey': os.getenv('BYBIT_API_KEY'), # 修正拼字
                        'secret': os.getenv('BYBIT_SECRET'),
                        'options': {'defaultType': 'linear'}
                    })

                if os.getenv('OKX_API_KEY'):
                    self.exchanges['okx'] = ccxt.okx({
                        'apiKey': os.getenv('OKX_API_KEY'),
                        'secret': os.getenv('OKX_SECRET'),
                        'password': os.getenv('OKX_PASSWORD'), # 修正加入 Password
                        'options': {'defaultType': 'swap'}
                    })
            except Exception as e:
                print(f"❌ 風控系統連線失敗: {e}")
        
        # 初始化顯示用的空帳戶狀態 (避免 UI 報錯)
        self.accounts = {
            'binance': AccountState('Binance', 0, 0, 0),
            'bybit':   AccountState('Bybit', 0, 0, 0),
            'okx':     AccountState('OKX', 0, 0, 0)
        }

    def update_states(self):
        """
        更新所有帳戶水位
        """
        if self.use_mock:
            self._mock_update()
            return

        for name, exchange in self.exchanges.items():
            try:
                # 抓取餘額
                balance = exchange.fetch_balance()
                total_wallet = float(balance['total']['USDT']) if 'USDT' in balance['total'] else 0.0
                
                # 抓取持倉以計算未實現盈虧 (UPNL) 和 保證金
                # 注意：不同交易所 API 結構不同，這裡做通用簡化處理
                positions = exchange.fetch_positions()
                total_pnl = 0.0
                total_margin = 0.0

                for p in positions:
                    if p['contracts'] > 0: # 只算有持倉的
                        pnl = float(p['unrealizedPnl']) if p['unrealizedPnl'] else 0.0
                        margin = float(p['initialMargin']) if p['initialMargin'] else 0.0
                        total_pnl += pnl
                        total_margin += margin

                self.accounts[name].balance = total_wallet
                self.accounts[name].unrealized_pnl = total_pnl
                self.accounts[name].used_margin = total_margin

            except Exception as e:
                print(f"⚠️ 無法更新 {name} 帳戶狀態 (檢查 API Key?): {e}")

    def _mock_update(self):
        """模擬數據變動"""
        import numpy as np
        # 第一次模擬給一些初始值
        if self.accounts['binance'].balance == 0:
            self.accounts['binance'] = AccountState('Binance', 10500, 50, 2500)
            self.accounts['bybit'] = AccountState('Bybit', 9800, -20, 3100)
            self.accounts['okx'] = AccountState('OKX', 6000, -150, 4800)
        
        # 隨機跳動
        for name in self.accounts:
            self.accounts[name].unrealized_pnl += np.random.uniform(-10, 10)

    def balance_security_transfer(self):
        """
        資產安全檢查建議
        """
        logs = []
        if self.use_mock: return [] # 模擬模式不回傳 Log 以免干擾

        try:
            equities = {k: v.equity for k, v in self.accounts.items() if v.equity > 0}
            if not equities: return []

            avg_equity = sum(equities.values()) / len(equities)
            for name, eq in equities.items():
                diff = eq - avg_equity
                if diff > 2000: # 偏差超過 2000 U 才提示
                    logs.append(f"💎 {name} 獲利累積 (${diff:.0f}) -> 建議劃轉至低水位帳戶")
        except:
            pass
            
        return logs