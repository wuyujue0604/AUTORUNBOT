import os
import json
from okx_client import transfer_profit_to_funding
from logger import log
from config import get_runtime_config

def get_reserve_file_path():
    """
    從配置讀取保留獲利檔案路徑，預設 json_results/profit_reserve.json
    """
    config = get_runtime_config()
    return config.get("PROFIT_RESERVE_PATH", "json_results/profit_reserve.json")

def add_profit(amount):
    """
    累積保留獲利金額，amount 必須大於 0
    並寫入檔案，若設定 DEBUG_MODE 則輸出詳細日誌
    """
    if amount <= 0:
        return
    total = get_reserved_profit() + amount
    save_reserved_profit(total)
    if get_runtime_config().get("DEBUG_MODE", False):
        log(f"[DEBUG] 已新增獲利：{amount:.2f}，目前保留總額：{total:.2f}", level="DEBUG")

def get_reserved_profit():
    """
    讀取目前保留獲利金額，若檔案不存在或錯誤，回傳 0.0
    """
    path = get_reserve_file_path()
    if not os.path.exists(path):
        return 0.0
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return float(data.get("profit", 0.0))
    except Exception as e:
        log(f"[錯誤] 讀取保留獲利檔案失敗: {e}", level="ERROR")
        return 0.0

def save_reserved_profit(value):
    """
    將保留獲利金額寫入檔案，包含目錄確保與錯誤捕獲
    """
    path = get_reserve_file_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"profit": round(value, 2)}, f)
    except Exception as e:
        log(f"[錯誤] 儲存保留獲利失敗: {e}", level="ERROR")

def reset_reserved_profit():
    """
    重置保留獲利為 0
    """
    save_reserved_profit(0.0)

def process_profit_transfer():
    """
    判斷是否達到轉帳門檻，若達標則嘗試轉帳至 Funding 帳戶，
    成功後重置保留獲利，失敗則輸出警告並保持原狀。
    """
    config = get_runtime_config()
    threshold = float(config.get("MIN_PROFIT_TO_RESERVE", 5.0))
    reserve = get_reserved_profit()

    if reserve >= threshold:
        if transfer_profit_to_funding(amount=reserve):
            log(f"[INFO] 已將 {reserve:.2f} USDT 轉入 Funding 帳戶", level="INFO")
            reset_reserved_profit()
            return True
        else:
            log("[WARNING] 轉入 Funding 帳戶失敗", level="WARN")
    return False