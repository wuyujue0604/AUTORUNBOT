import os
import time
import json
import traceback
from config import get_runtime_config, debug_mode, test_mode
import okx_client, state_manager, funding_manager, order_notifier
from logger import log
from order_notifier import log_trade_action, log_event
from combination_logger import record_performance  # 績效追蹤

MAX_CONTRACTS_PER_ORDER_DEFAULT = 6000

def calculate_investment_ratio(confidence: float, config: dict) -> float:
    """
    根據信心分數計算投入比例，限制在最小與最大比例之間。
    """
    min_ratio = float(config.get("MIN_SINGLE_POSITION_RATIO", 0.01))
    max_ratio = float(config.get("MAX_SINGLE_POSITION_RATIO", 0.15))
    ratio = (confidence / 100.0) * max_ratio
    return max(min_ratio, min(ratio, max_ratio))

def get_order_status(symbol: str, ord_id: str):
    """
    查詢訂單狀態，回傳狀態字串，若查詢失敗回傳 None。
    """
    try:
        response = okx_client.get_order(symbol, ord_id)
        if response and response.get("code") == "0" and response.get("data"):
            order_data = response["data"][0]
            return order_data.get("state")
        else:
            return None
    except Exception as e:
        log(f"[錯誤][訂單查詢] {symbol} ordId={ord_id} 查詢失敗: {e}")
        return None

def allocate_capital_for_position(symbol: str, direction: str, confidence: float, config: dict,
                                  long_count: int, short_count: int):
    """
    根據多單與空單數量比例分配可用本金，再估算合約張數與保證金
    :param symbol: 合約名稱
    :param direction: "buy" 或 "sell"
    :param confidence: 信心分數 (0-100)
    :param config: 系統設定字典
    :param long_count: 當前選中多單標的數量
    :param short_count: 當前選中空單標的數量
    :return: (contracts, price, leverage)
    """
    price = okx_client.get_market_price(symbol)
    if price is None or price <= 0:
        raise ValueError("無法取得有效市價")

    lev_long, lev_short = okx_client.get_leverage(symbol)
    max_leverage = float(config.get("MAX_LEVERAGE_LIMIT", 10))
    leverage = lev_long if direction == "buy" else lev_short
    leverage = min(leverage, max_leverage)

    balance = okx_client.get_trade_balance()

    # 預留10%保險金 buffer
    capital_buffer_ratio = float(config.get("CAPITAL_BUFFER_RATIO", 0.10))
    available = balance * (1 - capital_buffer_ratio)

    total_positions = long_count + short_count
    if total_positions == 0:
        # 避免除以0，直接用全部可用本金
        allocated_capital = available
    else:
        if direction == "buy":
            allocated_capital = available * (long_count / total_positions)
        else:
            # 空單保留本金和停損資金
            stop_loss_ratio = abs(float(config.get("STOP_LOSS_RATIO", -0.05)))
            reserved_amount = (available / 2) * (1 + stop_loss_ratio)  # 空單預留本金+停損資金
            free_amount = max(0, available - reserved_amount)
            allocated_capital = free_amount * (short_count / total_positions)

    ratio = calculate_investment_ratio(confidence, config)
    budget = allocated_capital * ratio

    margin_per_contract = price / leverage * float(config.get("ORDER_MARGIN_BUFFER", 1.10))

    max_possible_contracts = int(allocated_capital / margin_per_contract)
    contracts = int(budget / margin_per_contract)

    contracts = max(1, min(
        contracts,
        max_possible_contracts,
        config.get("MAX_CONTRACTS_PER_ORDER", MAX_CONTRACTS_PER_ORDER_DEFAULT)
    ))

    if debug_mode():
        log(f"[DEBUG][資金分配估算] {symbol} 方向={direction} 信心={confidence:.2f} "
            f"allocated_capital={allocated_capital:.2f}, budget={budget:.2f}, price={price:.4f}, leverage={leverage:.2f}, "
            f"margin_per_contract={margin_per_contract:.6f}, contracts={contracts}")

    return contracts, price, leverage

