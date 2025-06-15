import os
import json
from datetime import datetime, timedelta
import time
from logger import log

TRADE_LOG_PATH = "json_results/trade_logs.jsonl"  # 可改成你的實際路徑
WEIGHT_CACHE_PATH = "json_results/tf_weight_cache.json"  # 權重快取路徑

MIN_TRADES_THRESHOLD = 10  # 最小交易筆數門檻
EWMA_ALPHA = 0.3           # EWMA平滑係數

TIME_FRAMES = ["1h", "15m"]  # 支援時間框架列表

def load_recent_trades(days=30):
    cutoff_ts = int((datetime.now() - timedelta(days=days)).timestamp())
    trades = []
    if not os.path.exists(TRADE_LOG_PATH):
        log(f"[警告] 找不到交易紀錄檔案: {TRADE_LOG_PATH}", level="WARN")
        return trades
    try:
        with open(TRADE_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                data = json.loads(line)
                ts = data.get("timestamp", 0)
                if ts >= cutoff_ts:
                    trades.append(data)
    except Exception as e:
        log(f"[錯誤] 讀取交易紀錄失敗: {e}", level="ERROR")
    return trades

def calc_winrate_and_count(trades, timeframe):
    """
    計算該時間框架的勝率與交易筆數。
    只分析平倉(close)且符合時間框架的交易。
    """
    filtered = [t for t in trades if t.get("operation") == "close" and t.get("timeframe") == timeframe]
    count = len(filtered)
    if count == 0:
        return 0.0, 0
    wins = sum(1 for t in filtered if t.get("pnl", 0) > 0)
    winrate = wins / count
    return winrate, count

def ewma_update(prev, new, alpha=EWMA_ALPHA):
    if prev is None:
        return new
    return alpha * new + (1 - alpha) * prev

def load_weight_cache():
    if not os.path.exists(WEIGHT_CACHE_PATH):
        return {}
    try:
        with open(WEIGHT_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"[錯誤] 讀取權重快取失敗: {e}", level="ERROR")
        return {}

def save_weight_cache(cache):
    try:
        os.makedirs(os.path.dirname(WEIGHT_CACHE_PATH), exist_ok=True)
        with open(WEIGHT_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"[錯誤] 寫入權重快取失敗: {e}", level="ERROR")

def calculate_dynamic_weights(days=30):
    trades = load_recent_trades(days)
    cache = load_weight_cache()

    results = {}
    changed = False

    for tf in TIME_FRAMES:
        winrate, count = calc_winrate_and_count(trades, tf)

        prev_ewma = cache.get(tf, {}).get("ewma_winrate")
        new_ewma = ewma_update(prev_ewma, winrate)

        results[tf] = {
            "ewma_winrate": new_ewma,
            "count": count
        }

        if tf not in cache or abs(cache.get(tf, {}).get("ewma_winrate", 0) - new_ewma) > 0.001:
            changed = True

    if changed:
        save_weight_cache(results)

    # 計算權重分配，考慮交易筆數門檻與最小權重0.1
    min_weight = 0.1
    adjusted = []
    for tf in TIME_FRAMES:
        stat = results.get(tf, {})
        ewma_winrate = stat.get("ewma_winrate", 0.5)
        count = stat.get("count", 0)
        w = ewma_winrate if count >= MIN_TRADES_THRESHOLD else 0.5  # 筆數不夠時用中性值
        adjusted.append(max(min_weight, w))

    total = sum(adjusted)
    weights = {tf: adj / total for tf, adj in zip(TIME_FRAMES, adjusted)}

    # Debug log
    log(f"[動態權重] 計算結果: {weights}, 來源勝率與筆數: {results}")

    return weights

# 簡單測試程式
if __name__ == "__main__":
    w = calculate_dynamic_weights()
    print("動態時間框架權重:", w)
