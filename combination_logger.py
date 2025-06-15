import os
import json
import threading
import sqlite3
from datetime import datetime, timedelta
from config import get_runtime_config
from logger import log

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
RESULT_DIR = os.path.join(BASE_DIR, "json_results")
os.makedirs(RESULT_DIR, exist_ok=True)

COMBO_DB_PATH = os.path.join(RESULT_DIR, "indicator_combination_log.db")

_log_lock = threading.Lock()

def get_db_connection():
    conn = sqlite3.connect(COMBO_DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def init_combo_db():
    with _log_lock:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS indicator_combination_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    direction TEXT,
                    confidence REAL,
                    indicators TEXT,
                    timestamp INTEGER,
                    log_timestamp INTEGER
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_symbol ON indicator_combination_log(symbol)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_log_timestamp ON indicator_combination_log(log_timestamp)')
            conn.commit()

def record_performance(trade_log: dict):
    # 保留使用 jsonl 格式存交易績效，這段不動
    try:
        with open(os.path.join(RESULT_DIR, "performance_logs.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(trade_log, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[績效紀錄錯誤] {e}")

def log_combination_result(result: dict) -> bool:
    config = get_runtime_config()
    init_combo_db()
    log_entry = {
        "symbol": result.get("symbol"),
        "direction": result.get("direction"),
        "confidence": result.get("confidence"),
        "indicators": json.dumps(result.get("indicators", {}), ensure_ascii=False),
        "timestamp": result.get("timestamp"),
        "log_timestamp": int(datetime.now().timestamp())
    }

    try:
        with _log_lock:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO indicator_combination_log
                    (symbol, direction, confidence, indicators, timestamp, log_timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (log_entry["symbol"], log_entry["direction"], log_entry["confidence"], log_entry["indicators"], log_entry["timestamp"], log_entry["log_timestamp"]))

                max_records = config.get("MAX_COMBINATION_LOGS", 5000)
                cursor.execute(f'''
                    DELETE FROM indicator_combination_log 
                    WHERE id NOT IN (
                        SELECT id FROM indicator_combination_log 
                        ORDER BY log_timestamp DESC LIMIT ?
                    )
                ''', (max_records,))

                conn.commit()

            log(f"[INFO] 紀錄指標組合：{log_entry['symbol']} (信心: {log_entry['confidence']})", level="INFO")
            return True
    except Exception as e:
        log(f"[錯誤] 寫入指標組合紀錄失敗: {e}", level="ERROR")
        return False

def load_trade_actions():
    """
    從 indicator_combination_log.db 讀取交易紀錄
    並回傳 list[dict]，方便動態權重計算用
    只回傳 近30 天內的交易
    """
    trades = []
    cutoff_ts = int((datetime.now() - timedelta(days=30)).timestamp())
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT symbol, direction, confidence, indicators, timestamp, log_timestamp
                FROM indicator_combination_log
                WHERE log_timestamp >= ?
                ORDER BY log_timestamp DESC
            ''', (cutoff_ts,))
            rows = cursor.fetchall()
            for row in rows:
                sym, direction, confidence, indicators_json, ts, log_ts = row
                try:
                    indicators = json.loads(indicators_json)
                except Exception:
                    indicators = {}

                trade = {
                    "symbol": sym,
                    "direction": direction,
                    "confidence": confidence,
                    "indicators": indicators,
                    "timestamp": ts,
                    "log_timestamp": log_ts,
                    # 你需要的其他欄位可自行擴充
                }
                trades.append(trade)
    except Exception as e:
        log(f"[錯誤] 讀取交易紀錄失敗: {e}", level="ERROR")
    return trades
