# modules/trader/long_trader.py
# 多單建倉模組：含 run_long_trader()、加倉 / 減倉 / 平倉 與風控（支援浮動 TP/SL、統一通知記錄、資金控管）

import os
import sys
import json
from datetime import datetime
from dotenv import load_dotenv

# === 設定與修正路徑 ===
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from modules.okx_client import (
    set_leverage, place_order, get_min_order_amount,
    get_balance, get_latest_price
)
from modules.utils.position_manager import (
    save_position, load_positions, update_position, delete_position
)
from modules.risk.risk_manager import get_allocated_budget
from modules.utils.discord_notifier import send_discord_notification
from config.config import get_runtime_config

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# === 建倉邏輯：計算槓桿與投入比例 ===
def calculate_investment_ratio(confidence, config):
    min_r = float(config.get("MIN_SINGLE_POSITION_RATIO", 0.01))
    max_r = float(config.get("MAX_SINGLE_POSITION_RATIO", 0.15))
    return max(min_r, min((confidence / 100.0) * max_r, max_r))

def calculate_leverage(confidence, config):
    min_l = int(config.get("MIN_LEVERAGE", 3))
    max_l = int(config.get("MAX_LEVERAGE", 10))
    lev = int(min_l + (confidence / 100.0) * (max_l - min_l))
    return min(max(lev, min_l), max_l)

# === 建倉函式（open_long_position）會在 run_long_trader 使用 ===
def open_long_position(symbol, score, strategy_key, config):
    try:
        leverage = calculate_leverage(score, config)
        investment_ratio = calculate_investment_ratio(score, config)
        max_budget = get_allocated_budget("long", config)
        sl_ratio = config.get("STRATEGIES", {}).get(strategy_key, {}).get("SL_RATIO", 0.01)

        invest_amount = max_budget * investment_ratio
        reserved = invest_amount * sl_ratio
        total_required = invest_amount + reserved

        usdt_balance = get_balance()
        if usdt_balance < total_required:
            msg = f"⚠️ 餘額不足（需 {total_required:.2f} USDT，實際僅有 {usdt_balance:.2f}），需保留止損資金"
            print(msg)
            send_discord_notification({
                "symbol": symbol, "action": "open", "result": "fail",
                "message": msg, "level": "warn", "risk_level": "high",
                "strategy_key": strategy_key
            })
            return

        latest_price = get_latest_price(symbol)
        if not latest_price:
            msg = f"❌ 無法取得 {symbol} 最新價格"
            print(msg)
            send_discord_notification({
                "symbol": symbol, "action": "open", "result": "fail",
                "message": msg, "level": "alert", "risk_level": "high",
                "strategy_key": strategy_key
            })
            return

        size = round(invest_amount / latest_price, 4)
        min_amt, min_size = get_min_order_amount(symbol)
        if invest_amount < min_amt or size < min_size:
            msg = f"⚠️ 不符合最小下單限制（最少 {min_amt} USDT 或 {min_size} 張）"
            print(msg)
            send_discord_notification({
                "symbol": symbol, "action": "open", "result": "fail",
                "message": msg, "level": "warn", "risk_level": "medium",
                "strategy_key": strategy_key
            })
            return

        if config.get("test_mode", True):
            msg = f"🧪 模擬建倉 {symbol} 多單：投入 {invest_amount:.2f} USDT，槓桿 {leverage} 倍"
            print(msg)
        else:
            lev_resp = set_leverage(symbol, leverage, "isolated", "long")
            if lev_resp.get("code") != "0":
                msg = f"❌ 設定槓桿失敗：{lev_resp.get('msg')}"
                print(msg)
                send_discord_notification({
                    "symbol": symbol, "action": "open", "result": "fail",
                    "message": msg, "level": "alert", "risk_level": "high",
                    "strategy_key": strategy_key
                })
                return

            order = place_order(symbol, "buy", size, mgn_mode="isolated")
            if order.get("code") != "0":
                msg = f"❌ 下單失敗：{order.get('msg')}"
                print(msg)
                send_discord_notification({
                    "symbol": symbol, "action": "open", "result": "fail",
                    "message": msg, "level": "alert", "risk_level": "high",
                    "strategy_key": strategy_key
                })
                return

        save_position({
            "symbol": symbol,
            "direction": "long",
            "entry_price": latest_price,
            "size": size,
            "confidence": score,
            "strategy": strategy_key.split("-")[0],
            "strategy_key": strategy_key,
            "highest": latest_price,
            "lowest": latest_price,
            "timestamp": datetime.now().isoformat()
        })

        msg = f"✅ 建倉成功：{symbol} 多單 {size} 張，槓桿 {leverage} 倍，投入 {invest_amount:.2f} USDT"
        print(msg)
        send_discord_notification({
            "symbol": symbol, "action": "open", "result": "success",
            "message": msg, "level": "info", "risk_level": "medium",
            "strategy_key": strategy_key, "price": latest_price, "size": size
        })

    except Exception as e:
        msg = f"⚠️ 建倉錯誤：{str(e)}"
        print(msg)
        send_discord_notification({
            "symbol": symbol, "action": "open", "result": "fail",
            "message": msg, "level": "alert", "risk_level": "high",
            "strategy_key": strategy_key
        })

