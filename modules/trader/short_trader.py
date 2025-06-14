# modules/trader/short_trader.py
# 空單建倉模組：支援資金風控、多空比例調整、浮動止盈止損、統一通知模組寫入

import os
import sys
from datetime import datetime
from dotenv import load_dotenv

# === 修正模組路徑 ===
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# === 載入模組 ===
from modules.okx_client import (
    set_leverage, place_order, get_min_order_amount,
    get_balance, get_latest_price
)
from modules.utils.position_manager import (
    save_position, load_positions, update_position, delete_position
)
from modules.utils.discord_notifier import send_discord_notification
from modules.risk.risk_manager import get_allocated_budget
from config.config import get_runtime_config

# === 載入環境變數 ===
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

def calculate_investment_ratio(confidence, config):
    min_r = float(config.get("MIN_SINGLE_POSITION_RATIO", 0.01))
    max_r = float(config.get("MAX_SINGLE_POSITION_RATIO", 0.15))
    return max(min_r, min((confidence / 100.0) * max_r, max_r))

def calculate_leverage(confidence, config):
    min_l = int(config.get("MIN_LEVERAGE", 3))
    max_l = int(config.get("MAX_LEVERAGE", 10))
    lev = int(min_l + (confidence / 100.0) * (max_l - min_l))
    return min(max(lev, min_l), max_l)

# === 建倉空單 ===
def open_short_position(symbol, score, strategy_key, config, short_count=1, long_count=1):
    debug = config.get("debug_mode", False)
    test = config.get("test_mode", True)

    try:
        # 資金與風控
        budget = get_allocated_budget(config, direction="short", short_count=short_count, long_count=long_count)
        invest_ratio = calculate_investment_ratio(score, config)
        invest_amt = budget * invest_ratio

        sl_ratio = config.get("STRATEGIES", {}).get(strategy_key, {}).get("SL_RATIO", 0.01)
        reserve = invest_amt * sl_ratio
        if get_balance() < invest_amt + reserve:
            send_discord_notification({
                "symbol": symbol, "direction": "short", "action": "open", "strategy_key": strategy_key,
                "result": "fail", "message": f"餘額不足（需保留止損資金）", "level": "warn", "risk_level": "high"
            })
            return

        price = get_latest_price(symbol)
        if not price:
            send_discord_notification({
                "symbol": symbol, "direction": "short", "action": "open", "strategy_key": strategy_key,
                "result": "fail", "message": f"無法取得價格", "level": "alert", "risk_level": "high"
            })
            return

        size = round(invest_amt / price, 4)
        min_amt, min_size = get_min_order_amount(symbol)
        if invest_amt < min_amt or size < min_size:
            send_discord_notification({
                "symbol": symbol, "direction": "short", "action": "open", "strategy_key": strategy_key,
                "result": "fail", "message": f"不符合最小下單限制", "level": "warn", "risk_level": "medium"
            })
            return

        if test:
            msg = f"🧪 模擬建倉空單：{symbol} 投入 {invest_amt:.2f}，張數 {size}"
            print(msg)
        else:
            lev = calculate_leverage(score, config)
            lev_resp = set_leverage(symbol, lev, "isolated", "short")
            if lev_resp.get("code") != "0":
                send_discord_notification({
                    "symbol": symbol, "direction": "short", "action": "open", "strategy_key": strategy_key,
                    "result": "fail", "message": f"設定槓桿失敗：{lev_resp.get('msg')}", "level": "alert", "risk_level": "high"
                })
                return

            order = place_order(symbol, "sell", size, mgn_mode="isolated")
            if order.get("code") != "0":
                send_discord_notification({
                    "symbol": symbol, "direction": "short", "action": "open", "strategy_key": strategy_key,
                    "result": "fail", "message": f"下單失敗：{order.get('msg')}", "level": "alert", "risk_level": "high"
                })
                return

        # 儲存倉位
        save_position({
            "symbol": symbol,
            "direction": "short",
            "entry_price": price,
            "size": size,
            "confidence": score,
            "strategy": strategy_key.split("-")[0],
            "strategy_key": strategy_key,
            "highest": price,
            "lowest": price,
            "timestamp": datetime.now().isoformat()
        })

        send_discord_notification({
            "symbol": symbol, "direction": "short", "action": "open", "strategy_key": strategy_key,
            "price": price, "size": size, "result": "success",
            "message": f"✅ 建倉成功：空單 {size} 張，價格 {price}", "level": "info", "risk_level": "medium"
        })

    except Exception as e:
        send_discord_notification({
            "symbol": symbol, "direction": "short", "action": "open", "strategy_key": strategy_key,
            "result": "fail", "message": f"建倉錯誤：{str(e)}", "level": "alert", "risk_level": "high"
        })

