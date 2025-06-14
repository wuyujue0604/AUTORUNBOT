import os
import json
import time
import pandas as pd
import requests
from config import get_runtime_config, debug_mode
from logger import log
from okx_client import get_ohlcv

# === ✅ 取得所有 USDT 永續合約（並根據 24H 成交額過濾）===
def get_all_usdt_swap_symbols():
    """
    從 OKX API 取得所有 USDT 永續合約，並依照24小時成交額過濾。
    """
    config = get_runtime_config()
    min_volume = config.get("MIN_24H_VOLUME_USDT", 100000000)

    url = "https://www.okx.com/api/v5/market/tickers?instType=SWAP"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        tickers = data.get("data", [])
    except Exception as e:
        log(f"[錯誤] 無法取得 ticker 資料: {e}", "ERROR")
        return []

    symbols = []
    for ticker in tickers:
        instId = ticker.get("instId", "")
        vol = float(ticker.get("volCcy24h", 0))
        if instId.endswith("-USDT-SWAP") and vol >= min_volume:
            symbols.append(instId)

    if debug_mode():
        log(f"[DEBUG] 取得 USDT-SWAP 合約共 {len(symbols)} 檔")
        try:
            result_path = os.path.join(os.path.dirname(__file__), "json_results", "instruments_list.json")
            _safe_save_list(symbols, result_path)
            log(f"[INFO] 已儲存合約列表到 {result_path}")
        except Exception as e:
            log(f"[錯誤] 儲存合約列表失敗: {e}", "ERROR")

    return symbols

def _safe_save_list(data_list, path):
    """
    安全儲存 list 到 json，確保型態正確。
    """
    dirpath = os.path.dirname(path)
    if not os.path.exists(dirpath):
        os.makedirs(dirpath)
    if not isinstance(data_list, list):
        raise ValueError("只能儲存 list 結構")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data_list, f, ensure_ascii=False, indent=2)

# === 防呆載入最新選幣結果（dict格式，list會轉dict，空也安全）===
def load_latest_selection(path="json_results/latest_selection.json"):
    """
    載入最新選幣結果，無論原檔為 list/dict，都保證回傳 dict。
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            elif isinstance(data, list):
                # 將 list 轉為 {symbol: item}
                return {x["symbol"]: x for x in data if isinstance(x, dict) and "symbol" in x}
            return {}
    except Exception as e:
        log(f"[錯誤] 讀取選幣結果失敗: {e}", "ERROR")
        return {}

# === 簡單預篩條件 ===
def pass_pre_filter(symbol, ohlcv_df, config):
    """
    判斷 K 線資料是否通過預篩條件，並顯示詳細原因。
    """
    if ohlcv_df is None or len(ohlcv_df) < 10:
        if debug_mode():
            log(f"[DEBUG][預篩] {symbol} K線資料不足，略過")
        return False

    vol_std = ohlcv_df['volume'].std()
    if vol_std < config.get("MIN_VOL_STD", 1):
        if debug_mode():
            log(f"[DEBUG][預篩] {symbol} 成交量標準差過低（{vol_std:.2f} < {config.get('MIN_VOL_STD', 1)}），略過")
        return False

    amplitude = ((ohlcv_df['high'] - ohlcv_df['low']) / ohlcv_df['close']).mean()
    if amplitude < config.get("MIN_CANDLE_AMPLITUDE", 0.01):
        if debug_mode():
            log(f"[DEBUG][預篩] {symbol} K線平均振幅過低（{amplitude:.4f} < {config.get('MIN_CANDLE_AMPLITUDE', 0.01)}），略過")
        return False

    if debug_mode():
        log(f"[DEBUG][預篩] 符合標準: {symbol} 成交量標準差 {vol_std:.2f}, 平均振幅 {amplitude:.4f}")

    return True

# === 判斷是否在冷卻中 ===
def is_symbol_cooled_down(symbol, cooldown_pool, config):
    """
    判斷該 symbol 是否還在冷卻池時間內。
    """
    cooldown = cooldown_pool.get(symbol)
    if not cooldown:
        return False
    duration = config.get("COOLDOWN_DURATION", 3600)
    return (int(time.time()) - cooldown.get("timestamp", 0)) < duration

# === 判斷是否為封鎖幣種（黑名單）===
def is_symbol_blocked(symbol, config):
    """
    判斷是否在黑名單中。
    """
    blocked_list = config.get("BLOCKED_SYMBOLS", [])
    return symbol in blocked_list

# === 批次取得 K 線資料 ===
def get_ohlcv_batch(symbol_list, timeframe="1h", limit=100, config=None):
    """
    批次取得所有 symbol 的 K 線資料，回傳 dict 格式。
    """
    result = {}
    for symbol in symbol_list:
        try:
            df = get_ohlcv(symbol, timeframe, limit)
            if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
                df = df.iloc[:, :6]  # 保留 open, high, low, close, volume, ts
                result[symbol] = df
                if debug_mode():
                    log(f"[DEBUG] 取得 K 線: {symbol} 共 {len(df)} 筆")
            else:
                if debug_mode():
                    log(f"[DEBUG] {symbol} K 線資料無效或空，略過")
        except Exception as e:
            log(f"[錯誤] 無法取得 {symbol} 的 K 線: {e}", "ERROR")
    return result