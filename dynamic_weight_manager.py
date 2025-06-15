import os
import json
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict
from config import get_runtime_config
from logger import log

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
RESULT_DIR = os.path.join(BASE_DIR, "json_results")
os.makedirs(RESULT_DIR, exist_ok=True)

HISTORY_DB_PATH = os.path.join(RESULT_DIR, "selection_history.db")
COMBO_DB_PATH = os.path.join(RESULT_DIR, "indicator_combination_log.db")

def get_history_db_connection():
    conn = sqlite3.connect(HISTORY_DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def get_combo_db_connection():
    conn = sqlite3.connect(COMBO_DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_history_db():
    with get_history_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS selection_history (
                symbol TEXT PRIMARY KEY,
                consecutive INTEGER DEFAULT 0,
                confidence REAL DEFAULT 0,
                win_rate REAL DEFAULT 0.5,
                dynamic_weight TEXT DEFAULT '{}',
                last_seen INTEGER DEFAULT 0
            )
        ''')
        conn.commit()

def load_selection_history():
    init_history_db()
    with get_history_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT symbol, consecutive, confidence, win_rate, dynamic_weight, last_seen FROM selection_history")
        rows = cursor.fetchall()

    history = {}
    for symbol, consecutive, confidence, win_rate, dw_json, last_seen in rows:
        try:
            dw = json.loads(dw_json)
        except Exception:
            dw = {}
        history[symbol] = {
            "consecutive": consecutive,
            "confidence": confidence,
            "win_rate": win_rate,
            "dynamic_weight": dw,
            "last_seen": last_seen
        }
    return history

def save_selection_history(history):
    init_history_db()
    with get_history_db_connection() as conn:
        cursor = conn.cursor()
        for symbol, record in history.items():
            dw_json = json.dumps(record.get("dynamic_weight", {}))
            cursor.execute('''
                INSERT INTO selection_history (symbol, consecutive, confidence, win_rate, dynamic_weight, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    consecutive=excluded.consecutive,
                    confidence=excluded.confidence,
                    win_rate=excluded.win_rate,
                    dynamic_weight=excluded.dynamic_weight,
                    last_seen=excluded.last_seen
            ''', (symbol, record.get("consecutive",0), record.get("confidence",0),
                  record.get("win_rate",0.5), dw_json, record.get("last_seen",0)))
        conn.commit()

def load_trade_actions():
    """
    從 indicator_combination_log.db 讀取近30天內的交易紀錄。
    """
    trades = []
    cutoff_ts = int((datetime.now() - timedelta(days=30)).timestamp())
    try:
        with get_combo_db_connection() as conn:
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

                trades.append({
                    "symbol": sym,
                    "direction": direction,
                    "confidence": confidence,
                    "indicators": indicators,
                    "timestamp": ts,
                    "log_timestamp": log_ts,
                    # 需要時可擴充其他欄位
                })
    except Exception as e:
        log(f"[錯誤] 讀取交易紀錄失敗: {e}", level="ERROR")
    return trades

def analyze_trade_performance(trades):
    stats = defaultdict(lambda: {"buy": {"win":0, "count":0, "total_pnl":0}, "sell": {"win":0, "count":0, "total_pnl":0}})

    cutoff_ts = int((datetime.now() - timedelta(days=30)).timestamp())

    for t in trades:
        ts = t.get("timestamp", 0)
        if ts < cutoff_ts:
            continue
        sym = t.get("symbol")
        dir_ = t.get("direction")
        pnl = t.get("pnl", 0)
        op = t.get("operation", "").lower()
        if op != "close":
            continue  # 只分析平倉績效
        if sym and dir_ in ("buy", "sell"):
            stats[sym][dir_]["count"] += 1
            stats[sym][dir_]["total_pnl"] += pnl
            if pnl > 0:
                stats[sym][dir_]["win"] += 1

    results = {}
    for sym, dirs in stats.items():
        results[sym] = {}
        for dir_, val in dirs.items():
            c = val["count"]
            if c > 0:
                win_rate = val["win"] / c
                avg_pnl = val["total_pnl"] / c
            else:
                win_rate = 0.0
                avg_pnl = 0.0
            results[sym][dir_] = {
                "win_rate": round(win_rate, 4),
                "avg_pnl": round(avg_pnl, 4),
                "count": c
            }
    return results

def update_dynamic_weights():
    history = load_selection_history()
    trades = load_trade_actions()

    perf = analyze_trade_performance(trades)

    WIN_RATE_THRESHOLD = 0.55
    MIN_COUNT_FOR_UPDATE = 5
    ALPHA = 0.3

    changed = False

    for sym, dir_stats in perf.items():
        for direction in ("buy", "sell"):
            stat = dir_stats.get(direction)
            if not stat or stat["count"] < MIN_COUNT_FOR_UPDATE:
                continue

            base_weight = history.get(sym, {}).get("dynamic_weight", 1.0)
            win_rate = stat["win_rate"]

            if win_rate >= WIN_RATE_THRESHOLD:
                new_weight = base_weight + ALPHA * (win_rate - WIN_RATE_THRESHOLD)
            else:
                new_weight = base_weight - ALPHA * (WIN_RATE_THRESHOLD - win_rate)

            new_weight = max(0.5, min(new_weight, 2.0))

            if sym not in history:
                history[sym] = {}

            if "dynamic_weight" not in history[sym]:
                history[sym]["dynamic_weight"] = {}

            if isinstance(history[sym]["dynamic_weight"], dict):
                history[sym]["dynamic_weight"][direction] = round(new_weight, 4)
            else:
                history[sym]["dynamic_weight"] = {direction: round(new_weight, 4)}

            changed = True

    if changed:
        save_selection_history(history)
        log(f"[動態權重] 已更新 selection_history 資料庫")

if __name__ == "__main__":
    update_dynamic_weights()
