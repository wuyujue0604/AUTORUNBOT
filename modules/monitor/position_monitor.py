# modules/monitor/position_monitor.py
# 持倉監控模組：支援 TP/SL、閃崩、浮虧保底、回吐出場、風險等級通知

import os
import sys
import time
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv

# === 修正路徑 ===
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# === 載入環境與模組 ===
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
from modules.okx_client import place_order, get_latest_price, get_kline_1m
from config.config import get_runtime_config

# === 檔案路徑 ===
POSITION_PATH = os.path.join(PROJECT_ROOT, "output/positions.json")
NOTIFY_PATH = os.path.join(PROJECT_ROOT, "output/order_notifications.json")
LOG_PATH = os.path.join(PROJECT_ROOT, "output/monitor_logs.json")

# === 常數參數 ===
MAX_LOG_ROUNDS = 5
DEFAULT_MONITOR_INTERVAL = 10  # 秒
HIGH_VOL_SYMBOLS = ["DOGE-USDT-SWAP", "AIDOGE-USDT-SWAP"]
FALL_RATIO = 0.05
MAX_LOSS_RATIO = 0.03
RETRACE_PROFIT_RATIO = 0.05
RETRACE_DROP_RATIO = 0.01

# === 基礎 I/O ===
def load_positions():
    if not os.path.exists(POSITION_PATH):
        return []
    with open(POSITION_PATH, "r") as f:
        return json.load(f)

