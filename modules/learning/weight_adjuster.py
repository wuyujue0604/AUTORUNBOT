# modules/learning/weight_adjuster.py
# 策略權重自動調整模組（根據 order_notifications.json 的績效）

import os
import sys
import json
from datetime import datetime, timedelta
import math

# === 修正路徑 ===
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# === 檔案路徑 ===
NOTIFY_PATH = os.path.join(PROJECT_ROOT, "output/order_notifications.json")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config/strategy_config.json")
LOG_PATH = os.path.join(PROJECT_ROOT, "output/weight_adjust_log.json")
STAT_DIR = os.path.join(PROJECT_ROOT, "output/strategy_stats")

# === 可調參數（建議寫入 config）===
DAYS_BACK = 3
MIN_WEIGHT = 0.05
MAX_WEIGHT = 0.5
FALLBACK_WEIGHT = 0.1

# 評分加權參數（可擴充）
SCORE_WEIGHTS = {
    "win_rate": 0.6,
    "pnl_score": 0.4
}

# === Softmax 計算（避免極端差距）===
def softmax(scores):
    exp_scores = [math.exp(s) for s in scores]
    total = sum(exp_scores)
    return [s / total for s in exp_scores]

# === 主函式：調整策略權重 ===
def adjust_strategy_weights():
    if not os.path.exists(NOTIFY_PATH):
        print("❌ 找不到通知檔")
        return

    with open(NOTIFY_PATH, "r") as f:
        records = json.load(f)

    now = datetime.now()
    cutoff = now - timedelta(days=DAYS_BACK)
    stats = {}

    # === 統計各策略績效 ===
    for r in records:
        if r.get("action") != "close" or not r.get("strategy_key"):
            continue
        ts = datetime.fromisoformat(r["timestamp"])
        if ts < cutoff:
            continue

        key = r["strategy_key"]
        reason = r.get("reason")

        if key not in stats:
            stats[key] = {"total": 0, "win": 0, "pnl_sum": 0}

        stats[key]["total"] += 1
        if reason in ["TP", "RETRACE"]:
            stats[key]["win"] += 1
            stats[key]["pnl_sum"] += 1
        elif reason in ["SL", "FORCE_LOSS"]:
            stats[key]["pnl_sum"] -= 1

    if not stats:
        print("⚠️ 無策略績效可分析")
        return

    # === 計算加權得分與 softmax ===
    keys = list(stats.keys())
    raw_scores, detailed_stats = [], {}

    for k in keys:
        total = stats[k]["total"]
        win_rate = stats[k]["win"] / total if total > 0 else 0
        pnl = stats[k]["pnl_sum"]
        score = (SCORE_WEIGHTS["win_rate"] * win_rate +
                 SCORE_WEIGHTS["pnl_score"] * pnl)
        raw_scores.append(score)
        detailed_stats[k] = {
            "total": total, "win": stats[k]["win"], "pnl_sum": pnl,
            "score": round(score, 4), "win_rate": round(win_rate, 4)
        }

    smoothed = softmax(raw_scores)
    clipped = [max(MIN_WEIGHT, min(MAX_WEIGHT, s)) for s in smoothed]
    total_clip = sum(clipped)
    normalized = [round(c / total_clip, 4) for c in clipped]
    new_weights = dict(zip(keys, normalized))

    # === 載入原始設定 ===
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)
    strategy_config = config.get("STRATEGIES", {})

    # === 實際更新權重（保留無紀錄策略的原始或 fallback）===
    result_log = {"date": now.strftime("%Y-%m-%d"), "weights": {}}
    for k in strategy_config.keys():
        old = strategy_config[k].get("weight", 0.1)
        new = new_weights.get(k, FALLBACK_WEIGHT)
        strategy_config[k]["weight"] = new
        result_log["weights"][k] = {"old": old, "new": new}

    config["STRATEGIES"] = strategy_config
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    # === 寫入調整日誌 ===
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, "r") as f:
            old_log = json.load(f)
    else:
        old_log = []
    old_log.append(result_log)
    with open(LOG_PATH, "w") as f:
        json.dump(old_log[-20:], f, indent=2, ensure_ascii=False)

    # === 寫入當日詳細績效統計 ===
    os.makedirs(STAT_DIR, exist_ok=True)
    with open(os.path.join(STAT_DIR, f"{now.strftime('%Y-%m-%d')}.json"), "w") as f:
        json.dump(detailed_stats, f, indent=2, ensure_ascii=False)

    print("✅ 策略權重已更新")
    for k, w in new_weights.items():
        print(f"  - {k}：{w:.4f}")

# === CLI 測試 ===
if __name__ == "__main__":
    adjust_strategy_weights()