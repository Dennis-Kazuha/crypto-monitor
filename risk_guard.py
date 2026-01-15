import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
import os
import ccxt
import pandas as pd
import threading

@dataclass
class Position:
    """持倉詳細資訊"""
    exchange: str
    symbol: str
    side: str  # 'LONG' or 'SHORT'
    size: float
    entry_price: float
    current_price: float
    leverage: float
    margin: float
    unrealized_pnl: float
    entry_time: datetime
    fee_paid: float = 0.0  # 已支付手續費
    
    @property
    def roi(self) -> float:
        """投資回報率"""
        if self.margin <= 0:
            return 0
        return (self.unrealized_pnl / self.margin) * 100
    
    @property
    def holding_hours(self) -> float:
        """持倉時間（小時）"""
        return (datetime.now() - self.entry_time).total_seconds() / 3600
    
    @property
    def pnl_percentage(self) -> float:
        """盈虧百分比"""
        if self.entry_price <= 0:
            return 0
        
        if self.side == 'LONG':
            return ((self.current_price - self.entry_price) / self.entry_price) * 100
        else:  # SHORT
            return ((self.entry_price - self.current_price) / self.entry_price) * 100

@dataclass
class AccountState:
    """帳戶狀態"""
    name: str
    balance: float
    unrealized_pnl: float
    used_margin: float
    available_balance: float = 0
    total_positions: int = 0
    
    @property
    def equity(self) -> float:
        return self.balance + self.unrealized_pnl
    
    @property
    def margin_level(self) -> float:
        """保證金使用率"""
        if self.equity <= 0:
            return 1.0
        return self.used_margin / self.equity
    
    @property
    def risk_score(self) -> str:
        """風險評級"""
        level = self.margin_level
        if level < 0.3:
            return "🟢 安全"
        elif level < 0.5:
            return "🟡 注意"
        elif level < 0.7:
            return "🟠 警告"
        else:
            return "🔴 危險"