# === 加倉多單 ===
def add_long_position(symbol, config):
    pos = next((p for p in load_positions() if p["symbol"] == symbol and p["direction"] == "long"), None)
    if not pos:
        print(f"⚠️ 找不到持倉：{symbol}")
        return

    try:
        price = get_latest_price(symbol)
        if not price:
            print(f"❌ 無法取得價格：{symbol}")
            return

        strategy_key = pos["strategy_key"]
        sl_ratio = config.get("STRATEGIES", {}).get(strategy_key, {}).get("SL_RATIO", 0.01)
        add_ratio = config.get("ADD_POSITION_RATIO", 0.05)
        max_budget = get_allocated_budget("long", config)

        add_amount = max_budget * add_ratio
        reserved = add_amount * sl_ratio
        total_required = add_amount + reserved

        if get_balance() < total_required:
            print(f"⚠️ 餘額不足，加倉需保留止損資金")
            return

        add_size = round(add_amount / price, 4)
        max_size = pos["size"] * 2
        if pos["size"] + add_size > max_size:
            add_size = max_size - pos["size"]

        if add_size <= 0:
            print(f"⚠️ 已達加倉上限或張數不足")
            return

        min_amt, min_size = get_min_order_amount(symbol)
        if add_amount < min_amt or add_size < min_size:
            print(f"⚠️ 加倉張數不足")
            return

        order = place_order(symbol, "buy", add_size, mgn_mode="isolated")
        if order.get("code") == "0":
            pos["size"] += add_size
            pos["highest"] = max(pos["highest"], price)
            pos["lowest"] = min(pos["lowest"], price)
            pos["timestamp"] = datetime.now().isoformat()
            update_position(symbol, pos)
            print(f"✅ 加倉成功：{symbol} ➜ 倉位 {pos['size']} 張")
        else:
            print(f"❌ 加倉失敗：{order.get('msg')}")

    except Exception as e:
        print(f"❌ 加倉錯誤：{str(e)}")

# === 減倉多單 ===
def reduce_long_position(symbol, config):
    pos = next((p for p in load_positions() if p["symbol"] == symbol and p["direction"] == "long"), None)
    if not pos:
        print(f"⚠️ 找不到持倉：{symbol}")
        return

    try:
        ratio = config.get("REDUCE_POSITION_RATIO", 0.5)
        reduce_size = round(pos["size"] * ratio, 4)
        if reduce_size <= 0:
            print(f"⚠️ 減倉數量異常")
            return

        order = place_order(symbol, "sell", reduce_size, mgn_mode="isolated")
        if order.get("code") == "0":
            pos["size"] -= reduce_size
            price = get_latest_price(symbol)
            pos["highest"] = max(pos["highest"], price)
            pos["lowest"] = min(pos["lowest"], price)
            pos["timestamp"] = datetime.now().isoformat()
            update_position(symbol, pos)
            print(f"✅ 減倉成功：{symbol} ➜ 剩餘 {pos['size']} 張")
        else:
            print(f"❌ 減倉失敗：{order.get('msg')}")

    except Exception as e:
        print(f"❌ 減倉錯誤：{str(e)}")

# === 平倉多單 ===
def close_long_position(symbol, config):
    pos = next((p for p in load_positions() if p["symbol"] == symbol and p["direction"] == "long"), None)
    if not pos:
        print(f"⚠️ 無持倉可平倉：{symbol}")
        return

    try:
        order = place_order(symbol, "sell", pos["size"], mgn_mode="isolated")
        if order.get("code") == "0":
            delete_position(pos)
            print(f"✅ 平倉成功：{symbol} 多單已出場")
        else:
            print(f"❌ 平倉失敗：{order.get('msg')}")
    except Exception as e:
        print(f"❌ 平倉錯誤：{str(e)}")

# === 主執行函式：讀取 output/selected.json 執行建倉 ===
def run_long_trader():
    config = get_runtime_config()
    path = os.path.join(PROJECT_ROOT, "output/selected.json")

    if not os.path.exists(path):
        print("❌ 無選幣結果，略過建倉")
        return

    with open(path, "r") as f:
        selected = json.load(f)

    long_targets = [s for s in selected if s.get("direction") == "long"]
    if not long_targets:
        print("ℹ️ 無需建倉多單標的")
        return

    print(f"📥 開始建倉多單：共 {len(long_targets)} 檔")
    for coin in long_targets:
        symbol = coin["symbol"]
        score = coin["score"]
        strategy_key = coin["strategy_key"]
        open_long_position(symbol, score, strategy_key, config)