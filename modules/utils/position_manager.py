# modules/monitor/position_monitor.py
# 持倉監控主程式：每 10 秒檢查是否符合 TP/SL/閃崩條件，並執行平倉

import os
import sys
import time
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv

# === 修正導入路徑 ===
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# === 載入環境變數與動態參數 ===
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
from modules.okx_client import place_order, get_latest_price, get_kline_1m
from config.config import get_runtime_config

# === 初始檔案與預設參數（由 config.json 熱更新）===
POSITION_PATH = os.path.join(PROJECT_ROOT, "output/positions.json")
LOG_PATH = os.path.join(PROJECT_ROOT, "output/monitor_logs.json")

# === 倉位操作工具 ===
def load_positions() -> list:
    if not os.path.exists(POSITION_PATH):
        return []
    with open(POSITION_PATH, "r") as f:
        return json.load(f)

def save_positions(data: list):
    with open(POSITION_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def delete_position(pos: dict):
    all_pos = load_positions()
    updated = [p for p in all_pos if not (p["symbol"] == pos["symbol"] and p["direction"] == pos["direction"])]
    save_positions(updated)

def update_position(symbol: str, updates: dict):
    data = load_positions()
    for p in data:
        if p["symbol"] == symbol:
            p.update(updates)
    save_positions(data)

def clear_expired_positions(max_minutes: int):
    now = datetime.now()
    updated = []
    cleared = []

    for p in load_positions():
        created_at = datetime.strptime(p["timestamp"], "%Y-%m-%dT%H:%M:%S")
        if (now - created_at).total_seconds() >= max_minutes * 60:
            cleared.append(p)
        else:
            updated.append(p)

    save_positions(updated)
    return cleared

# === 平倉邏輯（支援浮動 TP/SL）===
def close_position(pos: dict, price: float, kline: list, strategy_conf: dict) -> str:
    symbol = pos["symbol"]
    direction = pos["direction"]
    size = pos["size"]
    entry = pos["entry_price"]

    tp_ratio = strategy_conf.get("TP_RATIO", 0.02)
    sl_ratio = strategy_conf.get("SL_RATIO", 0.01)

    # 動態高低點更新
    high = max(pos.get("highest", entry), price) if direction == "long" else pos.get("highest", price)
    low = min(pos.get("lowest", entry), price) if direction == "short" else pos.get("lowest", price)
    update_position(symbol, {"highest": high, "lowest": low})

    if direction == "long":
        tp_price = high * (1 - tp_ratio / 2)
        sl_price = high * (1 - sl_ratio)
    else:
        tp_price = low * (1 + tp_ratio / 2)
        sl_price = low * (1 + sl_ratio)

    # 閃崩檢查
    if kline:
        old_close = float(kline[0]["close"])
        drop = (old_close - price) / old_close
        if direction == "long" and drop >= 0.05:
            res = place_order(symbol, "sell", size, mgn_mode="isolated")
            if res.get("code") == "0":
                delete_position(pos)
                return f"⚠️ 閃崩強平成功：{symbol}"
            return f"❌ 閃崩強平失敗：{symbol}"

    # 正常 TP / SL 判斷
    if direction == "long":
        if price >= tp_price:
            side, reason = "sell", "TP"
        elif price <= sl_price:
            side, reason = "sell", "SL"
        else:
            return ""
    else:
        if price <= tp_price:
            side, reason = "buy", "TP"
        elif price >= sl_price:
            side, reason = "buy", "SL"
        else:
            return ""

    res = place_order(symbol, side, size, mgn_mode="isolated")
    if res.get("code") == "0":
        delete_position(pos)
        return f"✅ {reason} 平倉成功：{symbol} ({direction})"
    return f"❌ {reason} 平倉失敗：{symbol}"

# === 紀錄監控摘要 ===
def record_monitor_log(results: list, max_rounds: int):
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, "r") as f:
            logs = json.load(f)
    else:
        logs = []

    logs.append({
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "results": results
    })
    logs = logs[-max_rounds:]

    with open(LOG_PATH, "w") as f:
        json.dump(logs, f, indent=2, ensure_ascii=False)

