import os
import json
import time

# === 🔄 熱更新設定 ===
_last_load_time = 0
_cached_config = {}

def _load_config_file():
    """
    從 config.json 讀取設定檔內容，若檔案不存在或解析失敗，回傳預設設定字典。
    """
    path = os.path.join(os.path.dirname(__file__), "config.json")
    if not os.path.exists(path):
        # 預設設定，必要時可擴充
        return {
            "DEBUG_MODE": True,
            "TEST_MODE": False,
            # 其他預設值可放這裡
        }
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[錯誤] 載入 config.json 失敗: {e}")
        return {}

def get_runtime_config():
    """
    取得系統執行時設定，5秒內快取結果以降低 I/O 頻率，實現熱更新。
    """
    global _last_load_time, _cached_config
    now = time.time()
    if now - _last_load_time > 5 or not _cached_config:
        _cached_config = _load_config_file()
        _last_load_time = now
    return _cached_config

def get(key, default=None):
    """
    直接取得指定設定值，找不到時回傳預設值。
    """
    config = get_runtime_config()
    return config.get(key, default)

def debug_mode():
    """
    取得是否為 DEBUG 模式。
    """
    return bool(get("DEBUG_MODE", True))

def test_mode():
    """
    取得是否為 TEST 模式。
    """
    return bool(get("TEST_MODE", False))

# === 專用參數取得函式 ===
def get_open_threshold():
    return float(get("OPEN_THRESHOLD", 3.0))

def get_close_threshold():
    return float(get("CLOSE_THRESHOLD", 2.5))

def require_profit_to_close():
    return bool(get("REQUIRE_PROFIT_TO_CLOSE", True))

def get_max_add_times():
    return int(get("MAX_ADD_TIMES", 3))

def get_max_reduce_times():
    return int(get("MAX_REDUCE_TIMES", 2))

def get_take_profit_value():
    return float(get("TAKE_PROFIT_VALUE", 0.2))

def get_stop_loss_ratio():
    return float(get("STOP_LOSS_RATIO", -0.05))

def get_max_single_position_ratio():
    return float(get("MAX_SINGLE_POSITION_RATIO", 0.075))

def get_min_single_position_ratio():
    return float(get("MIN_SINGLE_POSITION_RATIO", 0.01))

def get_capital_buffer_ratio():
    return float(get("CAPITAL_BUFFER_RATIO", 0.10))

def get_order_margin_buffer():
    return float(get("ORDER_MARGIN_BUFFER", 1.10))

def get_max_holding_symbols():
    return int(get("MAX_HOLDING_SYMBOLS", 6))

def get_max_symbol_exposure_ratio():
    return float(get("MAX_SYMBOL_EXPOSURE_RATIO", 0.5))

def get_reserve_profit_ratio():
    return float(get("RESERVE_PROFIT_RATIO", 0.5))

def get_min_profit_to_reserve():
    return float(get("MIN_PROFIT_TO_RESERVE", 5.0))

def get_position_cooldown_after_fail():
    return int(get("POSITION_COOLDOWN_AFTER_FAIL", 600))

def get_cooldown_duration():
    return int(get("COOLDOWN_DURATION", 3600))

def get_cooldown_after_loss():
    return int(get("COOLDOWN_AFTER_LOSS", 1800))

def get_min_win_rate():
    return float(get("MIN_WIN_RATE", 0.6))

def get_min_avg_profit():
    return float(get("MIN_AVG_PROFIT", 0.01))

def get_min_occurrences():
    return int(get("MIN_OCCURRENCES", 10))

def get_min_vol_std():
    return float(get("MIN_VOL_STD", 1))

def get_min_candle_amplitude():
    return float(get("MIN_CANDLE_AMPLITUDE", 0.01))

def get_min_24h_volume_usdt():
    return float(get("MIN_24H_VOLUME_USDT", 200000000))

def get_blocked_symbols():
    return get("BLOCKED_SYMBOLS", [])

def get_disabled_indicators():
    return get("DISABLED_INDICATORS", [])

def get_main_loop_interval():
    return int(get("MAIN_LOOP_INTERVAL", 45))

def get_max_retry_on_failure():
    return int(get("MAX_RETRY_ON_FAILURE", 3))

def get_max_leverage_limit():
    return int(get("MAX_LEVERAGE_LIMIT", 10))

def get_trade_log_path():
    return get("TRADE_LOG_PATH", "json_results/trade_logs.jsonl")

def get_position_state_path():
    return get("POSITION_STATE_PATH", "json_results/position_status.json")

def get_combination_log_path():
    return get("COMBINATION_LOG_PATH", "indicator_combination_log.json")

def get_performance_log_path():
    return get("PERFORMANCE_LOG_PATH", "json_results/performance_logs.json")

def get_profit_reserve_path():
    return get("PROFIT_RESERVE_PATH", "json_results/profit_reserve.json")

def get_max_contracts_per_order():
    return int(get("MAX_CONTRACTS_PER_ORDER", 6000))

def get_tf_weight_1h():
    return float(get("TF_WEIGHT_1H", 0.7))

def get_tf_weight_15m():
    return float(get("TF_WEIGHT_15M", 0.3))

def get_selector_loop_interval():
    return int(get("SELECTOR_LOOP_INTERVAL", 45))

def get_position_monitor_loop_interval():
    return int(get("POSITION_MONITOR_LOOP_INTERVAL", 15))

# === 信心分數相關設定 ===
def get_confidence_boost_ratio():
    return float(get("CONFIDENCE_BOOST_RATIO", 1.05))

def get_confidence_decay_ratio():
    return float(get("CONFIDENCE_DECAY_RATIO", 0.90))

def get_confidence_weight():
    return float(get("CONFIDENCE_WEIGHT", 0.5))

def get_max_confidence_score():
    return float(get("MAX_CONFIDENCE_SCORE", 5.0))

def get_min_confidence_score():
    return float(get("MIN_CONFIDENCE_SCORE", 0.0))

# === 其他自訂函式可繼續擴充 ===