def estimate_contracts_and_margin(symbol: str, direction: str, confidence: float, config: dict):
    """
    估算下單張數及保證金，改為使用 allocate_capital_for_position 取得依多空比例分配的資金。
    需由外部呼叫時帶入最新多單與空單數量。
    """
    # 讀取最新選幣結果，計算多空數量
    path = os.path.join(os.path.dirname(__file__), "json_results", "latest_selection.json")
    if not os.path.exists(path):
        raise FileNotFoundError("最新選幣結果檔案不存在")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
        if isinstance(data, list):
            entries = data
        elif isinstance(data, dict):
            entries = list(data.values())
        else:
            entries = []

    long_count = sum(1 for e in entries if e.get("direction") == "buy")
    short_count = sum(1 for e in entries if e.get("direction") == "sell")

    return allocate_capital_for_position(symbol, direction, confidence, config, long_count, short_count)

def send_order(symbol: str, direction: str, contracts: int, config: dict, reduce_only=False):
    """
    發送下單請求，多次重試與錯誤處理，等待訂單成交確認。
    """
    try:
        if test_mode():
            log(f"[TEST][下單] 模擬下單: {symbol} {direction} {contracts} 張{' [reduceOnly]' if reduce_only else ''}")
            return {"ordId": "test_order", "filled": contracts}

        max_retry = int(config.get("MAX_RETRY_ON_FAILURE", 3))
        wait_time = 1
        for attempt in range(1, max_retry + 1):
            resp = okx_client.place_order(symbol, direction, contracts, reduce_only=reduce_only)

            if not isinstance(resp, dict):
                log(f"[錯誤] {symbol} 下單回傳格式非 dict，內容: {resp}", "ERROR")
                log(f"[下單][重試] ({attempt}次): {symbol} {direction} {contracts} 張 失敗或格式錯誤，等待 {wait_time} 秒後重試")
                time.sleep(wait_time)
                wait_time = min(wait_time * 2, 8)
                continue

            code = resp.get("code", "0")
            data = resp.get("data", [])
            if data and isinstance(data, list) and data[0].get("ordId"):
                ord_id = data[0].get("ordId")
                time.sleep(0.8)
                status = get_order_status(symbol, ord_id)
                if status and status.lower() in ("filled", "partial-filled"):
                    if code == "0":
                        log(f"[下單][成功] ({attempt}次): {symbol} {direction} {contracts} 張 訂單號: {ord_id} 狀態: {status}")
                    else:
                        log(f"[下單][警告] ({attempt}次): {symbol} {direction} {contracts} 張 非正常code({code})但有訂單ID，狀態: {status}，視為成功")
                    return data[0]
                else:
                    log(f"[警告] {symbol} 訂單 {ord_id} 狀態為 {status}，尚未成交，等待重試")
            if code == "50113":
                log(f"[錯誤] {symbol} 下單失敗: Invalid Sign，請檢查API金鑰與時間同步", "ERROR")
                return None
            if any("Insufficient USDT margin" in item.get("sMsg", "") for item in data):
                log(f"[警告] {symbol} 下單失敗: 保證金不足，不再重試", "WARN")
                return None

            log(f"[下單][重試] ({attempt}次): {symbol} {direction} {contracts} 張 失敗或格式錯誤，等待 {wait_time} 秒後重試")
            time.sleep(wait_time)
            wait_time = min(wait_time * 2, 8)

        log(f"[下單][失敗] 超過重試次數: {symbol} {direction} {contracts} 張{' [reduceOnly]' if reduce_only else ''}")
        return None

    except Exception as e:
        log(f"[例外][下單] {symbol} send_order錯誤: {e}\n{traceback.format_exc()}", "ERROR")
        return None

def check_position_conflict_and_limit(symbol: str, direction: str, position_state: dict, max_symbols: int) -> bool:
    """
    風控檢查，避免持倉方向衝突與持倉標的數超限
    """
    try:
        holding_symbols_dirs = {(sym, pos['direction']) for sym, pos in position_state.items()}
        holding_symbols = set(position_state.keys())
        opposite_direction = 'buy' if direction == 'sell' else 'sell'

        if (symbol, opposite_direction) in holding_symbols_dirs:
            log(f"[拒單][風控] {symbol} 建倉方向 {direction} 與現有持倉相反方向衝突，跳過")
            return False

        if symbol not in holding_symbols and len(holding_symbols) >= max_symbols:
            log(f"[拒單][風控] 持倉標的數已達上限({max_symbols})，拒絕新建倉 {symbol}")
            return False

        return True
    except Exception as e:
        log(f"[例外][風控] check_position_conflict_and_limit錯誤: {e}\n{traceback.format_exc()}", "ERROR")
        return False

