import datetime
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def log(message, level="INFO"):
    """
    簡易日誌輸出，預設輸出至標準輸出。
    :param message: 日誌內容，可為任意型態，會自動轉字串。
    :param level: 日誌層級，預設 INFO。
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    level = str(level).upper()
    print(f"[{level}] {now} - {str(message)}")