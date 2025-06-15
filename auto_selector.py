import os
import json
import time
import threading
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from config import get_runtime_config, debug_mode, test_mode
from selector_utils import (
    get_all_usdt_swap_symbols,
    get_ohlcv_batch,
    pass_pre_filter,
    is_symbol_blocked,
    load_symbol_locks,
    filter_candidates_by_position
)
from indicator_calculator import calculate_indicators
from combination_logger import log_combination_result
from state_manager import load_position_state
import okx_client
from logger import log

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
RESULT_DIR = os.path.join(BASE_DIR, "json_results")
os.makedirs(RESULT_DIR, exist_ok=True)

HISTORY_DB_PATH = os.path.join(RESULT_DIR, "selection_history.db")
LATEST_SELECTION_DB_PATH = os.path.join(RESULT_DIR, "latest_selection.db")

_db_lock = threading.Lock()

def get_db_connection(db_path):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_history_db():
    with _db_lock:
        with get_db_connection(HISTORY_DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS selection_history (
                    symbol TEXT PRIMARY KEY,
                    consecutive INTEGER DEFAULT 0,
                    confidence REAL DEFAULT 0,
                    win_rate REAL DEFAULT 0.5,
                    dynamic_weight TEXT DEFAULT '{}',
                    last_seen INTEGER DEFAULT 0
                )
            ''')
            conn.commit()

def init_latest_selection_db():
    with _db_lock:
        with get_db_connection(LATEST_SELECTION_DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS latest_selection (
                    symbol TEXT PRIMARY KEY,
                    direction TEXT,
                    confidence REAL,
                    operation TEXT,
                    indicators TEXT,
                    indicator_status TEXT,
                    timestamp INTEGER
                )
            ''')
            conn.commit()

def load_selection_history_cached():
    with _db_lock:
        try:
            with get_db_connection(HISTORY_DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT symbol, consecutive, confidence, win_rate, dynamic_weight, last_seen FROM selection_history")
                rows = cursor.fetchall()
        except Exception as e:
            log(f"[錯誤] 讀取歷史資料庫失敗: {e}", level="ERROR")
            return {}

    history = {}
    for symbol, consecutive, confidence, win_rate, dw_json, last_seen in rows:
        try:
            dw = json.loads(dw_json)
        except Exception:
            dw = {}
        history[symbol] = {
            "consecutive": consecutive,
            "confidence": confidence,
            "win_rate": win_rate,
            "dynamic_weight": dw,
            "last_seen": last_seen
        }
    return history

def save_selection_history_atomic(history):
    with _db_lock:
        try:
            with get_db_connection(HISTORY_DB_PATH) as conn:
                cursor = conn.cursor()
                for symbol, record in history.items():
                    dw_json = json.dumps(record.get("dynamic_weight", {}))
                    cursor.execute('''
                        INSERT INTO selection_history (symbol, consecutive, confidence, win_rate, dynamic_weight, last_seen)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(symbol) DO UPDATE SET
                            consecutive=excluded.consecutive,
                            confidence=excluded.confidence,
                            win_rate=excluded.win_rate,
                            dynamic_weight=excluded.dynamic_weight,
                            last_seen=excluded.last_seen
                    ''', (symbol, record.get("consecutive",0), record.get("confidence",0), record.get("win_rate",0.5), dw_json, record.get("last_seen",0)))
                conn.commit()
        except Exception as e:
            log(f"[錯誤] 儲存歷史資料庫失敗: {e}", level="ERROR")

def save_latest_selection_db(candidates):
    with _db_lock:
        try:
            with get_db_connection(LATEST_SELECTION_DB_PATH) as conn:
                cursor = conn.cursor()
                for c in candidates:
                    cursor.execute('''
                        INSERT INTO latest_selection 
                        (symbol, direction, confidence, operation, indicators, indicator_status, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(symbol) DO UPDATE SET
                            direction=excluded.direction,
                            confidence=excluded.confidence,
                            operation=excluded.operation,
                            indicators=excluded.indicators,
                            indicator_status=excluded.indicator_status,
                            timestamp=excluded.timestamp
                    ''', (
                        c["symbol"],
                        c["direction"],
                        c["confidence"],
                        c["operation"],
                        json.dumps(c["indicators"]),
                        json.dumps(c["indicator_status"]),
                        c["timestamp"]
                    ))
                conn.commit()
        except Exception as e:
            log(f"[錯誤] 儲存最新選幣資料庫失敗: {e}", level="ERROR")

def load_latest_selection_db():
    with _db_lock:
        try:
            with get_db_connection(LATEST_SELECTION_DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT symbol, confidence FROM latest_selection")
                rows = cursor.fetchall()
                return {symbol: {"confidence": confidence} for symbol, confidence in rows}
        except Exception as e:
            log(f"[錯誤] 讀取最新選幣資料庫失敗: {e}", level="ERROR")
            return {}

def fetch_ohlcv_for_symbol(symbol, timeframe="1h", limit=100, config=None):
    try:
        df = get_ohlcv_batch([symbol], timeframe, limit, config)
        return symbol, df.get(symbol, None)
    except Exception as e:
        log(f"[錯誤] 取得 {symbol} K線失敗: {e}")
        return symbol, None

def get_ohlcv_batch_multithread(symbols, timeframe="1h", limit=100, config=None, max_workers=4):
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_ohlcv_for_symbol, sym, timeframe, limit, config): sym for sym in symbols}
        for future in futures:
            sym = futures[future]
            try:
                symbol, df = future.result()
                results[symbol] = df
            except Exception as e:
                log(f"[錯誤] 多線程取得 {sym} K線時異常: {e}")
                results[sym] = None
            time.sleep(0.05)
    return results

