import os
import json
import time
import traceback
from config import get_runtime_config, debug_mode
from logger import log
import okx_client
import state_manager
import order_executor

# 全域快取及上次讀取時間，用於避免頻繁磁碟I/O
_cache_latest_selection = None
_last_load_time = 0


def load_latest_selection(path="json_results/latest_selection.json"):
    """
    載入最新選幣結果，確保回傳字典格式，即使檔案為空、格式錯誤也不崩潰。
    """
    if not os.path.exists(path):
        log(f"[警告] 找不到選幣結果檔: {path}", level="WARN")
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                # 過濾掉非字典或無symbol欄位資料
                return {x["symbol"]: x for x in data if isinstance(x, dict) and "symbol" in x}
            elif isinstance(data, dict):
                return data
            else:
                log(f"[警告] 選幣結果檔格式異常，非list/dict，返回空dict", level="WARN")
                return {}
    except json.JSONDecodeError as e:
        log(f"[錯誤] 解析選幣結果JSON失敗: {e}", level="ERROR")
        return {}
    except Exception as e:
        log(f"[錯誤] 載入選幣結果異常: {e}\n{traceback.format_exc()}", level="ERROR")
        return {}


def load_latest_selection_cached(path="json_results/latest_selection.json"):
    """
    載入選幣結果並快取，避免頻繁磁碟I/O，快取有效期5秒。
    """
    global _cache_latest_selection, _last_load_time
    now = time.time()
    if now - _last_load_time > 5 or _cache_latest_selection is None:
        _cache_latest_selection = load_latest_selection(path)
        _last_load_time = now
    return _cache_latest_selection


def check_take_profit_stop_loss():
    """
    統一停利停損判斷，根據收益額和收益率觸發平倉。
    加入投入資金最小判斷，避免浮點誤差影響判斷。
    """
    config = get_runtime_config()
    take_profit_value = config.get("TAKE_PROFIT_VALUE", 0.2)    # 調整成跟config.json一致
    stop_loss_ratio = config.get("STOP_LOSS_RATIO", -0.05)       # 停損收益率門檻 (-5%)

    positions = state_manager.load_position_state()
    if not positions:
        if debug_mode():
            log("[DEBUG] 無持倉，跳過停利停損檢查", level="DEBUG")
        return

    for symbol, pos in positions.items():
        direction = pos.get("direction")
        entry_price = pos.get("price")
        contracts = pos.get("contracts")

        if not direction or not entry_price or contracts <= 0:
            log(f"[警告] {symbol} 持倉資料不完整，略過", level="WARN")
            continue

        current_price = okx_client.get_market_price(symbol)
        if not current_price:
            log(f"[錯誤] 無法取得 {symbol} 市價，略過", level="ERROR")
            continue

        pnl = 0
        if direction == "buy":
            pnl = (current_price - entry_price) * contracts
        else:
            pnl = (entry_price - current_price) * contracts

        invested_amount = entry_price * contracts
        # 最小投入資金門檻，避免浮點誤差導致誤判
        if invested_amount < 1e-6:
            pnl_ratio = 0
        else:
            pnl_ratio = pnl / invested_amount

        log(f"[DEBUG] {symbol} 收益額: {pnl:.4f} USDT, 收益率: {pnl_ratio:.4%}", level="INFO")

        # 加容錯微調
        if pnl >= take_profit_value - 1e-8 or pnl_ratio <= stop_loss_ratio:
            log(f"[INFO] {symbol} 達停利停損條件，觸發平倉", level="INFO")
            entry = {"symbol": symbol}
            success = order_executor.try_close_position(entry, config)
            if not success:
                log(f"[錯誤] {symbol} 平倉下單失敗，待下次重試", level="ERROR")


def run_position_monitor():
    """
    持倉監控主流程：
    1. 停利停損判斷，符合條件觸發平倉
    2. 持倉同步管理，非最新選幣名單持倉減倉或平倉
    """
    config = get_runtime_config()

    # 執行停利停損檢查
    check_take_profit_stop_loss()

    # 載入目前持倉狀態
    positions = state_manager.load_position_state()
    if not positions:
        if debug_mode():
            log("[DEBUG] 無持倉，跳過持倉同步", level="DEBUG")
        return

    # 載入最新選幣結果（快取版）
    latest_selection = load_latest_selection_cached()

    # 持倉同步檢查，每個持倉依最新選幣結果判斷處理
    for symbol, pos in list(positions.items()):
        try:
            handled = order_executor.handle_removed_position(symbol, pos, latest_selection, config)
            if not handled:
                log(f"[警告] {symbol} 持倉同步處理失敗，待下次重試", level="WARN")
        except Exception as e:
            log(f"[錯誤] 處理 {symbol} 持倉同步發生例外: {e}\n{traceback.format_exc()}", level="ERROR")
