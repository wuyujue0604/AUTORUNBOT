# tools/performance_report.py
# 匯總每日 / 每週 / 每月績效報表

import os
import sys
import json
from datetime import datetime, timedelta
from collections import defaultdict

# === 修正路徑 ===
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# === 檔案路徑 ===
NOTIFY_PATH = os.path.join(PROJECT_ROOT, "output/order_notifications.json")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output/performance")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# === 勝負判定 ===
WIN_REASONS = {"TP", "RETRACE"}
LOSS_REASONS = {"SL", "FORCE_LOSS"}

# === 轉換時間為區間 key ===
def get_date_key(dt: datetime):
    return dt.strftime("%Y-%m-%d")

def get_week_key(dt: datetime):
    return f"{dt.year}-W{dt.isocalendar().week}"

def get_month_key(dt: datetime):
    return dt.strftime("%Y-%m")

# === 匯總函式 ===
def summarize(records, key_func):
    summary = defaultdict(lambda: {"total": 0, "wins": 0, "losses": 0, "pnl": 0})
    for r in records:
        if r.get("action") != "close" or "reason" not in r:
            continue
        try:
            ts = datetime.fromisoformat(r["timestamp"])
        except:
            continue

        key = key_func(ts)
        summary[key]["total"] += 1
        reason = r["reason"]
        if reason in WIN_REASONS:
            summary[key]["wins"] += 1
            summary[key]["pnl"] += 1
        elif reason in LOSS_REASONS:
            summary[key]["losses"] += 1
            summary[key]["pnl"] -= 1

    # 補上勝率
    result = {}
    for k, v in summary.items():
        total = v["total"]
        win_rate = round(v["wins"] / total, 2) if total else 0
        result[k] = {
            "total_trades": total,
            "win_rate": win_rate,
            "net_pnl_score": v["pnl"],
            "wins": v["wins"],
            "losses": v["losses"]
        }
    return result

# === 主函式 ===
def generate_reports():
    if not os.path.exists(NOTIFY_PATH):
        print("❌ 找不到通知檔")
        return

    with open(NOTIFY_PATH, "r") as f:
        records = json.load(f)

    daily = summarize(records, get_date_key)
    weekly = summarize(records, get_week_key)
    monthly = summarize(records, get_month_key)

    with open(os.path.join(OUTPUT_DIR, "daily_summary.json"), "w") as f:
        json.dump(daily, f, indent=2, ensure_ascii=False)

    with open(os.path.join(OUTPUT_DIR, "weekly_summary.json"), "w") as f:
        json.dump(weekly, f, indent=2, ensure_ascii=False)

    with open(os.path.join(OUTPUT_DIR, "monthly_summary.json"), "w") as f:
        json.dump(monthly, f, indent=2, ensure_ascii=False)

    print("✅ 績效報告已生成")

# === 測試執行點 ===
if __name__ == "__main__":
    generate_reports()