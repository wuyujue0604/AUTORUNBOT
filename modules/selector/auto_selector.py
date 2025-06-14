# auto_selector.py
# 幣種選擇器主模組（支援盤型+方向決策+通知整合）

import os
import sys
import json
from typing import List, Dict
from datetime import datetime

# === 設定專案根目錄 ===
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# === 匯入模組 ===
from modules.selector.indicator_utils import (
    calc_rsi_score, calc_macd_score, calc_kdj_score,
    calc_boll_score, calc_volume_score, detect_market_mode
)
from modules.okx_client import get_all_tickers, get_kline_1m
from modules.utils.discord_notifier import write_notification
from config.config import get_runtime_config

# === 載入策略設定 ===
def load_strategy_config() -> dict:
    config_path = os.path.join(PROJECT_ROOT, "config/strategy_config.json")
    with open(config_path, "r") as f:
        return json.load(f)

# === 初篩條件 ===
def is_qualified(ticker: dict, thresholds: dict, debug: bool) -> bool:
    try:
        if not ticker.get("instId", "").endswith("USDT-SWAP"):
            if debug:
                print(f"[篩選] {ticker.get('instId', 'unknown')} 非 USDT-SWAP 合約")
            return False

        vol_usdt = float(ticker.get("volCcy24h", 0))
        last_price = float(ticker.get("last", 0))
        high = float(ticker.get("high24h", 0))
        low = float(ticker.get("low24h", 0))
        price_range_ratio = (high - low) / last_price if last_price > 0 else 0

        if debug:
            print(f"[篩選] {ticker['instId']} | 成交量: {vol_usdt}, 價格: {last_price}, 波動率: {price_range_ratio:.2%}")

        return (
            vol_usdt >= thresholds["MIN_VOLUME_USDT"] and
            last_price >= thresholds["MIN_PRICE"] and
            price_range_ratio >= thresholds["MIN_PRICE_RANGE_RATIO"]
        )
    except Exception as e:
        print(f"[篩選失敗] {ticker.get('instId', 'unknown')}：{e}")
        return False

# === 評分每個標的 ===
def evaluate_symbol(symbol: str, klines: List[Dict], strategy_conf: dict, direction: str, debug: bool) -> dict:
    weights = strategy_conf["weights"]
    params = strategy_conf.get("params", {})

    rsi = calc_rsi_score(klines, period=params.get("RSI_PERIOD"))
    macd = calc_macd_score(klines, fast=params.get("MACD_FAST"), slow=params.get("MACD_SLOW"))
    kdj = calc_kdj_score(klines)
    boll = calc_boll_score(klines)
    vol = calc_volume_score(klines)

    total = (
        rsi * weights.get("RSI", 0) +
        macd * weights.get("MACD", 0) +
        kdj * weights.get("KDJ", 0) +
        boll * weights.get("BOLL", 0) +
        vol * weights.get("Volume", 0)
    )

    if debug:
        print(f"[評分] {symbol} ➜ RSI: {rsi:.2f}, MACD: {macd:.2f}, KDJ: {kdj:.2f}, BOLL: {boll:.2f}, VOL: {vol:.2f} ➜ 總分: {total:.2f} ➜ 方向: {direction}")

    return {
        "symbol": symbol,
        "score": round(total, 4),
        "direction": direction,
        "indicators": {
            "RSI": round(rsi, 3),
            "MACD": round(macd, 3),
            "KDJ": round(kdj, 3),
            "BOLL": round(boll, 3),
            "Volume": round(vol, 3)
        }
    }