def adjust_cooldown_time(symbol, history, base_cooldown=3600):
    record = history.get(symbol, {})
    consecutive_loss = record.get("consecutive_loss", 0)
    consecutive_win = record.get("consecutive_win", 0)
    if consecutive_loss >= 3:
        return base_cooldown * 2
    elif consecutive_win >= 3:
        return base_cooldown // 2
    else:
        return base_cooldown

def is_symbol_cooled_down_with_dynamic(symbol, cooldown_pool, config, history):
    cooldown_info = cooldown_pool.get(symbol)
    if not cooldown_info:
        return False
    base_duration = config.get("COOLDOWN_DURATION", 3600)
    dynamic_duration = adjust_cooldown_time(symbol, history, base_duration)
    elapsed = int(time.time()) - cooldown_info.get("timestamp", 0)
    return elapsed < dynamic_duration

def pass_liquidity_filter(symbol, ohlcv_df, min_vol_std=1.5):
    if ohlcv_df is None or ohlcv_df.empty:
        return False
    vol_std = ohlcv_df['volume'].std()
    if vol_std < min_vol_std:
        if debug_mode():
            log(f"[流動性過濾] {symbol} 成交量標準差過低 ({vol_std:.2f} < {min_vol_std})，過濾")
        return False
    return True

def calc_confidence_boost(consecutive: int, base_confidence: float, max_conf: float, win_rate: float):
    if consecutive <= 1:
        return base_confidence
    boost_base = 0.05
    boost_max = 0.3
    boost = boost_base + (boost_max - boost_base) * win_rate
    total_boost = min(boost * (consecutive - 1), max_conf - base_confidence)
    return min(base_confidence + total_boost, max_conf)