def get_order_params(position_direction: str, action: str):
    """
    根據操作類型取得下單方向及是否為reduceOnly
    """
    if action in ("open", "add"):
        return position_direction, False
    elif action in ("reduce", "close"):
        reversed_dir = "sell" if position_direction == "buy" else "buy"
        return reversed_dir, True
    else:
        raise ValueError(f"未知操作類型: {action}")

def wait_for_position_close(symbol: str, position_direction: str, timeout=5.0, interval=0.5):
    """
    等待持倉被清空或方向改變，最多等待 timeout 秒
    """
    start = time.time()
    while time.time() - start < timeout:
        pos = state_manager.get_position_state(symbol)
        if not pos:
            return True
        if pos.get('direction') != position_direction:
            return True
        time.sleep(interval)
    log(f"[警告] {symbol} 持倉未在 {timeout} 秒內清空")
    return False

# 以下是主要四個交易操作函式，已調整使用新的estimate_contracts_and_margin

def try_close_position(entry: dict, config: dict):
    symbol = entry["symbol"]
    current = state_manager.get_position_state(symbol)
    if not current:
        log(f"[錯誤][平倉] {symbol} 無持倉紀錄", "ERROR")
        return None

    position_direction = current["direction"]
    contracts = current["contracts"]
    price = okx_client.get_market_price(symbol)
    entry_price = current.get("price", 0)
    confidence = current.get("confidence", 0)

    log(f"[平倉][準備] {symbol} 全部 {contracts} 張，方向: {position_direction}，成本: {entry_price}，市價: {price}，信心: {confidence}")

    if test_mode():
        order_dir, reduce_only = get_order_params(position_direction, "close")
        log(f"[TEST][平倉] 模擬平倉: {symbol} {order_dir} {contracts} 張 {'[reduceOnly]' if reduce_only else ''}")
        return {
            "symbol": symbol,
            "direction": position_direction,
            "contracts": contracts,
            "price": price,
            "confidence": confidence,
            "operation": "close",
            "order_id": "test_order",
        }

    try:
        order_dir, reduce_only = get_order_params(position_direction, "close")
        result = send_order(symbol, order_dir, contracts, config, reduce_only=reduce_only)
        if result:
            log(f"[平倉][成功] {symbol} 平倉 {contracts} 張 @ {price}，API回傳: {result}")
            wait_for_position_close(symbol, order_dir)
            state_manager.remove_position(symbol)

            pnl = 0
            if entry_price > 0:
                pnl = (price - entry_price) * contracts if position_direction == "buy" else (entry_price - price) * contracts

            timestamp = int(time.time())
            log_data = {
                "symbol": symbol,
                "direction": position_direction,
                "contracts": contracts,
                "price": price,
                "confidence": confidence,
                "operation": "close",
                "timestamp": timestamp,
                "log_timestamp": timestamp,
                "pnl": round(pnl, 4),
                "result_emoji": "📈" if pnl > 0 else "📉",
                "order_id": result.get("ordId") if isinstance(result, dict) else "",
            }
            state_manager.record_trade_log(log_data)
            log_trade_action(
                log_data["symbol"],
                log_data["operation"],
                log_data["direction"],
                log_data["confidence"],
                log_data["price"],
                log_data["contracts"],
                log_data.get("pnl")
            )

            weights = {
                "TF_WEIGHT_1H": float(config.get("TF_WEIGHT_1H", 0.7)),
                "TF_WEIGHT_15M": 1 - float(config.get("TF_WEIGHT_1H", 0.7)),
            }
            perf_log = {
                "symbol": symbol,
                "operation": "close",
                "pnl": round(pnl, 4),
                "win": pnl > 0,
                "weights": weights,
                "timestamp": timestamp,
            }
            record_performance(perf_log)

            if pnl > 0:
                reserve_ratio = float(config.get("RESERVE_PROFIT_RATIO", 0.5))
                reserve_amount = pnl * reserve_ratio
                log(f"[平倉][獲利] {symbol} 平倉獲利 {pnl:.2f} USDT，保留 {reserve_amount:.2f} USDT")
                state_manager.add_profit(reserve_amount)
                total_reserved = state_manager.get_reserved_profit()
                if total_reserved >= float(config.get("MIN_PROFIT_TO_RESERVE", 5.0)):
                    if funding_manager.process_profit_transfer(total_reserved):
                        state_manager.reset_reserved_profit()
            return log_data
        else:
            log(f"[平倉][失敗] {symbol} 平倉下單失敗", "ERROR")
            return None
    except Exception as e:
        log(f"[例外][平倉] {symbol} 平倉異常: {e}\n{traceback.format_exc()}", "ERROR")
        return None