# === 加倉空單 ===
def add_short_position(symbol, config):
    pos = next((p for p in load_positions() if p["symbol"] == symbol and p["direction"] == "short"), None)
    if not pos:
        send_discord_notification({
            "symbol": symbol, "direction": "short", "action": "add",
            "result": "fail", "message": "找不到空單持倉", "level": "warn", "risk_level": "medium"
        })
        return

    try:
        price = get_latest_price(symbol)
        if not price:
            send_discord_notification({
                "symbol": symbol, "direction": "short", "action": "add", "strategy_key": pos["strategy_key"],
                "result": "fail", "message": "無法取得價格", "level": "alert", "risk_level": "high"
            })
            return

        strategy_key = pos.get("strategy_key", "default-short")
        sl_ratio = config.get("STRATEGIES", {}).get(strategy_key, {}).get("SL_RATIO", 0.01)
        add_ratio = config.get("ADD_POSITION_RATIO", 0.05)
        usdt_balance = get_balance()
        add_amt = usdt_balance * add_ratio
        reserve = add_amt * sl_ratio

        if usdt_balance < add_amt + reserve:
            send_discord_notification({
                "symbol": symbol, "direction": "short", "action": "add", "strategy_key": strategy_key,
                "result": "fail", "message": "餘額不足，加倉需保留止損資金", "level": "warn", "risk_level": "high"
            })
            return

        add_size = round(add_amt / price, 4)
        max_size = pos["size"] * 2
        if pos["size"] + add_size > max_size:
            add_size = round(max_size - pos["size"], 4)

        if add_size <= 0:
            send_discord_notification({
                "symbol": symbol, "direction": "short", "action": "add", "strategy_key": strategy_key,
                "result": "fail", "message": "已達最大加倉限制", "level": "warn", "risk_level": "medium"
            })
            return

        min_amt, min_size = get_min_order_amount(symbol)
        if add_amt < min_amt or add_size < min_size:
            send_discord_notification({
                "symbol": symbol, "direction": "short", "action": "add", "strategy_key": strategy_key,
                "result": "fail", "message": "金額或張數不足", "level": "warn", "risk_level": "low"
            })
            return

        order = place_order(symbol, "sell", add_size, mgn_mode="isolated")
        if order.get("code") == "0":
            pos["size"] += add_size
            pos["timestamp"] = datetime.now().isoformat()
            update_position(symbol, pos)
            send_discord_notification({
                "symbol": symbol, "direction": "short", "action": "add", "strategy_key": strategy_key,
                "price": price, "size": pos["size"],
                "result": "success", "message": f"加倉成功，新倉位 {pos['size']}", "level": "info", "risk_level": "low"
            })
        else:
            send_discord_notification({
                "symbol": symbol, "direction": "short", "action": "add", "strategy_key": strategy_key,
                "result": "fail", "message": f"加倉失敗：{order.get('msg')}", "level": "alert", "risk_level": "high"
            })

    except Exception as e:
        send_discord_notification({
            "symbol": symbol, "direction": "short", "action": "add", "strategy_key": pos.get("strategy_key"),
            "result": "fail", "message": f"加倉錯誤：{str(e)}", "level": "alert", "risk_level": "high"
        })

        # === 減倉空單 ===
