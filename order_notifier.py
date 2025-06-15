import os
import json
import requests
import threading
import time
from datetime import datetime
from dotenv import load_dotenv
from config import get_runtime_config, debug_mode
from logger import log  # 改用統一log系統

load_dotenv()
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# 通知佇列與鎖，避免多執行緒衝突
notification_queue = []
queue_lock = threading.Lock()

def get_interval():
    config = get_runtime_config()
    return int(config.get("MAIN_LOOP_INTERVAL", 30))

def get_max_queue_size():
    config = get_runtime_config()
    return int(config.get("NOTIFICATION_QUEUE_MAX_SIZE", 100))

def queue_trade(log_data):
    """
    加入交易通知佇列，超過最大長度時丟棄最舊訊息。
    """
    with queue_lock:
        max_size = get_max_queue_size()
        if len(notification_queue) >= max_size:
            removed = notification_queue.pop(0)
            log(f"[通知佇列] 佇列已滿，丟棄最舊訊息: {removed.get('symbol', '?')}")
        notification_queue.append(log_data)

def format_trade_message_embed(data):
    """
    使用Discord Embed格式建立交易訊息，讓訊息更美觀。
    """
    ts = datetime.fromtimestamp(data.get("timestamp", 0)).strftime("%Y-%m-%d %H:%M:%S")
    symbol = data.get("symbol", "?")
    op = data.get("operation", "?")
    qty = data.get("contracts", "?")
    px = data.get("price", "?")
    conf = data.get("confidence", "?")
    pnl = data.get("pnl", None)
    pnl_str = f"{pnl:.4f}" if pnl is not None else "N/A"
    color = 0x00FF00 if (pnl is not None and pnl > 0) else 0xFF0000

    embed = {
        "title": f"{symbol} {op} 通知",
        "color": color,
        "fields": [
            {"name": "時間", "value": ts, "inline": True},
            {"name": "張數", "value": str(qty), "inline": True},
            {"name": "價格", "value": str(px), "inline": True},
            {"name": "信心", "value": str(conf), "inline": True},
            {"name": "損益", "value": pnl_str, "inline": True}
        ]
    }
    return embed

def should_send_now(last_send_info):
    """
    根據配置靈活判斷是否該發送通知。
    支援配置化設定：
    - 週一至週五特定時段整點發送
    - 其他時間每15分鐘發送
    - 凌晨固定點發送等
    """
    config = get_runtime_config()
    now = datetime.now()
    weekday = now.weekday()
    hour = now.hour
    minute = now.minute

    # 讀取配置，若未設定則使用預設
    workdays = config.get("NOTIFY_WORKDAYS", [0,1,2,3,4])  # 週一~週五
    work_hours_start = config.get("NOTIFY_WORKHOUR_START", 9)
    work_hours_end = config.get("NOTIFY_WORKHOUR_END", 18)
    night_hours = config.get("NOTIFY_NIGHT_HOURS", list(range(0,7)))  # 0~6點

    # 凌晨固定點
    if hour in night_hours:
        if minute == 0 and last_send_info.get("hour") != hour:
            return True
        return False

    # 工作日整點通知
    if weekday in workdays and work_hours_start <= hour <= work_hours_end:
        if minute == 0 and last_send_info.get("hour") != hour:
            return True
        return False

    # 其他時間15分鐘通知
    if minute % 15 == 0:
        quarter = minute // 15
        if last_send_info.get("quarter") != (hour, quarter):
            return True

    return False

def send_notification(embeds):
    """
    使用Discord Webhook發送Embed格式訊息，支持批次多筆通知。
    """
    if not WEBHOOK_URL:
        log("[通知] 未設定 Discord Webhook URL，無法發送")
        return
    payload = {"embeds": embeds}
    try:
        resp = requests.post(WEBHOOK_URL, json=payload, headers={"Content-Type": "application/json"})
        if resp.status_code != 204:
            log(f"[通知] 發送失敗，HTTP狀態碼: {resp.status_code}，回應: {resp.text}")
        elif debug_mode():
            log("[通知] Discord Webhook 發送成功")
    except Exception as e:
        log(f"[通知錯誤] 發送失敗: {e}")

def flush_notifications(last_send_info):
    """
    判斷是否該發送通知，符合條件就批次發送，然後清空佇列。
    """
    with queue_lock:
        if not notification_queue:
            return False
        if not should_send_now(last_send_info):
            return False
        embeds = [format_trade_message_embed(t) for t in notification_queue]
        send_notification(embeds)
        notification_queue.clear()
        return True

def notification_loop():
    """
    背景執行緒，定時檢查是否應發送通知。
    """
    last_send_info = {"date": None, "hour": None, "quarter": None}
    while True:
        sent = flush_notifications(last_send_info)
        if sent:
            now = datetime.now()
            last_send_info["date"] = now.date()
            last_send_info["hour"] = now.hour
            last_send_info["quarter"] = now.minute // 15
        time.sleep(get_interval())

def start_notification_thread():
    """
    啟動背景執行緒持續執行通知排程。
    """
    t = threading.Thread(target=notification_loop, daemon=True)
    t.start()