def process_symbol(symbol, ohlcv, previous_confidence, position_state, config, cooldown_pool, blocked_symbols, history):
    if symbol in blocked_symbols or is_symbol_blocked(symbol, config):
        return None
    if is_symbol_cooled_down_with_dynamic(symbol, cooldown_pool, config, history):
        return None
    if not pass_pre_filter(symbol, ohlcv, config):
        return None
    if not pass_liquidity_filter(symbol, ohlcv, config.get("MIN_VOL_STD", 1.5)):
        return None

    result = calculate_indicators(ohlcv, symbol, "1h", config.get("DISABLED_INDICATORS", []))
    if not result or result.get("direction") == "none":
        return None

    direction = result["direction"]
    base_confidence = result["score"]
    max_conf = config.get("MAX_CONFIDENCE_SCORE", 5.0)

    record = history.get(symbol, {})
    consecutive = record.get("consecutive", 0)
    win_rate = record.get("win_rate", 0.5)

    dyn_weight = 1.0
    dw = record.get("dynamic_weight")
    if isinstance(dw, dict):
        dyn_weight = dw.get(direction, 1.0)
    elif isinstance(dw, (int, float)):
        dyn_weight = float(dw)

    boost_conf = calc_confidence_boost(consecutive, base_confidence, max_conf, win_rate)

    weighted_confidence = round(boost_conf * dyn_weight, 2)
    if weighted_confidence > max_conf:
        weighted_confidence = max_conf

    confidence = weighted_confidence

    price = okx_client.get_market_price(symbol)
    if price is None or price <= 0:
        return None

    indicator_status = {ind_name: str(ind_val) for ind_name, ind_val in result.get("indicators", {}).items()}

    log(f"[INFO] 紀錄指標組合：{symbol} (信心: {confidence})，連續被選中 {consecutive} 次，勝率 {win_rate}，動態權重 {dyn_weight}")

    pos = position_state.get(symbol, {})
    holding = pos.get("contracts", 0) > 0
    held_dir = pos.get("direction")
    entry_price = pos.get("price")
    add_times = pos.get("add_times", 0)
    reduce_times = pos.get("reduce_times", 0)

    unrealized_profit = 0
    pnl_ratio = 0
    if entry_price and holding:
        invested = entry_price * pos.get("contracts", 0)
        if held_dir == "buy":
            unrealized_profit = (price - entry_price) * pos.get("contracts", 0)
        elif held_dir == "sell":
            unrealized_profit = (entry_price - price) * pos.get("contracts", 0)
        if invested > 0:
            pnl_ratio = unrealized_profit / invested

    take_profit_value = config.get("TAKE_PROFIT_VALUE", 0.2)
    stop_loss_ratio = config.get("STOP_LOSS_RATIO", -0.05)
    threshold = config.get("OPEN_THRESHOLD", 3.0)
    max_add = config.get("MAX_ADD_TIMES", 3)
    max_reduce = config.get("MAX_REDUCE_TIMES", 2)
    require_profit = config.get("REQUIRE_PROFIT_TO_CLOSE", True)

    operation = None
    if not holding and confidence >= threshold:
        operation = "open"
    elif holding and held_dir == direction and confidence > pos.get("confidence", 0) and add_times < max_add:
        operation = "add"
    elif holding:
        if unrealized_profit >= take_profit_value:
            operation = "close"
        elif pnl_ratio <= stop_loss_ratio:
            operation = "reduce" if reduce_times < max_reduce else "close"
        elif confidence < threshold:
            if unrealized_profit > 0 or not require_profit:
                operation = "close"
            else:
                operation = "reduce" if reduce_times < max_reduce else "close"

    if not operation:
        return None

    return {
        "symbol": symbol,
        "direction": direction,
        "confidence": confidence,
        "operation": operation,
        "indicators": result["indicators"],
        "indicator_status": indicator_status,
        "timestamp": int(time.time())
    }

def run_selector():
    init_history_db()
    init_latest_selection_db()

    start_time = time.time()
    config = get_runtime_config()
    all_symbols = get_all_usdt_swap_symbols()
    cooldown_pool, blocked_symbols = load_symbol_locks()
    position_state = load_position_state()
    history = load_selection_history_cached()

    previous_selection = load_latest_selection_db()

    BATCH_SIZE = 10
    candidates = []
    current_seen = set()

    for i in range(0, len(all_symbols), BATCH_SIZE):
        batch = all_symbols[i:i + BATCH_SIZE]
        try:
            ohlcv_data = get_ohlcv_batch_multithread(batch, "1H", limit=100, config=config, max_workers=4)
        except Exception as e:
            log(f"[錯誤] 批次取得 K 線失敗: {e}", level="ERROR")
            continue

        for symbol in batch:
            ohlcv = ohlcv_data.get(symbol)
            if ohlcv is None or ohlcv.empty:
                continue
            prev_score = previous_selection.get(symbol, None)
            result = process_symbol(symbol, ohlcv, prev_score, position_state, config, cooldown_pool, blocked_symbols, history)
            if result:
                candidates.append(result)
                current_seen.add(symbol)
                history.setdefault(result["symbol"], {})["confidence"] = result["confidence"]
                log_combination_result(result)
        time.sleep(0.5)

    decay_ratio = config.get("CONFIDENCE_DECAY_RATIO", 0.9)
    for symbol in set(list(history.keys()) + list(current_seen)):
        if symbol in current_seen:
            history.setdefault(symbol, {"consecutive": 0})
            history[symbol]["consecutive"] = history[symbol].get("consecutive", 0) + 1
            history[symbol]["last_seen"] = int(time.time())
        else:
            history.setdefault(symbol, {"consecutive": 0})
            history[symbol]["consecutive"] = 0
            if "confidence" in history[symbol]:
                history[symbol]["confidence"] = max(0.0, round(history[symbol]["confidence"] * decay_ratio, 2))

    save_selection_history_atomic(history)

    candidates = filter_candidates_by_position(candidates, position_state, config)

    save_latest_selection_db(candidates)
    log(f"完成選出 {len(candidates)} 檔，並寫入最新選幣資料庫", level="INFO")

    end_time = time.time()
    log(f"[選幣] 執行時間: {round(end_time - start_time, 2)} 秒", level="INFO")

if __name__ == "__main__":
    run_selector()
