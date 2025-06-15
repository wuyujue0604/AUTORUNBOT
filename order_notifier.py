import os
import json
import time
from datetime import datetime, timedelta

# === 📁 設定儲存路徑 ===
BASE_DIR = os.path.dirname(__file__)
RESULT_DIR = os.path.join(BASE_DIR, "json_results")
os.makedirs(RESULT_DIR, exist_ok=True)
TRADE_LOG_PATH = os.path.join(RESULT_DIR, "trade_actions.json")
EVENT_LOG_PATH = os.path.join(RESULT_DIR, "event_logs.json")

# === 📦 載入與儲存工具 ===
def _load_json(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        print(f"[錯誤] 儲存 {path} 失敗")

# === 🟢 寫入交易紀錄 ===
def log_trade_action(symbol, operation, direction, confidence, price, contracts, pnl=None):
    now = int(time.time())
    data = _load_json(TRADE_LOG_PATH)
    data.append({
        "symbol": symbol,
        "operation": operation,
        "direction": direction,
        "confidence": confidence,
        "price": price,
        "contracts": contracts,
        "pnl": pnl,
        "timestamp": now
    })
    cutoff = now - 7 * 86400
    data = [d for d in data if d.get("timestamp", 0) >= cutoff]
    _save_json(TRADE_LOG_PATH, data)

# === 🛑 寫入異常事件 ===
def log_event(event_type, source, message):
    now = int(time.time())
    data = _load_json(EVENT_LOG_PATH)
    data.append({
        "type": event_type,
        "source": source,
        "message": message,
        "timestamp": now
    })
    cutoff = now - 7 * 86400
    data = [d for d in data if d.get("timestamp", 0) >= cutoff]
    _save_json(EVENT_LOG_PATH, data)

# === ⏲️ 保留備用推播函式（後續實作） ===
def flush_discord_notifications():
    print("[模擬] 發送 Discord 紀錄摘要...（尚未實作）")