# === 加分模組（記憶機制）===
def apply_confidence_boost(results: List[Dict], config: dict, debug: bool) -> None:
    boost_conf = config.get("CONFIDENCE_BOOST", {})
    if not boost_conf.get("ENABLED", False):
        return

    window_size = boost_conf.get("WINDOW_SIZE", 5)
    min_occurrence = boost_conf.get("MIN_OCCURRENCE", 2)
    boost_per_hit = boost_conf.get("BOOST_PER_HIT", 0.05)

    buffer_path = os.path.join(PROJECT_ROOT, "output/history_buffer.json")
    if not os.path.exists(buffer_path):
        return

    with open(buffer_path, "r") as f:
        buffer = json.load(f)

    symbol_counts = {}
    for round_data in buffer[-window_size:]:
        for s in round_data:
            symbol_counts[s] = symbol_counts.get(s, 0) + 1

    for item in results:
        symbol = item["symbol"]
        count = symbol_counts.get(symbol, 0)
        if count >= min_occurrence:
            bonus = round(count * boost_per_hit, 4)
            item["score"] = round(item["score"] + bonus, 4)
            item["boost_info"] = {"boosted_by": count, "bonus": bonus}
            if debug:
                print(f"[加分] {symbol} 出現 {count} 次 ➜ 加分 {bonus:.4f} ➜ 最終分數 {item['score']}")

# === 更新選幣歷史 ===
def update_history(results: List[Dict], top_n: int):
    buffer_path = os.path.join(PROJECT_ROOT, "output/history_buffer.json")
    os.makedirs(os.path.dirname(buffer_path), exist_ok=True)

    history = []
    if os.path.exists(buffer_path):
        with open(buffer_path, "r") as f:
            try:
                history = json.load(f)
            except:
                history = []

    this_round = [r["symbol"] for r in results[:top_n]]
    history.append(this_round)
    history = history[-20:]

    with open(buffer_path, "w") as f:
        json.dump(history, f, indent=2)

# === 主選幣流程 ===
def select_top_symbols() -> List[Dict]:
    runtime_config = get_runtime_config()
    debug = runtime_config.get("debug_mode", False)

    strategy_config = load_strategy_config()
    top_n = strategy_config["TOP_N"]
    thresholds = strategy_config["INITIAL_FILTER"]
    strategies = strategy_config["STRATEGIES"]

    tickers = get_all_tickers()
    if not tickers:
        print("⚠️ 無法獲取交易對資料")
        return []

    print(f"[初篩] 條件：{thresholds}")
    filtered = [t for t in tickers if is_qualified(t, thresholds, debug)]
    print(f"[初篩] 符合條件：{len(filtered)}")

    qualified = filtered[:top_n]
    results = []

    for ticker in qualified:
        symbol = ticker["instId"]
        klines = get_kline_1m(symbol, limit=30)
        if not klines or len(klines) < 20:
            if debug:
                print(f"⚠️ {symbol} 缺少 K 線")
            continue

        market_mode = detect_market_mode(klines)
        macd_score = calc_macd_score(klines)
        direction = "long" if macd_score >= 0.5 else "short"
        strategy_key = f"{market_mode}-{direction}"

        market_mode_ch = {
            "trend": "趨勢盤",
            "range": "區間震盪"
        }.get(market_mode, "未知")

        print(f"[盤型] {symbol} ➜ {market_mode_ch}（{market_mode}） | 方向：{direction}")

        if strategy_key not in strategies:
            print(f"⚠️ 找不到策略設定：{strategy_key}，跳過")
            continue

        strategy = strategies[strategy_key]
        result = evaluate_symbol(symbol, klines, strategy, direction, debug)
        result["strategy"] = market_mode
        result["strategy_key"] = strategy_key
        results.append(result)

    apply_confidence_boost(results, strategy_config, debug)
    print(f"[完成] 評分幣種：{len(results)}")

    top_symbols = sorted(results, key=lambda x: x["score"], reverse=True)[:top_n]
    update_history(top_symbols, top_n)

    # === 寫入通知 ===
    for r in top_symbols:
        write_notification({
            "symbol": r["symbol"],
            "action": "select",
            "result": "success",
            "strategy_key": r["strategy_key"],
            "score": r["score"],
            "level": "info",
            "risk_level": "low"
        })

    return top_symbols

# === 儲存選幣結果 ===
def save_selection_results(results: List[Dict]):
    output_dir = os.path.join(PROJECT_ROOT, "output")
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    output_path = os.path.join(output_dir, f"selected_coins_{ts}.json")
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"[輸出] 選幣結果已儲存：{output_path}")

# === 執行主流程 ===
if __name__ == "__main__":
    results = select_top_symbols()
    if not results:
        print("⚠️ 無符合條件幣種")
    else:
        save_selection_results(results)