import os
import json
import threading
from datetime import datetime
from config import get_runtime_config
from logger import log

# 設定結果儲存目錄及建立
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
RESULT_DIR = os.path.join(BASE_DIR, "json_results")
os.makedirs(BASE_DIR, exist_ok=True)

# 多線程鎖，確保檔案存取安全
_log_lock = threading.Lock()

performance_log_path = "json_results/performance_logs.jsonl"
performance_lock = threading.Lock()

def record_performance(trade_log: dict):
    try:
        with performance_lock:
            with open(performance_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(trade_log, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[績效紀錄錯誤] {e}")

def log_combination_result(result: dict) -> bool:
    """
    紀錄每次選中的幣種與對應的指標組合，用於績效分析與學習。
    1. 防呆：只允許 list 格式，若檔案不存在或格式錯誤自動重置為空。
    2. 多線程鎖定，避免同時寫入錯亂。
    3. 動態從配置讀取儲存路徑與最大紀錄數。
    :param result: dict，包含 symbol, direction, confidence, indicators, timestamp 等欄位。
    :return: bool，是否成功寫入。
    """
    config = get_runtime_config()
    log_file = os.path.join(RESULT_DIR, config.get("COMBINATION_LOG_PATH", "indicator_combination_log.json"))

    entry = {
        "symbol": result.get("symbol"),
        "direction": result.get("direction"),
        "confidence": result.get("confidence"),
        "indicators": result.get("indicators", {}),
        "timestamp": result.get("timestamp"),
        "log_timestamp": int(datetime.now().timestamp())
    }

    try:
        with _log_lock:
            if os.path.exists(log_file):
                with open(log_file, "r", encoding="utf-8") as f:
                    try:
                        data = json.load(f)
                        if not isinstance(data, list):
                            log(f"[警告] 指標組合紀錄檔非 list，將被重置", level="WARN")
                            data = []
                    except Exception as e:
                        log(f"[錯誤] 解析指標組合紀錄失敗: {e}", level="ERROR")
                        data = []
            else:
                data = []

            max_records = config.get("MAX_COMBINATION_LOGS", 5000)
            data.append(entry)
            if len(data) > max_records:
                data = data[-max_records:]  # 只保留最新 N 筆

            with open(log_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            log(f"[INFO] 紀錄指標組合：{entry['symbol']} (信心: {entry['confidence']})", level="INFO")
            return True
    except Exception as e:
        log(f"[錯誤] 寫入指標組合紀錄失敗: {e}", level="ERROR")
        return False