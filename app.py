import streamlit as st
import pandas as pd
import numpy as np
import time
import os
from datetime import datetime
import plotly.graph_objects as go
from dotenv import load_dotenv

# 導入自定義模組
from market_scanner import SmartMarketScanner
from risk_guard import DynamicRiskGuard

# 載入環境變數
load_dotenv()

# 頁面配置
st.set_page_config(
    page_title="Crypto Arbitrage Dashboard",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .stMetric { background-color: #161b22; padding: 15px; border-radius: 10px; border: 1px solid #30363d; }
    .stDataFrame { border: 1px solid #30363d; }
    </style>
    """, unsafe_allow_html=True)

# 初始化 Session State
if 'scanner' not in st.session_state: st.session_state.scanner = None
if 'guard' not in st.session_state: st.session_state.guard = None
if 'last_update' not in st.session_state: st.session_state.last_update = "---"

# 側邊欄控制
st.sidebar.title("⚙️ 系統控制")
# 預設勾選模擬，取消勾選即為實戰模式
use_mock = st.sidebar.checkbox("使用模擬數據 (Mock Data)", value=True)
refresh_rate = st.sidebar.slider("自動刷新頻率 (秒)", 10, 300, 60)

if st.sidebar.button("立即手動刷新"):
    st.rerun()

# 初始化或更新實例 (當模式切換時重新初始化)
if st.session_state.scanner is None or st.session_state.scanner.use_mock != use_mock:
    st.session_state.scanner = SmartMarketScanner(use_mock=use_mock)
    st.session_state.guard = DynamicRiskGuard(use_mock=use_mock)

# 執行邏輯
with st.spinner('正在獲取數據... (若為實戰模式，請稍候 API 回應)'):
    # 1. 更新帳戶狀態
    st.session_state.guard.update_states()
    # 2. 掃描市場
    opportunities = st.session_state.scanner.scan_funding_opportunities()
    st.session_state.last_update = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# 主界面
st.title("🚀 Crypto Arbitrage 智能監控系統")
st.caption(f"最後更新: {st.session_state.last_update} | 模式: {'模擬 (Mock)' if use_mock else '🔴 實戰 (Live)'}")

# 第一排：風險儀表板
st.subheader("🛡️ 帳戶風險監控")
cols = st.columns(3)
ex_keys = ['binance', 'bybit', 'okx']

for i, name in enumerate(ex_keys):
    with cols[i]:
        acc = st.session_state.guard.accounts.get(name)
        if acc:
            # 顏色邏輯
            color = "green"
            lvl = acc.margin_level
            if lvl > 0.8: color = "red"
            elif lvl > 0.6: color = "orange"
            
            fig = go.Figure(go.Indicator(
                mode = "gauge+number",
                value = lvl * 100,
                domain = {'x': [0, 1], 'y': [0, 1]},
                title = {'text': f"{acc.name} Risk (%)", 'font': {'size': 18}},
                gauge = {
                    'axis': {'range': [None, 100]},
                    'bar': {'color': color},
                    'bgcolor': "rgba(0,0,0,0)",
                    'threshold': {'line': {'color': "red", 'width': 4}, 'thickness': 0.75, 'value': 80}
                }
            ))
            fig.update_layout(height=200, margin=dict(l=20, r=20, t=30, b=20), paper_bgcolor='rgba(0,0,0,0)', font={'color': "white"})
            st.plotly_chart(fig, use_container_width=True)
            
            m1, m2 = st.columns(2)
            m1.metric("權益 (Equity)", f"${acc.equity:,.0f}")
            m2.metric("未實現盈虧", f"${acc.unrealized_pnl:,.0f}", delta_color="normal")
        else:
            st.warning(f"{name} 無數據")

# 第二排：套利機會表格
st.subheader("🔥 最佳資金費率套利機會")

if not opportunities:
    st.info("😴 目前無高於 1% 年化之機會，或 API 連線異常。")
else:
    df = pd.DataFrame(opportunities)
    
    # 準備顯示用的 DataFrame
    display_df = pd.DataFrame({
        '幣種': df['symbol'],
        '預估年化 (APR)': df['apr'].map('{:.2f}%'.format),
        '價差 (Spread %)': df['spread_price'].map('{:.3f}%'.format),
        '深度 (Depth U)': df['depth'].apply(lambda x: f"${x/1000:.1f}k" if x > 1000 else f"${x:.0f}"),
        '做空 (Short)': df['short_ex'].str.upper(),
        '做多 (Long)': df['long_ex'].str.upper(),
        '穩定度': df['sigma'].map('{:.5f}'.format)
    })
    
    # 顏色邏輯
    def color_spread(val):
        try:
            val_float = float(val.replace('%', ''))
            # 正數(紅)代表成本，負數(綠)代表利潤
            color = '#ff4b4b' if val_float > 0.1 else '#00cc96' # 0.1%以上標紅
            return f'color: {color}'
        except:
            return ''

    st.dataframe(
        display_df.style.applymap(color_spread, subset=['價差 (Spread %)']),
        use_container_width=True,
        height=400
    )

# 第三排：資產安全 Log
st.subheader("💰 資產劃轉建議")
logs = st.session_state.guard.balance_security_transfer()
if logs:
    for log in logs:
        st.warning(log)
else:
    st.success("✅ 目前資產分佈健康")

# 自動刷新
if refresh_rate > 0:
    time.sleep(refresh_rate)
    st.rerun()