def try_build_position(entry: dict, config: dict):
    symbol = entry["symbol"]
    position_direction = entry["direction"]
    confidence = float(entry["confidence"])
    max_symbols = int(config.get("MAX_HOLDING_SYMBOLS", 100))
    position_state = state_manager.load_position_state()

    current_pos = position_state.get(symbol, {})
    long_qty = current_pos.get("contracts", 0) if current_pos.get("direction") == "buy" else 0
    short_qty = current_pos.get("contracts", 0) if current_pos.get("direction") == "sell" else 0

    # 若方向相反持倉存在，先平倉
    if position_direction == "buy" and short_qty > 0:
        log(f"[自動平倉] {symbol} 有空單持倉({short_qty}張)，先平空單")
        try_close_position({"symbol": symbol}, config)
        wait_for_position_close(symbol, "sell")

    if position_direction == "sell" and long_qty > 0:
        log(f"[自動平倉] {symbol} 有多單持倉({long_qty}張)，先平多單")
        try_close_position({"symbol": symbol}, config)
        wait_for_position_close(symbol, "buy")

    if not check_position_conflict_and_limit(symbol, position_direction, position_state, max_symbols):
        return None

    try:
        contracts, price, leverage = estimate_contracts_and_margin(symbol, position_direction, confidence, config)
    except Exception as e:
        log(f"[錯誤][建倉] {symbol} 建倉估算失敗: {e}", "ERROR")
        return None

    budget = price * contracts / leverage
    total_balance = okx_client.get_trade_balance()
    exposure_limit = float(config.get("MAX_SYMBOL_EXPOSURE_RATIO", 0.5))
    if total_balance > 0 and (budget / total_balance) > exposure_limit:
        log(f"[拒單][曝險] {symbol} 預估投入 {budget:.2f} 超過總資金的 {exposure_limit*100:.0f}%，跳過建倉")
        return None

    direction, reduce_only = get_order_params(position_direction, "open")
    result = send_order(symbol, direction, contracts, config, reduce_only=reduce_only)

    order_id = result.get("ordId") if isinstance(result, dict) else ""

    if result and isinstance(result, dict):
        log(f"[建倉][成功] {symbol} 建倉 {contracts} 張 @ {price}")
        state_manager.update_position_state(symbol, position_direction, contracts, price, confidence, {
            "add_times": 0,
            "reduce_times": 0,
            "timestamp": int(time.time())
        })
        trade_log = {
            "symbol": symbol,
            "direction": position_direction,
            "contracts": contracts,
            "price": price,
            "confidence": confidence,
            "operation": "open",
            "order_id": order_id,
            "response": result,
        }
        state_manager.record_trade_log(trade_log)
        log_trade_action(
            trade_log["symbol"],
            trade_log["operation"],
            trade_log["direction"],
            trade_log["confidence"],
            trade_log["price"],
            trade_log["contracts"]
        )

        weights = {
            "TF_WEIGHT_1H": float(config.get("TF_WEIGHT_1H", 0.7)),
            "TF_WEIGHT_15M": 1 - float(config.get("TF_WEIGHT_1H", 0.7)),
        }
        perf_log = {
            "symbol": symbol,
            "operation": "open",
            "pnl": 0,
            "win": None,
            "weights": weights,
            "timestamp": int(time.time()),
        }
        record_performance(perf_log)

        return trade_log
    else:
        log(f"[錯誤][建倉] {symbol} 建倉下單失敗", "ERROR")
        return None

