import time
import traceback
from config import get_runtime_config, debug_mode
from logger import log
import okx_client
import order_executor
from state_manager import load_position_state, load_latest_selection_db

def check_take_profit_stop_loss():
    """
    停利停損判斷函式：
    依據持倉收益額與收益率判斷是否符合停利或停損條件，符合條件則嘗試平倉。
    """
    config = get_runtime_config()
    take_profit_value = config.get("TAKE_PROFIT_VALUE", 0.2)    # 停利值
    stop_loss_ratio = config.get("STOP_LOSS_RATIO", -0.05)       # 停損比率

    positions = load_position_state()
    if not positions:
        if debug_mode():
            log("[DEBUG] 無持倉，跳過停利停損檢查", level="DEBUG")
        return

    for symbol, pos in positions.items():
        direction = pos.get("direction")
        entry_price = pos.get("price")
        contracts = pos.get("contracts", 0)

        # 基本資料完整性檢查
        if not direction or not entry_price or contracts <= 0:
            log(f"[警告] {symbol} 持倉資料不完整，略過", level="WARN")
            continue

        current_price = okx_client.get_market_price(symbol)
        if current_price is None or current_price <= 0:
            log(f"[錯誤] 無法取得 {symbol} 市價，略過", level="ERROR")
            continue

        # 計算浮動盈虧
        pnl = (current_price - entry_price) * contracts if direction == "buy" else (entry_price - current_price) * contracts
        invested_amount = entry_price * contracts
        pnl_ratio = 0 if invested_amount < 1e-6 else pnl / invested_amount

        log(f"[INFO] {symbol} 盈利: {pnl:.4f} USDT, 盈利率: {pnl_ratio:.4%}", level="INFO")

        # 判斷是否觸發停利停損
        if pnl >= take_profit_value or pnl_ratio <= stop_loss_ratio:
            log(f"[INFO] {symbol} 達停利停損條件，嘗試平倉", level="INFO")
            entry = {"symbol": symbol}
            success = order_executor.try_close_position(entry, config)
            if not success:
                log(f"[錯誤] {symbol} 平倉下單失敗，待下次重試", level="ERROR")

def run_position_monitor():
    """
    持倉監控主流程：
    1. 停利停損判斷
    2. 持倉同步根據最新選幣結果調整持倉狀態
    """
    config = get_runtime_config()

    # 先進行停利停損檢查
    check_take_profit_stop_loss()

    positions = load_position_state()
    if not positions:
        if debug_mode():
            log("[DEBUG] 無持倉，跳過持倉同步", level="DEBUG")
        return

    latest_selection = load_latest_selection_db()

    for symbol, pos in list(positions.items()):
        try:
            handled = order_executor.handle_removed_position(symbol, pos, latest_selection, config)
            if not handled:
                log(f"[警告] {symbol} 持倉同步處理失敗，待下次重試", level="WARN")
        except Exception as e:
            log(f"[錯誤] 處理 {symbol} 持倉同步發生例外: {e}\n{traceback.format_exc()}", level="ERROR")

if __name__ == "__main__":
    run_position_monitor()
