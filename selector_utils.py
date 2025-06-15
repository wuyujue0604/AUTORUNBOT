import os
import json
import time
import pandas as pd
import requests
import sqlite3
from config import get_runtime_config, debug_mode, test_mode
from logger import log
from okx_client import get_ohlcv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
RESULT_DIR = os.path.join(BASE_DIR, "json_results")
os.makedirs(RESULT_DIR, exist_ok=True)

COOLDOWN_DB_PATH = os.path.join(RESULT_DIR, "cooldown_pool.db")
BLOCKED_DB_PATH = os.path.join(RESULT_DIR, "blocked_symbols.db")

def get_db_connection(db_path):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_cooldown_db():
    with get_db_connection(COOLDOWN_DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cooldown_pool (
                symbol TEXT PRIMARY KEY,
                timestamp INTEGER
            )
        ''')
        conn.commit()

def init_blocked_db():
    with get_db_connection(BLOCKED_DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS blocked_symbols (
                symbol TEXT PRIMARY KEY
            )
        ''')
        conn.commit()

def get_all_usdt_swap_symbols():
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
            result_path = os.path.join(RESULT_DIR, "instruments_list.json")
            _safe_save_list(symbols, result_path)
            log(f"[INFO] 已儲存合約列表到 {result_path}")
        except Exception as e:
            log(f"[錯誤] 儲存合約列表失敗: {e}", "ERROR")

    return symbols

def _safe_save_list(data_list, path):
    dirpath = os.path.dirname(path)
    if not os.path.exists(dirpath):
        os.makedirs(dirpath)
    if not isinstance(data_list, list):
        raise ValueError("只能儲存 list 結構")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data_list, f, ensure_ascii=False, indent=2)

def load_latest_selection(path="json_results/latest_selection.json"):
    # 這裡你可依需求保留或改DB讀取
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            elif isinstance(data, list):
                return {x["symbol"]: x for x in data if isinstance(x, dict) and "symbol" in x}
            return {}
    except Exception as e:
        log(f"[錯誤] 讀取選幣結果失敗: {e}", "ERROR")
        return {}

def filter_candidates_by_position(candidates, position_state, config):
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

def pass_pre_filter(symbol, ohlcv_df, config):
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

def is_symbol_cooled_down(symbol, cooldown_pool, config):
    cooldown = cooldown_pool.get(symbol)
    if not cooldown:
        return False
    duration = config.get("COOLDOWN_DURATION", 3600)
    return (int(time.time()) - cooldown.get("timestamp", 0)) < duration

def is_symbol_blocked(symbol, config):
    # 改成從 blocked_symbols DB 讀
    blocked_set = load_blocked_symbols_db()
    return symbol in blocked_set

# === 新增函式從資料庫載入冷卻池 ===
def load_cooldown_pool_db():
    init_cooldown_db()
    cooldown = {}
    try:
        with get_db_connection(COOLDOWN_DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT symbol, timestamp FROM cooldown_pool")
            rows = cursor.fetchall()
            for symbol, ts in rows:
                cooldown[symbol] = {"timestamp": ts}
    except Exception as e:
        log(f"[錯誤] 讀取冷卻池資料庫失敗: {e}", level="ERROR")
    return cooldown

def init_cooldown_db():
    with get_db_connection(COOLDOWN_DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cooldown_pool (
                symbol TEXT PRIMARY KEY,
                timestamp INTEGER
            )
        ''')
        conn.commit()

# === 新增函式從資料庫載入封鎖列表 ===
def load_blocked_symbols_db():
    init_blocked_db()
    blocked = set()
    try:
        with get_db_connection(BLOCKED_DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT symbol FROM blocked_symbols")
            rows = cursor.fetchall()
            blocked = {row[0] for row in rows}
    except Exception as e:
        log(f"[錯誤] 讀取封鎖列表資料庫失敗: {e}", level="ERROR")
    return blocked

def init_blocked_db():
    with get_db_connection(BLOCKED_DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS blocked_symbols (
                symbol TEXT PRIMARY KEY
            )
        ''')
        conn.commit()

# 批次取得 K 線資料
def get_ohlcv_batch(symbol_list, timeframe="1h", limit=100, config=None):
    result = {}
    for symbol in symbol_list:
        try:
            df = get_ohlcv(symbol, timeframe, limit)
            if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
                df = df.iloc[:, :6]
                result[symbol] = df
                if debug_mode():
                    log(f"[DEBUG] 取得 K 線: {symbol} 共 {len(df)} 筆")
            else:
                if debug_mode():
                    log(f"[DEBUG] {symbol} K 線資料無效或空，略過")
        except Exception as e:
            log(f"[錯誤] 無法取得 {symbol} 的 K 線: {e}", "ERROR")
    return result

# 修改 load_symbol_locks() 改用資料庫讀取
def load_symbol_locks():
    cooldown = load_cooldown_pool_db()
    blocked = load_blocked_symbols_db()
    return cooldown, blocked
