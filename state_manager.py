import os
import json
import threading
import time
import traceback
from config import get_runtime_config, debug_mode
from logger import log

# 全域鎖，確保多執行緒時讀寫持倉安全
lock = threading.Lock()

# 系統配置動態讀取
def _get_config():
    return get_runtime_config()

# 持倉狀態檔案路徑（動態讀取，提升彈性）
def _get_position_state_path():
    config = _get_config()
    return config.get("POSITION_STATE_PATH", "json_results/position_status.json")

# 交易紀錄檔案路徑（動態讀取）
def _get_trade_log_path():
    config = _get_config()
    return config.get("TRADE_LOG_PATH", "json_results/trade_logs.jsonl")

# 保留獲利檔案路徑（動態讀取）
def _get_profit_path():
    config = _get_config()
    return config.get("PROFIT_PATH", "json_results/profit_reserved.json")

# --- 初始化資料夾(啟動時呼叫一次即可) ---
def init_data_dirs():
    for path in [_get_position_state_path(), _get_trade_log_path(), _get_profit_path()]:
        dirpath = os.path.dirname(path)
        if not os.path.exists(dirpath):
            os.makedirs(dirpath)
            if debug_mode():
                log(f"[DEBUG] 建立資料夾: {dirpath}", level="DEBUG")

# --- 讀取所有持倉狀態，加入快取機制降低I/O ---
_position_cache = None
_position_cache_time = 0
def load_position_state(force_reload=False):
    global _position_cache, _position_cache_time
    path = _get_position_state_path()
    now = time.time()
    # 5秒快取有效期
    if not force_reload and _position_cache is not None and (now - _position_cache_time) < 5:
        return _position_cache

    # 確保資料夾存在
    dirpath = os.path.dirname(path)
    if not os.path.exists(dirpath):
        os.makedirs(dirpath)
        if debug_mode():
            log(f"[DEBUG] 建立資料夾: {dirpath}", level="DEBUG")

    # 如果檔案不存在，寫入空dict並回傳
    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({}, f)
            _position_cache = {}
            _position_cache_time = now
            return {}
        except Exception as e:
            log(f"[錯誤] 建立空持倉檔失敗: {e}\n{traceback.format_exc()}", level="ERROR")
            return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                _position_cache = {}
                _position_cache_time = now
                return {}
            data = json.loads(content)
            if not isinstance(data, dict):
                log(f"[錯誤] 持倉檔格式錯誤，非 dict，重置為空 dict", level="ERROR")
                with open(path, "w", encoding="utf-8") as fw:
                    json.dump({}, fw)
                _position_cache = {}
                _position_cache_time = now
                return {}
            _position_cache = data
            _position_cache_time = now
            return data
    except Exception as e:
        log(f"[錯誤] 讀取持倉檔失敗: {e}\n{traceback.format_exc()}", level="ERROR")
        return {}

# --- 取得指定持倉資訊 ---
def get_position_state(symbol):
    positions = load_position_state()
    return positions.get(symbol)

# --- 更新或新增持倉資訊 ---
def update_position_state(symbol, direction, contracts, price, confidence, extra=None, add=False):
    with lock:
        positions = load_position_state()
        if symbol not in positions:
            positions[symbol] = {
                "direction": direction,
                "contracts": contracts,
                "price": price,
                "confidence": confidence
            }
        else:
            if add:
                positions[symbol]["contracts"] += contracts
            else:
                positions[symbol]["contracts"] = contracts
            positions[symbol]["price"] = price
            positions[symbol]["confidence"] = confidence

        if extra:
            positions[symbol].update(extra)

        _save_position_state(positions)
        if debug_mode():
            log(f"[DEBUG] 更新持倉: {symbol} 張數={positions[symbol]['contracts']}", level="DEBUG")