def try_add_position(entry: dict, config: dict):
    symbol = entry["symbol"]
    position_direction = entry["direction"]
    confidence = float(entry["confidence"])
    max_add = int(config.get("MAX_ADD_TIMES", 3))
    position_state = state_manager.load_position_state()

    current = state_manager.get_position_state(symbol)
    if not current:
        log(f"[錯誤][加倉] {symbol} 無持倉紀錄", "ERROR")
        return None

    if not check_position_conflict_and_limit(symbol, position_direction, position_state, config.get("MAX_HOLDING_SYMBOLS", 100)):
        log(f"[拒單][加倉] {symbol} 因持倉衝突或上限限制拒絕加倉")
        return None

    add_times = current.get("add_times", 0)
    if add_times >= max_add:
        log(f"[風控][加倉] {symbol} 已加倉 {add_times} 次，超過上限 {max_add}，轉為平倉")
        try_close_position({"symbol": symbol, "exit_reason": "加倉次數達上限"}, config)
        return None

    log(f"[加倉][處理] {symbol} direction={position_direction} confidence={confidence} 已加倉 {add_times} 次")
    try:
        contracts, price, leverage = estimate_contracts_and_margin(symbol, position_direction, confidence, config)
    except Exception as e:
        log(f"[錯誤][加倉] {symbol} 加倉估算失敗: {e}", "ERROR")
        return None

    direction, reduce_only = get_order_params(position_direction, "add")
    result = send_order(symbol, direction, contracts, config, reduce_only=reduce_only)

    order_id = result.get("ordId") if isinstance(result, dict) else ""

    if result and isinstance(result, dict):
        log(f"[加倉][成功] {symbol} 加倉 {contracts} 張 @ {price}")
        state_manager.update_position_state(symbol, position_direction, contracts, price, confidence, {
            "add_times": add_times + 1,
            "timestamp": int(time.time())
        }, add=True)
        trade_log = {
            "symbol": symbol,
            "direction": position_direction,
            "contracts": contracts,
            "price": price,
            "confidence": confidence,
            "operation": "add",
            "order_id": order_id,
            "response": result,
        }
        state_manager.record_trade_log(trade_log)
        log_trade_action(
            trade_log["symbol"],
            trade_log["operation"],
            trade_log["direction"],
            trade_log["confidence"],
            trade_log["price"],
            trade_log["contracts"]
        )

        weights = {
            "TF_WEIGHT_1H": float(config.get("TF_WEIGHT_1H", 0.7)),
            "TF_WEIGHT_15M": 1 - float(config.get("TF_WEIGHT_1H", 0.7)),
        }
        perf_log = {
            "symbol": symbol,
            "operation": "add",
            "pnl": 0,
            "win": None,
            "weights": weights,
            "timestamp": int(time.time()),
        }
        record_performance(perf_log)

        return trade_log
    else:
        log(f"[錯誤][加倉] {symbol} 加倉下單失敗", "ERROR")
        return None