# === 主監控流程 ===
def monitor_positions():
    print("🟡 持倉監控啟動中...")
    last_log_time = datetime.now()

    while True:
        try:
            config = get_runtime_config()
            strategies = config.get("STRATEGIES", {})
            monitor_conf = config.get("POSITION_MONITOR", {})
            interval_sec = monitor_conf.get("INTERVAL_SECONDS", 10)
            log_interval_min = monitor_conf.get("LOG_INTERVAL_MINUTES", 5)
            max_log_rounds = monitor_conf.get("MAX_LOG_ROUNDS", 5)
            max_position_min = monitor_conf.get("MAX_POSITION_MINUTES", 60)

            results = []
            positions = load_positions()

            for pos in positions:
                strategy = pos.get("strategy", "default")
                strategy_conf = strategies.get(strategy, {"TP_RATIO": 0.02, "SL_RATIO": 0.01})
                price = get_latest_price(pos["symbol"])
                kline = get_kline_1m(pos["symbol"], limit=1)

                if price:
                    res = close_position(pos, price, kline, strategy_conf)
                    if res:
                        print(res)
                        results.append(res)

            # 清除過期倉位
            expired = clear_expired_positions(max_position_min)
            for p in expired:
                print(f"🕒 自動清除過期倉位：{p['symbol']}")
                results.append(f"🕒 自動清除：{p['symbol']}")

            # 定時記錄輪巡結果
            if (datetime.now() - last_log_time) >= timedelta(minutes=log_interval_min):
                record_monitor_log(results, max_log_rounds)
                last_log_time = datetime.now()

        except Exception as e:
            print(f"⚠️ 監控錯誤：{e}")

        time.sleep(interval_sec)
# modules/utils/position_manager.py
# 倉位管理模組：儲存、更新、刪除、過期清除、筆數上限、TP/SL用

import os
import json
from datetime import datetime
from config.config import get_runtime_config

# === 設定 ===
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
POSITION_PATH = os.path.join(PROJECT_ROOT, "output/positions.json")

# === 輔助：讀寫倉位 ===
def load_positions() -> list:
    if not os.path.exists(POSITION_PATH):
        return []
    with open(POSITION_PATH, "r") as f:
        return json.load(f)

def save_positions(data: list):
    with open(POSITION_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ✅ ⬅️ 修復重點：補上 save_position()
def save_position(pos: dict):
    """
    加入新倉位（若超過最大筆數則略過）
    """
    config = get_runtime_config()
    max_positions = config.get("POSITION_MONITOR", {}).get("MAX_POSITION_COUNT", 5)

    all_pos = load_positions()
    if len(all_pos) >= max_positions:
        print(f"⚠️ 持倉筆數已達上限 {max_positions}，拒絕新倉位：{pos['symbol']}")
        return

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    pos["timestamp"] = now
    pos["created_at"] = now
    pos["highest"] = pos.get("entry_price")  # 初始化高點 / 低點
    pos["lowest"] = pos.get("entry_price")

    all_pos.append(pos)
    save_positions(all_pos)

def delete_position(pos: dict):
    all_pos = load_positions()
    updated = [p for p in all_pos if not (p["symbol"] == pos["symbol"] and p["direction"] == pos["direction"])]
    save_positions(updated)

def update_position(symbol: str, updates: dict):
    data = load_positions()
    for p in data:
        if p["symbol"] == symbol:
            p.update(updates)
    save_positions(data)

def clear_expired_positions(max_minutes: int):
    now = datetime.now()
    updated = []
    cleared = []

    for p in load_positions():
        created_at = datetime.strptime(p["timestamp"], "%Y-%m-%dT%H:%M:%S")
        if (now - created_at).total_seconds() >= max_minutes * 60:
            cleared.append(p)
        else:
            updated.append(p)

    save_positions(updated)
    return cleared

# === 直接執行 ===
if __name__ == "__main__":
    monitor_positions()