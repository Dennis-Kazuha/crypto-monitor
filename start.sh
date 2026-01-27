#!/bin/bash

# 1. 在背景啟動 Worker (負責掃描資料庫)
# "&" 符號代表在背景執行，不會卡住
echo "🚀 Starting Worker..."
python worker.py &

# 2. 在前景啟動 Streamlit (負責網頁顯示)
echo "🚀 Starting Streamlit App..."
streamlit run app.py --server.port 8501 --server.address 0.0.0.0