def save_positions(data):
    with open(POSITION_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def delete_position(pos):
    data = load_positions()
    updated = [p for p in data if not (p["symbol"] == pos["symbol"] and p["direction"] == pos["direction"])]
    save_positions(updated)

def update_position(symbol, updates):
    data = load_positions()
    for p in data:
        if p["symbol"] == symbol:
            p.update(updates)
    save_positions(data)

def append_notification(data):
    if os.path.exists(NOTIFY_PATH):
        with open(NOTIFY_PATH, "r") as f:
            existing = json.load(f)
    else:
        existing = []
    existing.append(data)
    with open(NOTIFY_PATH, "w") as f:
        json.dump(existing[-200:], f, indent=2, ensure_ascii=False)


# === 判斷平倉條件並執行 ===
def close_position(pos, price, kline, conf):
    symbol = pos["symbol"]
    direction = pos["direction"]
    entry = pos["entry_price"]
    size = pos["size"]
    strategy_key = pos.get("strategy_key") or f"{pos.get('strategy', 'unknown')}-{direction}"

    tp_ratio = conf.get("TP_RATIO", 0.02)
    sl_ratio = conf.get("SL_RATIO", 0.01)

    # 更新最高/最低價格
    highest = max(pos.get("highest", entry), price)
    lowest = min(pos.get("lowest", entry), price)
    update_position(symbol, {"highest": highest, "lowest": lowest})

    # 計算 TP / SL 價格
    tp_price = highest * (1 - tp_ratio / 2) if direction == "long" else lowest * (1 + tp_ratio / 2)
    sl_price = highest * (1 - sl_ratio) if direction == "long" else lowest * (1 + sl_ratio)

    level, reason, side = "info", None, None

    # === 閃崩偵測（long） ===
    if kline:
        prev_close = float(kline[0]["close"])
        drop = (prev_close - price) / prev_close
        if direction == "long" and drop >= FALL_RATIO:
            reason, side, level = "FALL", "sell", "alert"

    # === 浮虧保底出場（雙向）===
    pnl_ratio = (price - entry) / entry if direction == "long" else (entry - price) / entry
    if pnl_ratio < -MAX_LOSS_RATIO:
        reason = "FORCE_LOSS"
        side = "sell" if direction == "long" else "buy"
        level = "alert"

    # === 高獲利後回吐出場 ===
    retrace_from = highest if direction == "long" else lowest
    peak_gain = (retrace_from - entry) / entry if direction == "long" else (entry - retrace_from) / entry
    current_gain = (price - entry) / entry if direction == "long" else (entry - price) / entry
    if peak_gain > RETRACE_PROFIT_RATIO and (peak_gain - current_gain) > RETRACE_DROP_RATIO:
        reason = "RETRACE"
        side = "sell" if direction == "long" else "buy"
        level = "warn"

    # === 一般 TP / SL 判斷 ===
    if not reason:
        if direction == "long":
            if price >= tp_price:
                reason, side, level = "TP", "sell", "info"
            elif price <= sl_price:
                reason, side, level = "SL", "sell", "alert"
        else:
            if price <= tp_price:
                reason, side, level = "TP", "buy", "info"
            elif price >= sl_price:
                reason, side, level = "SL", "buy", "alert"

    if not reason:
        return ""

    # === 下單平倉 ===
    res = place_order(symbol, side, size, mgn_mode="isolated")
    if res.get("code") == "0":
        delete_position(pos)
        append_notification({
            "symbol": symbol,
            "direction": direction,
            "action": "close",
            "size": size,
            "price": price,
            "reason": reason,
            "strategy_key": strategy_key,
            "risk_level": "high" if pnl_ratio < -0.03 else "low" if pnl_ratio > 0.03 else "medium",
            "level": level,
            "timestamp": datetime.now().isoformat()
        })
        return f"✅ {reason} 平倉成功：{symbol} @ {price:.4f}"
    else:
        return f"❌ {reason} 平倉失敗：{symbol}"

# === 清除過期持倉 ===
def clear_expired_positions(timeout_min=60):
    now = datetime.now()
    remaining, cleared = [], []
    for p in load_positions():
        ts = datetime.fromisoformat(p["timestamp"])
        if (now - ts).total_seconds() > timeout_min * 60:
            cleared.append(p)
        else:
            remaining.append(p)
    save_positions(remaining)

    for p in cleared:
        append_notification({
            "symbol": p["symbol"],
            "direction": p["direction"],
            "action": "close",
            "size": p["size"],
            "price": None,
            "reason": "EXPIRE",
            "strategy_key": p.get("strategy_key", "unknown"),
            "risk_level": "medium",
            "level": "warn",
            "timestamp": datetime.now().isoformat()
        })

    return cleared

# === 監控主循環 ===
def monitor_positions():
    print("🟡 持倉監控啟動...")
    last_log_time = datetime.now()
    while True:
        try:
            config = get_runtime_config()
            strategies = config.get("STRATEGIES", {})
            timeout = config.get("POSITION_TIMEOUT_MIN", 60)
            debug = config.get("debug_mode", False)

            results = []
            for pos in load_positions():
                symbol = pos["symbol"]
                strategy_key = pos.get("strategy_key") or f"{pos.get('strategy', 'default')}-{pos['direction']}"
                conf = strategies.get(strategy_key, {"TP_RATIO": 0.02, "SL_RATIO": 0.01})
                price = get_latest_price(symbol)
                kline = get_kline_1m(symbol, limit=1)
                if price:
                    result = close_position(pos, price, kline, conf)
                    if result:
                        print(result)
                        results.append(result)
                    elif debug:
                        print(f"🔍 未觸發 TP/SL：{symbol} @ {price:.4f}")

            expired = clear_expired_positions(timeout)
            for p in expired:
                results.append(f"🕒 過期平倉：{p['symbol']}")

            # 每 5 分鐘寫入一次監控紀錄
            if (datetime.now() - last_log_time).total_seconds() > 300:
                with open(LOG_PATH, "w") as f:
                    json.dump(results[-MAX_LOG_ROUNDS:], f, indent=2, ensure_ascii=False)
                last_log_time = datetime.now()

        except Exception as e:
            print(f"⚠️ 監控錯誤：{str(e)}")

        # 動態監控間隔
        sleep_time = 5 if any(p["symbol"] in HIGH_VOL_SYMBOLS for p in load_positions()) else DEFAULT_MONITOR_INTERVAL
        time.sleep(sleep_time)

# === 執行進入點 ===
if __name__ == "__main__":
    monitor_positions()