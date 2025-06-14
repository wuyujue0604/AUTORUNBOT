# modules/logger/trade_logger.py
# 記錄每次選幣與交易結果（供後續績效分析與強化學習使用）

import os
import json
from datetime import datetime
from config import get_runtime_config  # 動態載入設定

# === 取得 log 路徑（從 config 支援熱更新）===
def get_log_path() -> str:
    config = get_runtime_config()
    log_dir = config.get("LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, "trade_results.json")

def log_trade_result(entry: dict):
    """
    紀錄單筆交易結果，格式如下：
    {
        "symbol": "BTC-USDT",
        "strategy": "trend",
        "score": 4.72,
        "indicators": {...},
        "result": "TP" / "SL" / "MISS",
        "pnl": 0.018,
        "timestamp": "2025-06-13T20:14:01"
    }
    """
    entry["timestamp"] = datetime.now().isoformat()
    log_path = get_log_path()

    try:
        # 讀取既有紀錄
        if os.path.exists(log_path):
            with open(log_path, "r") as f:
                data = json.load(f)
        else:
            data = []

        data.append(entry)

        # 寫入更新後的 log 檔
        with open(log_path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    except Exception as e:
        print(f"⚠️ 無法寫入交易記錄：{e}")