class DynamicRiskGuard:
    """
    動態風控系統 V2
    - 詳細持倉追蹤（入場價、ROI、持倉時間）
    - 5秒級監控
    - 自動平衡機制
    """
    
    # 風險閾值
    DANGER_MARGIN_LEVEL = 0.80  # 80% 觸發警報
    CRITICAL_MARGIN_LEVEL = 0.90  # 90% 緊急處理
    
    def __init__(self, use_mock=False):
        self.use_mock = use_mock
        self.accounts: Dict[str, AccountState] = {}
        self.exchanges: Dict[str, ccxt.Exchange] = {}
        self.positions: List[Position] = []
        self.position_cache = {}  # 持倉緩存（記錄入場信息）
        self.lock = threading.Lock()
        
        if not use_mock:
            self._initialize_exchanges()
        
        # 初始化帳戶
        self.accounts = {
            'binance': AccountState('Binance', 0, 0, 0),
            'bybit': AccountState('Bybit', 0, 0, 0),
            'okx': AccountState('OKX', 0, 0, 0)
        }
    
    def _initialize_exchanges(self):
        """初始化交易所連接"""
        try:
            if os.getenv('BINANCE_API_KEY'):
                self.exchanges['binance'] = ccxt.binance({
                    'apiKey': os.getenv('BINANCE_API_KEY'),
                    'secret': os.getenv('BINANCE_SECRET'),
                    'options': {'defaultType': 'future'},
                    'enableRateLimit': True
                })
            
            if os.getenv('BYBIT_API_KEY'):
                self.exchanges['bybit'] = ccxt.bybit({
                    'apiKey': os.getenv('BYBIT_API_KEY'),
                    'secret': os.getenv('BYBIT_SECRET'),
                    'options': {'defaultType': 'linear'},
                    'enableRateLimit': True
                })
            
            if os.getenv('OKX_API_KEY'):
                self.exchanges['okx'] = ccxt.okx({
                    'apiKey': os.getenv('OKX_API_KEY'),
                    'secret': os.getenv('OKX_SECRET'),
                    'password': os.getenv('OKX_PASSWORD'),
                    'options': {'defaultType': 'swap'},
                    'enableRateLimit': True
                })
            
            print(f"✅ 風控系統連接 {len(self.exchanges)} 個交易所")
        except Exception as e:
            print(f"⚠️ 風控初始化失敗: {e}")
    
    def update_states(self):
        """更新所有帳戶狀態（並發）"""
        with self.lock:
            self.positions = []
            
            if self.use_mock:
                self._mock_update()
                return
            
            from concurrent.futures import ThreadPoolExecutor
            
            def update_exchange(name, exchange):
                try:
                    # 1. 獲取餘額
                    bal = exchange.fetch_balance()
                    total = float(bal['total'].get('USDT', 0))
                    free = float(bal['free'].get('USDT', 0))
                    
                    # 2. 獲取持倉
                    positions = exchange.fetch_positions()
                    
                    total_pnl = 0
                    total_margin = 0
                    position_count = 0
                    
                    for p in positions:
                        contracts = float(p.get('contracts', 0) or 0)
                        if contracts == 0:
                            continue
                        
                        position_count += 1
                        
                        # 基本信息
                        symbol = p['symbol']
                        side = p['side'].upper() if p['side'] else 'UNKNOWN'
                        entry_price = float(p.get('entryPrice', 0) or 0)
                        current_price = float(p.get('markPrice', 0) or p.get('lastPrice', 0) or 0)
                        leverage = float(p.get('leverage', 1) or 1)
                        margin = float(p.get('initialMargin', 0) or 0)
                        pnl = float(p.get('unrealizedPnl', 0) or 0)
                        
                        total_pnl += pnl
                        total_margin += margin
                        
                        # 持倉緩存（記錄入場時間）
                        cache_key = f"{name}:{symbol}:{side}"
                        if cache_key not in self.position_cache:
                            self.position_cache[cache_key] = {
                                'entry_time': datetime.now(),
                                'fee_paid': 0
                            }
                        
                        # 創建持倉對象
                        position = Position(
                            exchange=name.upper(),
                            symbol=symbol,
                            side=side,
                            size=contracts,
                            entry_price=entry_price,
                            current_price=current_price,
                            leverage=leverage,
                            margin=margin,
                            unrealized_pnl=pnl,
                            entry_time=self.position_cache[cache_key]['entry_time'],
                            fee_paid=self.position_cache[cache_key]['fee_paid']
                        )
                        
                        self.positions.append(position)
                    
                    # 更新帳戶
                    self.accounts[name].balance = total
                    self.accounts[name].unrealized_pnl = total_pnl
                    self.accounts[name].used_margin = total_margin
                    self.accounts[name].available_balance = free
                    self.accounts[name].total_positions = position_count
                    
                except Exception as e:
                    print(f"更新 {name} 失敗: {e}")
            
            # 並發更新
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [
                    executor.submit(update_exchange, name, exchange)
                    for name, exchange in self.exchanges.items()
                ]
                
                for future in futures:
                    future.result()
    
    def get_positions_df(self) -> pd.DataFrame:
        """返回詳細持倉 DataFrame"""
        if self.use_mock:
            return self._get_mock_positions_df()
        
        if not self.positions:
            return pd.DataFrame()
        
        data = []
        for p in self.positions:
            data.append({
                '交易所': p.exchange,
                '幣種': p.symbol,
                '方向': p.side,
                '數量': f"{p.size:.4f}",
                '槓桿': f"{p.leverage:.1f}x",
                '入場價': f"${p.entry_price:,.2f}",
                '當前價': f"${p.current_price:,.2f}",
                '盈虧%': p.pnl_percentage,
                '未實現損益': p.unrealized_pnl,
                'ROI': p.roi,
                '保證金': f"${p.margin:,.2f}",
                '持倉時間': f"{p.holding_hours:.1f}h",
                '已付手續費': f"${p.fee_paid:.2f}"
            })
        
        return pd.DataFrame(data)
    
    def _get_mock_positions_df(self) -> pd.DataFrame:
        """模擬持倉數據"""
        return pd.DataFrame([
            {
                '交易所': 'BINANCE',
                '幣種': 'BTC/USDT',
                '方向': 'SHORT',
                '數量': '0.5000',
                '槓桿': '3.0x',
                '入場價': '$42,150.00',
                '當前價': '$42,120.00',
                '盈虧%': 0.071,
                '未實現損益': 15.0,
                'ROI': 0.21,
                '保證金': '$7,025.00',
                '持倉時間': '12.5h',
                '已付手續費': '$10.50'
            },
            {
                '交易所': 'BYBIT',
                '幣種': 'BTC/USDT',
                '方向': 'LONG',
                '數量': '0.5000',
                '槓桿': '3.0x',
                '入場價': '$42,148.00',
                '當前價': '$42,120.00',
                '盈虧%': -0.066,
                '未實現損益': -14.0,
                'ROI': -0.20,
                '保證金': '$7,024.67',
                '持倉時間': '12.5h',
                '已付手續費': '$10.50'
            }
        ])
    
    def _mock_update(self):
        """模擬數據更新"""
        # 模擬帳戶
        self.accounts['binance'] = AccountState(
            'Binance', 10000, 15, 7025, 2975, 1
        )
        self.accounts['bybit'] = AccountState(
            'Bybit', 10000, -14, 7024, 2986, 1
        )
        self.accounts['okx'] = AccountState(
            'OKX', 5000, 0, 0, 5000, 0
        )
    
    def check_risks(self) -> List[str]:
        """檢查風險並返回警告"""
        warnings = []
        
        for name, account in self.accounts.items():
            level = account.margin_level
            
            if level >= self.CRITICAL_MARGIN_LEVEL:
                warnings.append(
                    f"🚨 {name.upper()} 保證金使用率 {level*100:.1f}% - 極度危險！"
                )
            elif level >= self.DANGER_MARGIN_LEVEL:
                warnings.append(
                    f"⚠️ {name.upper()} 保證金使用率 {level*100:.1f}% - 需要注意"
                )
        
        return warnings
    
    def balance_security_transfer(self) -> List[str]:
        """自動平衡資金（從盈利倉位轉至風險倉位）"""
        logs = []
        
        # 檢查風險
        warnings = self.check_risks()
        if warnings:
            logs.extend(warnings)
        
        # TODO: 實現自動轉帳邏輯
        # 1. 識別高風險帳戶
        # 2. 識別盈利倉位
        # 3. 執行內部轉帳
        
        return logs
    
    def get_summary_stats(self) -> Dict:
        """獲取統計摘要"""
        total_equity = sum(acc.equity for acc in self.accounts.values())
        total_pnl = sum(acc.unrealized_pnl for acc in self.accounts.values())
        total_positions = len(self.positions)
        
        avg_margin_level = sum(acc.margin_level for acc in self.accounts.values()) / len(self.accounts)
        
        return {
            'total_equity': total_equity,
            'total_pnl': total_pnl,
            'total_positions': total_positions,
            'avg_margin_level': avg_margin_level,
            'timestamp': datetime.now()
        }
