import os
import json
import time
from datetime import datetime
import pandas as pd

from config import get_runtime_config, debug_mode, test_mode
from selector_utils import (
    get_all_usdt_swap_symbols,
    get_ohlcv_batch,
    pass_pre_filter,
    is_symbol_cooled_down,
    is_symbol_blocked,
    load_latest_selection  # 確保讀取結果永遠為 dict
)
from indicator_calculator import calculate_indicators
from combination_logger import log_combination_result
import okx_client
import state_manager
from logger import log

# === 📁 資料夾設定 ===
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
RESULT_DIR = os.path.join(BASE_DIR, "json_results")
os.makedirs(RESULT_DIR, exist_ok=True)

def load_position_state():
    """
    載入當前持倉狀態，格式為字典（防呆：僅接受 dict 結構）
    """
    path = os.path.join(RESULT_DIR, "position_status.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                log(f"[錯誤] 持倉狀態格式錯誤，強制轉空 dict", level="ERROR")
                return {}
            return data
    except Exception as e:
        log(f"[錯誤] 無法讀取持倉狀態: {e}", level="ERROR")
        return {}

def load_symbol_locks():
    """
    載入冷卻池與封鎖標的資料（防呆：皆保證為 dict）
    """
    def read_json(path):
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
                if isinstance(data, list):
                    return {x: {} for x in data}
            return {}
        except Exception as e:
            log(f"[錯誤] 讀取 {path} 失敗: {e}", level="ERROR")
            return {}
    cooldown = read_json(os.path.join(RESULT_DIR, "cooldown_pool.json"))
    blocked = read_json(os.path.join(RESULT_DIR, "blocked_symbols.json"))
    return cooldown, blocked

def filter_candidates_by_position(candidates, position_state, config):
    """
    根據持倉狀態過濾候選標的，避免同標的多空重複持倉及持倉標的數量超限
    """
    holding_symbols_dirs = {(sym, pos['direction']) for sym, pos in position_state.items()}
    holding_symbols = set(position_state.keys())

    debug_enabled = test_mode() or debug_mode()

    filtered = []
    for c in candidates:
        if not isinstance(c, dict) or 'symbol' not in c:
            log(f"[錯誤] 候選清單元素格式錯誤或缺少symbol: {c}", level="ERROR")
            continue
        symbol = c['symbol']
        direction = c.get('direction', 'buy')
        opposite_direction = 'buy' if direction == 'sell' else 'sell'

        if (symbol, opposite_direction) in holding_symbols_dirs:
            if debug_enabled:
                log(f"[選幣過濾] {symbol} 方向{direction}因相反方向持倉存在，跳過", level="DEBUG")
            continue

        if symbol not in holding_symbols and len(holding_symbols) >= config.get("MAX_HOLDING_SYMBOLS", 6):
            if debug_enabled:
                log(f"[選幣過濾] 持倉已達上限，拒絕新標的 {symbol}", level="DEBUG")
            continue

        filtered.append(c)
    return filtered

def process_symbol(symbol, ohlcv, previous_confidence, position_state, config, cooldown_pool, blocked_symbols):
    """
    單一標的完整篩選與決策流程，包含封鎖、冷卻、預篩、指標計算與操作決策
    """
    if symbol in blocked_symbols or is_symbol_blocked(symbol, config):
        if test_mode():
            log(f"[TEST] {symbol} 被封鎖", level="DEBUG")
        return None
    if is_symbol_cooled_down(symbol, cooldown_pool, config):
        if test_mode():
            log(f"[TEST] {symbol} 在冷卻中", level="DEBUG")
        return None
    if not pass_pre_filter(symbol, ohlcv, config):
        if test_mode():
            log(f"[TEST] {symbol} 不符合預篩條件", level="DEBUG")
        return None

    disabled = config.get("DISABLED_INDICATORS", [])
    result = calculate_indicators(ohlcv, symbol, "1h", disabled)
    if not result or result.get("direction") == "none":
        if test_mode():
            log(f"[TEST] {symbol} 指標計算無明確方向", level="DEBUG")
        return None

    direction = result["direction"]
    confidence = result["score"]
    indicators = result["indicators"]

    # 信心加成，限制最大值100
    if previous_confidence:
        confidence = min(confidence * config.get("CONFIDENCE_BOOST_RATIO", 1.05), 100)

    price = okx_client.get_market_price(symbol)
    if price is None or price <= 0:
        if test_mode():
            log(f"[TEST] {symbol} 無法取得市價", level="DEBUG")
        return None

    pos = position_state.get(symbol, {})
    holding = pos.get("contracts", 0) > 0
    held_dir = pos.get("direction")
    entry_price = pos.get("price", None)
    add_times = pos.get("add_times", 0)
    reduce_times = pos.get("reduce_times", 0)

    unrealized_profit = 0
    pnl_ratio = 0
    invested_capital = 0

    if entry_price and holding:
        invested_capital = entry_price * pos.get("contracts", 0)
        if held_dir == "buy":
            unrealized_profit = (price - entry_price) * pos.get("contracts", 0)
        elif held_dir == "sell":
            unrealized_profit = (entry_price - price) * pos.get("contracts", 0)
        if invested_capital > 0:
            pnl_ratio = unrealized_profit / invested_capital

    take_profit_value = config.get("TAKE_PROFIT_VALUE", 0.02)
    stop_loss_ratio = config.get("STOP_LOSS_RATIO", -0.05)

    if test_mode():
        log(f"[TEST] {symbol} 未實現收益額: {unrealized_profit:.4f} USDT, 收益率: {pnl_ratio:.4%}", level="DEBUG")

    threshold = config.get("OPEN_THRESHOLD", 3.5)
    max_add = config.get("MAX_ADD_TIMES", 3)
    max_reduce = config.get("MAX_REDUCE_TIMES", 2)
    require_profit = config.get("REQUIRE_PROFIT_TO_CLOSE", True)
    operation = None

    # 新標的只要本次信心分數合格即可 open
    if not holding and confidence >= threshold:
        operation = "open"
    # 同方向持倉且信心增加且加倉次數未超過上限，加倉
    elif holding and held_dir == direction and confidence > pos.get("confidence", 0) and add_times < max_add:
        operation = "add"
    elif holding:
        if unrealized_profit >= take_profit_value:
            operation = "close"
        elif pnl_ratio <= stop_loss_ratio:
            if reduce_times < max_reduce:
                operation = "reduce"
            else:
                operation = "close"
        elif confidence < threshold:
            if unrealized_profit > 0 or not require_profit:
                operation = "close"
            elif reduce_times < max_reduce:
                operation = "reduce"
            else:
                operation = "close"
    else:
        if test_mode():
            log(f"[TEST] {symbol} 無進場動作，holding={holding}, conf={confidence}", level="DEBUG")

    if not operation:
        return None

    if test_mode():
        log(f"[TEST] ✅ {symbol} 符合條件，操作: {operation}，信心: {confidence}", level="DEBUG")

    return {
        "symbol": symbol,
        "direction": direction,
        "confidence": round(confidence, 2),
        "operation": operation,
        "indicators": indicators,
        "timestamp": int(time.time()),
    }

def run_selector():
    """
    主選幣流程，包含所有資料讀取、防呆及結果輸出
    """
    config = get_runtime_config()
    all_symbols = get_all_usdt_swap_symbols()
    cooldown_pool, blocked_symbols = load_symbol_locks()
    position_state = load_position_state()

    prev_path = os.path.join(RESULT_DIR, "latest_selection.json")
    previous_selection = {}
    if os.path.exists(prev_path):
        try:
            previous_selection = load_latest_selection(prev_path)
            # 防呆：信心轉為 float，非數值則忽略
            previous_selection = {k: float(v.get("confidence", 0)) if v else 0 for k, v in previous_selection.items()}
        except Exception as e:
            log(f"[錯誤] 讀取歷史選幣結果失敗: {e}", level="ERROR")

    BATCH_SIZE = 10
    candidates = []

    for i in range(0, len(all_symbols), BATCH_SIZE):
        batch = all_symbols[i:i + BATCH_SIZE]
        try:
            ohlcv_data = get_ohlcv_batch(batch, "1H", limit=100, config=config)
        except Exception as e:
            log(f"[錯誤] 批次取得 K 線失敗: {e}", level="ERROR")
            continue

        for symbol in batch:
            ohlcv = ohlcv_data.get(symbol)
            if ohlcv is None or ohlcv.empty:
                if test_mode():
                    log(f"[TEST] {symbol} 沒有有效 K 線資料", level="DEBUG")
                continue
            try:
                prev_score = previous_selection.get(symbol, None)
                result = process_symbol(
                    symbol, ohlcv, prev_score, position_state, config, cooldown_pool, blocked_symbols
                )
                if result:
                    candidates.append(result)
                    log_combination_result(result)
            except Exception as e:
                log(f"[錯誤] 處理 {symbol} 發生例外: {e}", level="ERROR")
        time.sleep(0.5)

    if debug_mode():
        log(f"[DEBUG] 進入 filter 前，合格標的數量: {len(candidates)}", level="DEBUG")
        for c in candidates:
            log(f"[DEBUG] 合格標的: {c['symbol']} 方向: {c['direction']} 信心: {c['confidence']}", level="DEBUG")

    candidates = filter_candidates_by_position(candidates, position_state, config)

    save_path = os.path.join(RESULT_DIR, "latest_selection.json")
    try:
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(candidates, f, ensure_ascii=False, indent=2)
        log(f"完成選出 {len(candidates)} 檔，儲存於 {save_path}，並寫入 log", level="INFO")
    except Exception as e:
        log(f"[錯誤] 寫入最新選幣結果失敗: {e}", level="ERROR")

if __name__ == "__main__":
    run_selector()
