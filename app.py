import streamlit as st
import pandas as pd
from datetime import datetime
from database import load_latest_data

st.set_page_config(page_title="Crypto Arbitrage Monitor", page_icon="📊", layout="wide")
st.title("📊 虛擬貨幣資費監控平台")

opportunities, last_update = load_latest_data()

if last_update:
    st.caption(f"數據來源：後台自動掃描 | 最後更新時間: {last_update}")

if opportunities:
    df = pd.DataFrame(opportunities)
    display_df = pd.DataFrame({
        '幣種': df['symbol'],
        '做多': df['long_ex'].str.upper(),
        '做空': df['short_ex'].str.upper(),
        '買入價': df['long_price'].map('${:,.4f}'.format),
        '賣出價': df['short_price'].map('${:,.4f}'.format),
        '結算週期': df['funding_interval'].apply(lambda x: f"{x}h"),
        '費率差': (df['rate_diff'] * 100).map('{:.4f}%'.format),
        '預估 APR': df['apr'].map('{:.2f}%'.format),
        '價差 (Spread)': df['spread'].map('{:.3f}%'.format),
        '手續費 (Fees)': df['fees'].map('{:.3f}%'.format),
        '總成本': df['total_cost'].map('{:.3f}%'.format),
        '回本結算次數': df['breakeven_times'].map('{:.1f} 次'.format),
        '掛單數量 (Depth)': df['depth'].map('{:,.2f}'.format)
    })
    st.dataframe(display_df, use_container_width=True, height=700)
else:
    st.warning("📉 暫無數據。請確保後台掃描服務 `worker.py` 正在運行。")