def reduce_short_position(symbol, config):
    pos = next((p for p in load_positions() if p["symbol"] == symbol and p["direction"] == "short"), None)
    if not pos:
        send_discord_notification({
            "symbol": symbol, "direction": "short", "action": "reduce",
            "result": "fail", "message": "找不到空單持倉", "level": "warn", "risk_level": "medium"
        })
        return

    try:
        strategy_key = pos.get("strategy_key", "default-short")
        ratio = config.get("REDUCE_POSITION_RATIO", 0.5)
        reduce_size = round(pos["size"] * ratio, 4)

        if reduce_size <= 0:
            send_discord_notification({
                "symbol": symbol, "direction": "short", "action": "reduce", "strategy_key": strategy_key,
                "result": "fail", "message": "減倉數量異常", "level": "warn", "risk_level": "low"
            })
            return

        order = place_order(symbol, "buy", reduce_size, mgn_mode="isolated")
        if order.get("code") == "0":
            pos["size"] -= reduce_size
            pos["timestamp"] = datetime.now().isoformat()
            update_position(symbol, pos)
            send_discord_notification({
                "symbol": symbol, "direction": "short", "action": "reduce", "strategy_key": strategy_key,
                "size": pos["size"], "price": get_latest_price(symbol),
                "result": "success", "message": f"減倉成功 ➜ 剩餘倉位 {pos['size']}", "level": "info", "risk_level": "low"
            })
        else:
            send_discord_notification({
                "symbol": symbol, "direction": "short", "action": "reduce", "strategy_key": strategy_key,
                "result": "fail", "message": f"減倉失敗：{order.get('msg')}", "level": "alert", "risk_level": "high"
            })
    except Exception as e:
        send_discord_notification({
            "symbol": symbol, "direction": "short", "action": "reduce", "strategy_key": pos.get("strategy_key"),
            "result": "fail", "message": f"減倉錯誤：{str(e)}", "level": "alert", "risk_level": "high"
        })

# === 平倉空單 ===
def close_short_position(symbol, config):
    pos = next((p for p in load_positions() if p["symbol"] == symbol and p["direction"] == "short"), None)
    if not pos:
        send_discord_notification({
            "symbol": symbol, "direction": "short", "action": "close",
            "result": "fail", "message": "找不到空單持倉", "level": "warn", "risk_level": "medium"
        })
        return

    try:
        strategy_key = pos.get("strategy_key", "default-short")
        order = place_order(symbol, "buy", pos["size"], mgn_mode="isolated")
        if order.get("code") == "0":
            delete_position(pos)
            send_discord_notification({
                "symbol": symbol, "direction": "short", "action": "close", "strategy_key": strategy_key,
                "price": get_latest_price(symbol), "size": pos["size"],
                "result": "success", "message": f"✅ 平倉成功：{symbol} 空單已出場", "level": "info", "risk_level": "medium",
                "reason": "MANUAL"
            })
        else:
            send_discord_notification({
                "symbol": symbol, "direction": "short", "action": "close", "strategy_key": strategy_key,
                "result": "fail", "message": f"❌ 平倉失敗：{order.get('msg')}", "level": "alert", "risk_level": "high"
            })
    except Exception as e:
        send_discord_notification({
            "symbol": symbol, "direction": "short", "action": "close", "strategy_key": pos.get("strategy_key"),
            "result": "fail", "message": f"平倉錯誤：{str(e)}", "level": "alert", "risk_level": "high"
        })

# === 測試入口點 ===
if __name__ == "__main__":
    cfg = get_runtime_config()
    open_short_position("BTC-USDT-SWAP", 85, "trend-short", cfg, short_count=2, long_count=1)