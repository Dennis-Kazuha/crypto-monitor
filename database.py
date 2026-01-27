import sqlite3
import pandas as pd
import json
from datetime import datetime
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'market_data.db')

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scan_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT,
            timestamp DATETIME
        )
    ''')
    conn.commit()
    conn.close()

def save_latest_data(opportunities):
    if not opportunities: return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    json_data = json.dumps(opportunities, default=str)
    cursor.execute("INSERT INTO scan_results (data, timestamp) VALUES (?, ?)", (json_data, datetime.now()))
    cursor.execute("DELETE FROM scan_results WHERE id NOT IN (SELECT id FROM scan_results ORDER BY timestamp DESC LIMIT 10)")
    conn.commit()
    conn.close()

def load_latest_data():
    if not os.path.exists(DB_PATH): return None, None
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql('SELECT data, timestamp FROM scan_results ORDER BY timestamp DESC LIMIT 1', conn)
        conn.close()
        if not df.empty:
            return json.loads(df.iloc[0]['data']), df.iloc[0]['timestamp']
        return None, None
    except:
        conn.close()
        return None, None
