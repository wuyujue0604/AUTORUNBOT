# main_runner.py
# 主排程控制器：選幣、持倉監控、策略強化、自動建倉、自動重啟、每日績效報表

import os
import sys
import time
import json
import math
import schedule
import threading
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

SELECTOR_PATH = os.path.join(PROJECT_ROOT, "modules/selector/auto_selector.py")
MONITOR_PATH = os.path.join(PROJECT_ROOT, "modules/monitor/position_monitor.py")
NOTIFY_PATH = os.path.join(PROJECT_ROOT, "output/order_notifications.json")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config/strategy_config.json")
LOG_PATH = os.path.join(PROJECT_ROOT, "output/weight_adjust_log.json")
REPORT_FOLDER = os.path.join(PROJECT_ROOT, "output/strategy_stats")
SYMBOL_PATH = os.path.join(PROJECT_ROOT, "output/selected_symbols.json")

from modules.trader.long_trader import run_long_trader
from modules.trader.short_trader import open_short_position
from config.config import get_runtime_config

# === Softmax 函式（用於策略強化）===
def softmax(scores):
    exp_scores = [math.exp(s) for s in scores]
    total = sum(exp_scores)
    return [s / total for s in exp_scores]

# === 建倉執行（多 + 空）===
def run_all_traders():
    run_long_trader()  # 多單建倉

    # 讀取選幣結果
    if not os.path.exists(SYMBOL_PATH):
        print("⚠️ 找不到選幣結果")
        return

    with open(SYMBOL_PATH, "r") as f:
        data = json.load(f)

    cfg = get_runtime_config()
    short_targets = [d for d in data if d.get("direction") == "short"]
    long_targets = [d for d in data if d.get("direction") == "long"]

    short_count = len(short_targets)
    long_count = len(long_targets)

    for item in short_targets:
        symbol = item.get("symbol")
        score = item.get("score")
        strategy_key = item.get("strategy_key")
        print(f"➡️ 建立空單：{symbol}, 策略：{strategy_key}")
        open_short_position(symbol, score, strategy_key, cfg, short_count, long_count)

# === 選幣 + 建倉任務 ===
def run_selector_and_trade():
    start = time.time()
    print(f"\n===== 🚀 選幣任務開始 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] =====")
    try:
        os.system(f"python3 {SELECTOR_PATH}")
        print("✅ 選幣完成，執行建倉中...")
        run_all_traders()
    except Exception as e:
        print(f"❌ 選幣任務錯誤: {e}")
    print(f"===== ✅ 任務完成，用時 {round(time.time() - start, 2)} 秒 =====\n")

# === 策略權重強化 ===
def run_optimizer():
    print(f"\n===== 🎯 策略權重強化開始 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] =====")
    DAYS_BACK = 3
    MIN_WEIGHT = 0.05
    MAX_WEIGHT = 0.5

    if not os.path.exists(NOTIFY_PATH) or not os.path.exists(CONFIG_PATH):
        print("❌ 找不到必要檔案")
        return

    with open(NOTIFY_PATH, "r") as f:
        records = json.load(f)

    now = datetime.now()
    cutoff = now - timedelta(days=DAYS_BACK)
    stats = {}

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
        print("⚠️ 無可用策略績效")
        return

    keys = list(stats.keys())
    raw_scores = []
    for k in keys:
        total = stats[k]["total"]
        win_rate = stats[k]["win"] / total if total > 0 else 0
        pnl_sum = stats[k]["pnl_sum"]
        score = win_rate + pnl_sum * 0.5
        raw_scores.append(score)

    soft_scores = softmax(raw_scores)
    clipped = [max(MIN_WEIGHT, min(MAX_WEIGHT, s)) for s in soft_scores]
    total_clip = sum(clipped)
    normalized = [round(c / total_clip, 4) for c in clipped]
    new_weights = dict(zip(keys, normalized))

    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)

    for key, w in new_weights.items():
        if key in config.get("STRATEGIES", {}):
            config["STRATEGIES"][key]["weight"] = w

    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    Path(os.path.dirname(LOG_PATH)).mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps({
            "date": now.strftime("%Y-%m-%d"),
            "weights": new_weights,
            "stats": stats,
            "raw_scores": dict(zip(keys, raw_scores)),
            "softmax_scores": dict(zip(keys, soft_scores))
        }, ensure_ascii=False) + "\n")

    print("✅ 策略權重已更新")
    for k, w in new_weights.items():
        print(f"  - {k}：{w:.4f}")

# === 每日績效報表 ===
def generate_daily_report():
    now = datetime.now()
    target_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    output_path = os.path.join(REPORT_FOLDER, f"{target_date}.json")

    if not os.path.exists(NOTIFY_PATH):
        print("❌ 無法產生績效報表，找不到通知檔")
        return

    with open(NOTIFY_PATH, "r") as f:
        records = json.load(f)

    daily_records = [r for r in records if r.get("action") == "close" and r.get("timestamp", "").startswith(target_date)]
    if not daily_records:
        print(f"⚠️ 無平倉紀錄：{target_date}")
        return

    summary = {}
    for r in daily_records:
        key = r.get("strategy_key", "unknown")
        reason = r.get("reason")
        if key not in summary:
            summary[key] = {"TP": 0, "SL": 0, "RETRACE": 0, "FORCE_LOSS": 0, "total": 0, "win": 0, "pnl_sum": 0}

        summary[key]["total"] += 1
        if reason in ["TP", "RETRACE"]:
            summary[key]["win"] += 1
            summary[key]["pnl_sum"] += 1
        elif reason in ["SL", "FORCE_LOSS"]:
            summary[key]["pnl_sum"] -= 1

        if reason in summary[key]:
            summary[key][reason] += 1

    for key, val in summary.items():
        val["win_rate"] = round(val["win"] / val["total"], 4) if val["total"] > 0 else 0

    Path(REPORT_FOLDER).mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"📊 已儲存當日策略績效報表：{output_path}")

# === 持倉監控背景執行 ===
def start_monitor_thread():
    def monitor_loop():
        while True:
            try:
                os.system(f"python3 {MONITOR_PATH}")
            except Exception as e:
                print(f"⚠️ 持倉監控錯誤：{e}")
            print("🔁 重新啟動持倉監控中...")
            time.sleep(5)
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()

# === 每日自動重啟 ===
def restart_self():
    print(f"\n🔁 主排程重新啟動中 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
    python = sys.executable
    os.execv(python, [python] + sys.argv)

# === 排程設定 ===
schedule.every(1).minutes.do(run_selector_and_trade)        # 每分鐘選幣 + 建倉
schedule.every().day.at("00:00").do(run_optimizer)          # 每日權重強化
schedule.every().day.at("00:10").do(generate_daily_report)  # 每日績效報表
schedule.every().day.at("03:00").do(restart_self)           # 每日重新啟動

# === 啟動主排程 ===
print("🟢 主排程啟動中：整合 選幣 / 建倉 / 強化 / 監控 / 報表 / 重啟")
start_monitor_thread()

# === 排程迴圈 ===
while True:
    schedule.run_pending()
    time.sleep(1)