# --- 減倉後更新持倉數量和減倉次數 ---
def update_position_after_reduce(symbol, reduced_contracts, new_reduce_times=None):
    with lock:
        positions = load_position_state()
        if symbol in positions:
            positions[symbol]["contracts"] -= reduced_contracts
            if new_reduce_times is not None:
                positions[symbol]["reduce_times"] = new_reduce_times
            if positions[symbol]["contracts"] <= 0:
                del positions[symbol]
        _save_position_state(positions)
        if debug_mode():
            log(f"[DEBUG] 減倉後更新持倉: {symbol} 剩餘張數={positions.get(symbol, {}).get('contracts', 0)}", level="DEBUG")

# --- 移除指定持倉 ---
def remove_position(symbol):
    with lock:
        positions = load_position_state()
        if symbol in positions:
            del positions[symbol]
        _save_position_state(positions)
        if debug_mode():
            log(f"[DEBUG] 移除持倉: {symbol}", level="DEBUG")

# --- 私有函式：寫入持倉狀態到檔案 ---
def _save_position_state(positions):
    path = _get_position_state_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(positions, f, indent=2)
        if debug_mode():
            log(f"[DEBUG] 寫入持倉成功，共 {len(positions)} 檔", level="DEBUG")
    except Exception as e:
        log(f"[錯誤] 寫入持倉失敗: {e}\n{traceback.format_exc()}", level="ERROR")

# --- 寫入交易紀錄（jsonl格式） ---
def record_trade_log(data):
    """
    將交易紀錄追加到trade_logs.jsonl檔案。
    會補足時間戳欄位，並確保資料夾存在。
    """
    path = _get_trade_log_path()
    dirpath = os.path.dirname(path)
    if not os.path.exists(dirpath):
        os.makedirs(dirpath)
        if debug_mode():
            log(f"[DEBUG] 建立資料夾: {dirpath}", level="DEBUG")

    if "timestamp" not in data:
        data["timestamp"] = int(time.time())
    if "log_timestamp" not in data:
        data["log_timestamp"] = int(time.time())

    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
        if debug_mode():
            log(f"[記錄成功] 寫入交易紀錄: {data}", level="DEBUG")
    except Exception as e:
        log(f"[錯誤] 寫入交易紀錄失敗: {e}\n{traceback.format_exc()}", level="ERROR")

# --- 累加保留獲利 ---
def add_profit(amount):
    path = _get_profit_path()
    dirpath = os.path.dirname(path)
    if not os.path.exists(dirpath):
        os.makedirs(dirpath)
        if debug_mode():
            log(f"[DEBUG] 建立資料夾: {dirpath}", level="DEBUG")

    data = {"reserved": 0}
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
                if isinstance(d, dict) and "reserved" in d:
                    data = d
        data["reserved"] += amount
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        if debug_mode():
            log(f"[DEBUG] 累加保留獲利: +{amount}，總計: {data['reserved']}", level="DEBUG")
    except Exception as e:
        log(f"[錯誤] 儲存保留獲利失敗: {e}\n{traceback.format_exc()}", level="ERROR")

# --- 取得保留獲利 ---
def get_reserved_profit():
    path = _get_profit_path()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
                if isinstance(d, dict):
                    return d.get("reserved", 0)
    except Exception as e:
        log(f"[錯誤] 查詢保留獲利失敗: {e}\n{traceback.format_exc()}", level="ERROR")
    return 0

# --- 重置保留獲利 ---
def reset_reserved_profit():
    path = _get_profit_path()
    try:
        dirpath = os.path.dirname(path)
        if not os.path.exists(dirpath):
            os.makedirs(dirpath)
            if debug_mode():
                log(f"[DEBUG] 建立資料夾: {dirpath}", level="DEBUG")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"reserved": 0}, f)
        if debug_mode():
            log(f"[DEBUG] 已重置保留獲利為 0", level="DEBUG")
    except Exception as e:
        log(f"[錯誤] 重置保留獲利失敗: {e}\n{traceback.format_exc()}", level="ERROR")

# --- 啟動時初始化所需資料夾 ---
init_data_dirs()