def try_reduce_position(entry: dict, config: dict):
    symbol = entry["symbol"]
    current = state_manager.get_position_state(symbol)
    if not current:
        log(f"[錯誤][減倉] {symbol} 無持倉紀錄", "ERROR")
        return None

    position_direction = current["direction"]

    # 空單禁止減倉改直接平倉
    if position_direction == "sell":
        log(f"[拒絕][減倉] {symbol} 空單不允許減倉，請直接平倉", "WARN")
        entry = {"symbol": symbol}
        return try_close_position(entry, config)

    contracts = max(1, current["contracts"] // 2)
    price = okx_client.get_market_price(symbol)
    entry_price = current.get("price", 0)
    confidence = current.get("confidence", 0)

    log(f"[減倉][準備] {symbol} 減少 {contracts} 張, 方向: {position_direction}, 成本: {entry_price}, 市價: {price}, 信心: {confidence}")

    if test_mode():
        order_dir, reduce_only = get_order_params(position_direction, "reduce")
        log(f"[TEST][減倉] 模擬減倉: {symbol} {order_dir} {contracts} 張 {'[reduceOnly]' if reduce_only else ''}")
        return {
            "symbol": symbol,
            "direction": position_direction,
            "contracts": contracts,
            "price": price,
            "confidence": confidence,
            "operation": "reduce",
            "order_id": "test_order",
        }

    try:
        order_dir, reduce_only = get_order_params(position_direction, "reduce")
        result = send_order(symbol, order_dir, contracts, config, reduce_only=reduce_only)

        order_id = result.get("ordId") if isinstance(result, dict) else ""

        if result and isinstance(result, dict):
            log(f"[減倉][成功] {symbol} 減倉 {contracts} 張 @ {price}，API回傳: {result}")
            state_manager.update_position_after_reduce(symbol, contracts)

            pnl = 0
            if entry_price > 0:
                pnl = (price - entry_price) * contracts if position_direction == "buy" else (entry_price - price) * contracts

            log_data = {
                "symbol": symbol,
                "direction": position_direction,
                "contracts": contracts,
                "price": price,
                "confidence": confidence,
                "operation": "reduce",
                "timestamp": int(time.time()),
                "log_timestamp": int(time.time()),
                "pnl": round(pnl, 4),
                "result_emoji": "📈" if pnl > 0 else "📉",
                "order_id": order_id,
            }
            state_manager.record_trade_log(log_data)
            log_trade_action(
                log_data["symbol"],
                log_data["operation"],
                log_data["direction"],
                log_data["confidence"],
                log_data["price"],
                log_data["contracts"],
                log_data.get("pnl")
            )

            weights = {
                "TF_WEIGHT_1H": float(config.get("TF_WEIGHT_1H", 0.7)),
                "TF_WEIGHT_15M": 1 - float(config.get("TF_WEIGHT_1H", 0.7)),
            }
            perf_log = {
                "symbol": symbol,
                "operation": "reduce",
                "pnl": round(pnl, 4),
                "win": pnl > 0,
                "weights": weights,
                "timestamp": int(time.time()),
            }
            record_performance(perf_log)

            if pnl > 0:
                reserve_ratio = float(config.get("RESERVE_PROFIT_RATIO", 0.5))
                reserve_amount = pnl * reserve_ratio
                log(f"[減倉][獲利] {symbol} 減倉獲利 {pnl:.2f} USDT，保留 {reserve_amount:.2f} USDT")
                state_manager.add_profit(reserve_amount)
                total_reserved = state_manager.get_reserved_profit()
                if total_reserved >= float(config.get("MIN_PROFIT_TO_RESERVE", 5.0)):
                    if funding_manager.process_profit_transfer(total_reserved):
                        state_manager.reset_reserved_profit()
            return log_data
        else:
            log(f"[錯誤][減倉] {symbol} 減倉下單失敗", "ERROR")
            return None
    except Exception as e:
        log(f"[例外][減倉] {symbol} 減倉異常: {e}\n{traceback.format_exc()}", "ERROR")
        return None

def handle_removed_position(symbol: str, pos: dict, latest_selection: dict, config: dict) -> bool:
    """
    持倉同步處理：
    1. 持倉標的不在最新選幣清單，且已獲利或不強制獲利，直接平倉。
    2. 否則嘗試減倉，超過最大減倉次數強制平倉。
    3. 信心下降時同理減倉或平倉。
    """
    reason = ""
    latest = latest_selection.get(symbol)
    current_conf = float(pos.get("confidence", 0))
    reduce_times = pos.get("reduce_times", 0)
    direction = pos.get("direction")
    contracts = int(pos.get("contracts", 0))
    entry_price = float(pos.get("price", 0))

    price = okx_client.get_market_price(symbol)
    if not price:
        log(f"[錯誤][持倉同步] 取得市價失敗: {symbol}", "ERROR")
        return False

    ts = int(time.time())

    pnl = 0  # 初始化pnl

    if not latest:
        require_profit = config.get("REQUIRE_PROFIT_TO_CLOSE", True)
        profit = (price - entry_price) if direction == "buy" else (entry_price - price)
        pnl = profit

        if profit > 0 or not require_profit:
            reason = "不在選幣名單，已獲利或允許虧損"
            entry = {"symbol": symbol}
            success = try_close_position(entry, config)
            if success:
                state_manager.remove_position(symbol)
                log_data = {
                    "symbol": symbol,
                    "direction": direction,
                    "contracts": contracts,
                    "price": price,
                    "confidence": current_conf,
                    "operation": "close",
                    "timestamp": ts,
                    "log_timestamp": ts,
                    "result_emoji": "📈" if pnl > 0 else "📉",
                    "exit_reason": reason
                }
                state_manager.record_trade_log(log_data)
                log_trade_action(
                    log_data["symbol"],
                    log_data["operation"],
                    log_data["direction"],
                    log_data["confidence"],
                    log_data["price"],
                    log_data["contracts"],
                    log_data.get("pnl")
                )
                return True
            else:
                log(f"[錯誤][持倉同步] {symbol} 平倉下單失敗，稍後重試", "ERROR")
                return False

        if reduce_times < config.get("MAX_REDUCE_TIMES", 2):
            if direction == "sell":
                # 空單禁止減倉，改直接平倉
                log(f"[拒絕][減倉] {symbol} 空單不允許減倉，請直接平倉", "WARN")
                entry = {"symbol": symbol}
                success = try_close_position(entry, config)
                if success:
                    state_manager.remove_position(symbol)
                    log_data = {
                        "symbol": symbol,
                        "direction": direction,
                        "contracts": contracts,
                        "price": price,
                        "confidence": current_conf,
                        "operation": "close",
                        "timestamp": ts,
                        "log_timestamp": ts,
                        "result_emoji": "📈" if pnl > 0 else "📉",
                        "exit_reason": "空單禁止減倉，直接平倉"
                    }
                    state_manager.record_trade_log(log_data)
                    log_trade_action(
                        log_data["symbol"],
                        log_data["operation"],
                        log_data["direction"],
                        log_data["confidence"],
                        log_data["price"],
                        log_data["contracts"],
                        log_data.get("pnl")
                    )
                    return True
                else:
                    log(f"[錯誤][持倉同步] {symbol} 空單強制平倉失敗，稍後重試", "ERROR")
                    return False

            reduce_qty = max(1, contracts // 2)
            reason = "不在名單但未獲利，嘗試減倉"
            entry = {"symbol": symbol}
            success = try_reduce_position(entry, config)
            if success:
                state_manager.update_position_after_reduce(symbol, reduce_qty)
                log_data = {
                    "symbol": symbol,
                    "direction": direction,
                    "contracts": reduce_qty,
                    "price": price,
                    "confidence": current_conf,
                    "operation": "reduce",
                    "timestamp": ts,
                    "log_timestamp": ts,
                    "result_emoji": "📈" if pnl > 0 else "📉",
                    "exit_reason": reason
                }
                state_manager.record_trade_log(log_data)
                log_trade_action(
                    log_data["symbol"],
                    log_data["operation"],
                    log_data["direction"],
                    log_data["confidence"],
                    log_data["price"],
                    log_data["contracts"],
                    log_data.get("pnl")
                )
                return True
            else:
                log(f"[錯誤][持倉同步] {symbol} 減倉下單失敗，稍後重試", "ERROR")
                return False
        else:
            reason = "不在名單且減倉次數用盡，強制平倉"
            entry = {"symbol": symbol}
            success = try_close_position(entry, config)
            if success:
                state_manager.remove_position(symbol)
                log_data = {
                    "symbol": symbol,
                    "direction": direction,
                    "contracts": contracts,
                    "price": price,
                    "confidence": current_conf,
                    "operation": "close",
                    "timestamp": ts,
                    "log_timestamp": ts,
                    "result_emoji": "📈" if pnl > 0 else "📉",
                    "exit_reason": reason
                }
                state_manager.record_trade_log(log_data)
                log_trade_action(
                    log_data["symbol"],
                    log_data["operation"],
                    log_data["direction"],
                    log_data["confidence"],
                    log_data["price"],
                    log_data["contracts"],
                    log_data.get("pnl")
                )
                return True
            else:
                log(f"[錯誤][持倉同步] {symbol} 強制平倉下單失敗，稍後重試", "ERROR")
                return False

    new_conf = float(latest.get("confidence", 0))
    if new_conf < current_conf:
        if reduce_times < config.get("MAX_REDUCE_TIMES", 2):
            if direction == "sell":
                # 空單禁止減倉，改直接平倉
                log(f"[拒絕][減倉] {symbol} 空單不允許減倉，請直接平倉", "WARN")
                entry = {"symbol": symbol}
                success = try_close_position(entry, config)
                if success:
                    state_manager.remove_position(symbol)
                    log_data = {
                        "symbol": symbol,
                        "direction": direction,
                        "contracts": contracts,
                        "price": price,
                        "confidence": current_conf,
                        "operation": "close",
                        "timestamp": ts,
                        "log_timestamp": ts,
                        "result_emoji": "📈" if pnl > 0 else "📉",
                        "exit_reason": "空單禁止減倉，直接平倉"
                    }
                    state_manager.record_trade_log(log_data)
                    log_trade_action(
                        log_data["symbol"],
                        log_data["operation"],
                        log_data["direction"],
                        log_data["confidence"],
                        log_data["price"],
                        log_data["contracts"],
                        log_data.get("pnl")
                    )
                    return True
                else:
                    log(f"[錯誤][持倉同步] {symbol} 空單強制平倉失敗，稍後重試", "ERROR")
                    return False

            reduce_qty = max(1, contracts // 2)
            reason = "信心下降，嘗試減倉"
            entry = {"symbol": symbol}
            success = try_reduce_position(entry, config)
            if success:
                state_manager.update_position_after_reduce(symbol, reduce_qty)
                pnl = (price - entry_price) if direction == "buy" else (entry_price - price)
                log_data = {
                    "symbol": symbol,
                    "direction": direction,
                    "contracts": reduce_qty,
                    "price": price,
                    "confidence": current_conf,
                    "operation": "reduce",
                    "timestamp": ts,
                    "log_timestamp": ts,
                    "result_emoji": "📈" if pnl > 0 else "📉",
                    "exit_reason": reason
                }
                state_manager.record_trade_log(log_data)
                log_trade_action(
                    log_data["symbol"],
                    log_data["operation"],
                    log_data["direction"],
                    log_data["confidence"],
                    log_data["price"],
                    log_data["contracts"],
                    log_data.get("pnl")
                )
                return True
            else:
                log(f"[錯誤][持倉同步] {symbol} 減倉下單失敗，稍後重試", "ERROR")
                return False
        else:
            reason = "信心下降且減倉次數用盡，平倉"
            entry = {"symbol": symbol}
            success = try_close_position(entry, config)
            if success:
                state_manager.remove_position(symbol)
                pnl = (price - entry_price) if direction == "buy" else (entry_price - price)
                log_data = {
                    "symbol": symbol,
                    "direction": direction,
                    "contracts": contracts,
                    "price": price,
                    "confidence": current_conf,
                    "operation": "close",
                    "timestamp": ts,
                    "log_timestamp": ts,
                    "result_emoji": "📈" if pnl > 0 else "📉",
                    "exit_reason": reason
                }
                state_manager.record_trade_log(log_data)
                log_trade_action(
                    log_data["symbol"],
                    log_data["operation"],
                    log_data["direction"],
                    log_data["confidence"],
                    log_data["price"],
                    log_data["contracts"],
                    log_data.get("pnl")
                )
                return True
            else:
                log(f"[錯誤][持倉同步] {symbol} 平倉下單失敗，稍後重試", "ERROR")
                return False

    return True

def run_order_executor():
    """
    主控調度函式，讀取最新選幣結果並依指令執行下單。
    """
    config = get_runtime_config()
    path = os.path.join(os.path.dirname(__file__), "json_results", "latest_selection.json")
    if not os.path.exists(path):
        log("[警告][主控] 找不到選幣結果檔案，無法執行下單")
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                entries = data
            elif isinstance(data, dict):
                entries = list(data.values())
            else:
                entries = []
    except Exception as e:
        log(f"[錯誤][主控] 讀取選幣結果失敗: {e}", "ERROR")
        return []

    trades = []
    for entry in entries:
        op = entry.get("operation")
        symbol = entry.get("symbol")
        log(f"[主控][調度] 處理交易指令: {symbol}，操作: {op}")

        try:
            trade = None
            if op == "open":
                trade = try_build_position(entry, config)
            elif op == "add":
                trade = try_add_position(entry, config)
            elif op == "reduce":
                trade = try_reduce_position(entry, config)
            elif op == "close":
                trade = try_close_position(entry, config)
            else:
                log(f"[忽略][主控] 不支援的操作類型: {op}")

            if trade:
                trades.append(trade)

        except Exception as e:
            log(f"[錯誤][主控] {symbol} 操作 {op} 發生例外: {e}\n{traceback.format_exc()}", "ERROR")

    return trades
