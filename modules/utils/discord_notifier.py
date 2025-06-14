# modules/utils/discord_notifier.py
# 通知模組：同時發送 Discord 並寫入通知檔，支援分級與風險標記

import os
import sys
import json
import requests
from datetime import datetime
from dotenv import load_dotenv

# === 設定根目錄與路徑 ===
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

NOTIFY_PATH = os.path.join(PROJECT_ROOT, "output/order_notifications.json")
MAX_ENTRIES = 200

# === 載入 .env 取得 Webhook ===
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# === 主通知函式 ===
def send_discord_notification(data: dict):
    """
    發送 Discord 通知，並寫入通知記錄檔（支援自動補齊欄位）
    必要欄位：symbol, action，可選：level, risk_level, reason, result, message...
    """
    # === 補齊欄位（預設）===
    data["timestamp"] = data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    data["level"] = data.get("level", "info")               # 通知層級
    data["risk_level"] = data.get("risk_level", "medium")   # 風險等級

    # === 寫入通知紀錄檔 ===
    os.makedirs(os.path.dirname(NOTIFY_PATH), exist_ok=True)
    history = []
    if os.path.exists(NOTIFY_PATH):
        try:
            with open(NOTIFY_PATH, "r") as f:
                history = json.load(f)
        except Exception:
            history = []

    history.append(data)
    history = history[-MAX_ENTRIES:]  # 最多保留 200 筆
    with open(NOTIFY_PATH, "w") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

    # === 組裝 Discord Embed 訊息 ===
    symbol = data.get("symbol", "")
    action = data.get("action", "").upper()
    level = data.get("level", "info")
    risk = data.get("risk_level", "medium")
    reason = data.get("reason", "")
    result = data.get("result", "")
    message = data.get("message", "")
    strategy = data.get("strategy_key", "")
    size = data.get("size", "")
    price = data.get("price", "")

    emojis = {"info": "ℹ️", "warn": "⚠️", "alert": "🚨"}
    colors = {"info": 3447003, "warn": 15105570, "alert": 15158332}
    emoji = emojis.get(level, "ℹ️")
    color = colors.get(level, 3447003)

    title = f"{emoji} [{level.upper()}] {symbol} {action}"
    description = (
        f"> 🧭 操作：{action}\n"
        f"> 📌 時間：{data['timestamp']}\n"
        f"> 🧮 風險等級：{risk}\n"
        f"> 🎯 策略：{strategy}\n"
        f"> 📈 數量：{size}，價格：{price}\n"
        f"> 🛠 原因：{reason or message}\n"
        f"> ✅ 結果：{result}\n"
    )

    payload = {
        "embeds": [{
            "title": title,
            "description": description,
            "color": color
        }]
    }

    # === 傳送至 Discord ===
    try:
        if WEBHOOK_URL:
            requests.post(WEBHOOK_URL, data=json.dumps(payload), headers={"Content-Type": "application/json"})
    except Exception as e:
        print(f"❗ Discord 傳送失敗：{e}")