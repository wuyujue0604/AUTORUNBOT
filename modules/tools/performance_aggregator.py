# tools/performance_aggregator.py
# 綜合版：指標級強化 + 策略級強化 + 清除舊檔案

import os
import sys

# 🔧 加入根目錄，修正模組導入錯誤
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
import json
from datetime import datetime, timedelta
from collections import defaultdict

# === 檔案路徑 ===
CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../config/strategy_config.json"))
LOG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../logs/trade_results.json"))
OUTPUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../output"))

# === 輔助：讀取交易紀錄 ===
def load_trade_logs():
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH, "r") as f:
        return json.load(f)

# === 輔助：平滑調整（滑動平均）===
def smooth_adjust(old_w, new_contrib, lr=0.1):
    return round(old_w * (1 - lr) + new_contrib * lr, 4)

# === 分析整體 PnL 表現（策略級強化）===
def analyze_strategy_pnl(trades):
    perf = defaultdict(lambda: {"pnl_total": 0, "count": 0})
    for t in trades:
        strategy = t.get("strategy")
        pnl = t.get("pnl", 0)
        perf[strategy]["pnl_total"] += pnl
        perf[strategy]["count"] += 1
    avg_pnl = {}
    for s in perf:
        if perf[s]["count"] > 0:
            avg_pnl[s] = perf[s]["pnl_total"] / perf[s]["count"]
    return avg_pnl

# === 綜合強化邏輯 ===
def reinforce_config():
    logs = load_trade_logs()
    if not logs:
        print("❌ 無交易紀錄可分析")
        return

    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)

    backup_path = CONFIG_PATH.replace(".json", "_backup.json")
    os.rename(CONFIG_PATH, backup_path)

    updated = False
    strategy_pnls = analyze_strategy_pnl(logs)

    # 根據每個策略強化其指標
    grouped = defaultdict(list)
    for row in logs:
        grouped[row["strategy"]].append(row)

    for strategy, records in grouped.items():
        if strategy not in config["STRATEGIES"]:
            continue

        strategy_conf = config["STRATEGIES"][strategy]
        weights = strategy_conf["weights"]
        indicators = weights.keys()

        # 1️⃣ ▶ 指標級強化（只針對 TP 的單）
        score_sum = {ind: 0 for ind in indicators}
        count_tp = 0

        for r in records:
            if r.get("result") == "TP":
                for ind in indicators:
                    score_sum[ind] += r["indicators"].get(ind, 0)
                count_tp += 1

        if count_tp > 0:
            print(f"\n🔧 指標級強化：{strategy}（TP 筆數: {count_tp}）")
            for ind in indicators:
                avg_contrib = score_sum[ind] / count_tp
                old_w = weights[ind]
                new_w = smooth_adjust(old_w, avg_contrib)
                weights[ind] = new_w
                print(f" - {ind}: {old_w:.4f} → {new_w:.4f}")
                updated = True

        # 2️⃣ ▶ 策略級微調（根據 PnL 整體調整強度）
        if strategy in strategy_pnls:
            avg_pnl = strategy_pnls[strategy]
            factor = 1.1 if avg_pnl > 0 else 0.9
            weights = {
                k: round(v * factor, 4)
                for k, v in weights.items()
            }

            # Normalize 權重
            total = sum(weights.values())
            if total > 0:
                weights = {
                    k: round(v / total, 4)
                    for k, v in weights.items()
                }

            config["STRATEGIES"][strategy]["weights"] = weights
            print(f"📈 策略級強化：{strategy}，Avg PnL={avg_pnl:.2f}，調整比例: {factor}")
            updated = True

    if updated:
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print("\n✅ 策略設定已更新，原檔備份為：", backup_path)
    else:
        print("⚠️ 無可更新項目，策略維持不變。")

# === 清除舊檔案 ===
def cleanup_old_outputs(days_to_keep=7):
    now = datetime.now()
    deleted = 0
    for fname in os.listdir(OUTPUT_DIR):
        if fname.startswith("selected_coins_") and fname.endswith(".json"):
            try:
                ts_str = fname.replace("selected_coins_", "").replace(".json", "")
                file_time = datetime.strptime(ts_str, "%Y%m%d_%H%M")
                if (now - file_time).days > days_to_keep:
                    os.remove(os.path.join(OUTPUT_DIR, fname))
                    deleted += 1
            except:
                continue
    print(f"\n🧹 清理完成，已刪除 {deleted} 筆過期選幣資料")

# === 主流程 ===
if __name__ == "__main__":
    print("🔍 執行策略強化與歷史清理中...")
    reinforce_config()
    cleanup_old_outputs()