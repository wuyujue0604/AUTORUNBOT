import os
import json
import threading
import time
import traceback
import sqlite3
from config import get_runtime_config, debug_mode
from logger import log

# 定義結果資料夾路徑
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
RESULT_DIR = os.path.join(BASE_DIR, "json_results")
os.makedirs(RESULT_DIR, exist_ok=True)

# SQLite DB 路徑設定
STATE_DB_PATH = os.path.join(RESULT_DIR, "state_manager.db")
LATEST_SELECTION_DB_PATH = os.path.join(RESULT_DIR, "latest_selection.db")

# 執行緒鎖，確保多執行緒安全
_lock = threading.Lock()

def get_db_connection(db_path=STATE_DB_PATH):
    """
    取得 SQLite 連線並啟用 WAL 模式提升性能
    """
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    """
    初始化資料庫，建立必要的資料表
    """
    with _lock:
        with get_db_connection(STATE_DB_PATH) as conn:
            cursor = conn.cursor()
            # 建立持倉狀態表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS position_state (
                    symbol TEXT PRIMARY KEY,
                    direction TEXT,
                    contracts INTEGER,
                    price REAL,
                    confidence REAL,
                    add_times INTEGER DEFAULT 0,
                    reduce_times INTEGER DEFAULT 0,
                    extra TEXT DEFAULT '{}'
                )
            ''')
            # 建立交易紀錄表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trade_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    direction TEXT,
                    pnl REAL,
                    operation TEXT,
                    timestamp INTEGER,
                    log_timestamp INTEGER
                )
            ''')
            # 建立保留獲利表（單筆）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS reserved_profit (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    reserved REAL DEFAULT 0
                )
            ''')
            conn.commit()
        # 初始化最新選幣資料庫表
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

# ------------------- 持倉狀態操作 -------------------

_position_cache = None
_position_cache_time = 0

def load_position_state(force_reload=False):
    """
    從資料庫讀取持倉狀態，使用快取避免頻繁 I/O，快取有效期 5 秒
    """
    global _position_cache, _position_cache_time
    now = time.time()
    if not force_reload and _position_cache is not None and (now - _position_cache_time) < 5:
        return _position_cache

    try:
        with _lock:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT symbol, direction, contracts, price, confidence, add_times, reduce_times, extra FROM position_state")
                rows = cursor.fetchall()
    except Exception as e:
        log(f"[錯誤] 讀取持倉狀態失敗: {e}\n{traceback.format_exc()}", level="ERROR")
        return {}

    positions = {}
    for row in rows:
        symbol, direction, contracts, price, confidence, add_times, reduce_times, extra_json = row
        try:
            extra = json.loads(extra_json)
        except Exception:
            extra = {}
        positions[symbol] = {
            "direction": direction,
            "contracts": contracts,
            "price": price,
            "confidence": confidence,
            "add_times": add_times,
            "reduce_times": reduce_times,
            **extra
        }

    _position_cache = positions
    _position_cache_time = now
    return positions

def _save_position_state(positions):
    """
    將持倉狀態寫回資料庫，extra 欄位 JSON 序列化
    """
    with _lock:
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                for symbol, pos in positions.items():
                    extra = {k:v for k,v in pos.items() if k not in ("direction","contracts","price","confidence","add_times","reduce_times")}
                    extra_json = json.dumps(extra)
                    cursor.execute('''
                        INSERT INTO position_state (symbol, direction, contracts, price, confidence, add_times, reduce_times, extra)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(symbol) DO UPDATE SET
                            direction=excluded.direction,
                            contracts=excluded.contracts,
                            price=excluded.price,
                            confidence=excluded.confidence,
                            add_times=excluded.add_times,
                            reduce_times=excluded.reduce_times,
                            extra=excluded.extra
                    ''', (symbol, pos.get("direction"), pos.get("contracts"), pos.get("price"), pos.get("confidence"),
                          pos.get("add_times", 0), pos.get("reduce_times", 0), extra_json))
                conn.commit()
        except Exception as e:
            log(f"[錯誤] 寫入持倉狀態失敗: {e}\n{traceback.format_exc()}", level="ERROR")

def update_position_state(symbol, direction, contracts, price, confidence, extra=None, add=False):
    """
    新增或更新持倉資料
    """
    with _lock:
        positions = load_position_state()
        if symbol not in positions:
            positions[symbol] = {
                "direction": direction,
                "contracts": contracts,
                "price": price,
                "confidence": confidence,
                "add_times": 0,
                "reduce_times": 0
            }
        else:
            if add:
                positions[symbol]["contracts"] += contracts
            else:
                positions[symbol]["contracts"] = contracts
            positions[symbol]["price"] = price
            positions[symbol]["confidence"] = confidence

        if extra:
            positions[symbol].update(extra)

        _save_position_state(positions)
        if debug_mode():
            log(f"[DEBUG] 更新持倉: {symbol} 張數={positions[symbol]['contracts']}", level="DEBUG")

def update_position_after_reduce(symbol, reduced_contracts, new_reduce_times=None):
    """
    減倉後更新持倉數量與減倉次數
    """
    with _lock:
        positions = load_position_state()
        if symbol in positions:
            positions[symbol]["contracts"] -= reduced_contracts
            if new_reduce_times is not None:
                positions[symbol]["reduce_times"] = new_reduce_times
            if positions[symbol]["contracts"] <= 0:
                del positions[symbol]
        _save_position_state(positions)
        if debug_mode():
            log(f"[DEBUG] 減倉後更新持倉: {symbol} 剩餘張數={positions.get(symbol, {}).get('contracts', 0)}", level="DEBUG")

def remove_position(symbol):
    """
    移除指定持倉
    """
    with _lock:
        positions = load_position_state()
        if symbol in positions:
            del positions[symbol]
        _save_position_state(positions)
        if debug_mode():
            log(f"[DEBUG] 移除持倉: {symbol}", level="DEBUG")

def get_position_state(symbol):
    """
    取得指定持倉詳細資料
    """
    positions = load_position_state()
    return positions.get(symbol)

# ------------------ 交易紀錄相關 --------------------

def record_trade_log(data):
    """
    新增一筆交易紀錄到 trade_logs 表，會補 timestamp 與 log_timestamp
    """
    dirpath = os.path.dirname(STATE_DB_PATH)
    if not os.path.exists(dirpath):
        os.makedirs(dirpath)
        if debug_mode():
            log(f"[DEBUG] 建立資料夾: {dirpath}", level="DEBUG")

    if "timestamp" not in data:
        data["timestamp"] = int(time.time())
    if "log_timestamp" not in data:
        data["log_timestamp"] = int(time.time())

    with _lock:
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO trade_logs (symbol, direction, pnl, operation, timestamp, log_timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (data.get("symbol"), data.get("direction"), data.get("pnl", 0), data.get("operation"),
                      data["timestamp"], data["log_timestamp"]))
                conn.commit()
            if debug_mode():
                log(f"[記錄成功] 新增交易紀錄: {data}", level="DEBUG")
        except Exception as e:
            log(f"[錯誤] 新增交易紀錄失敗: {e}\n{traceback.format_exc()}", level="ERROR")

# ------------------ 保留獲利相關 --------------------

def add_profit(amount):
    """
    累加保留獲利金額
    """
    with _lock:
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT OR IGNORE INTO reserved_profit (id, reserved) VALUES (1, 0)")
                cursor.execute("UPDATE reserved_profit SET reserved = reserved + ? WHERE id = 1", (amount,))
                conn.commit()
            if debug_mode():
                log(f"[DEBUG] 累加保留獲利: +{amount}", level="DEBUG")
        except Exception as e:
            log(f"[錯誤] 累加保留獲利失敗: {e}\n{traceback.format_exc()}", level="ERROR")

def get_reserved_profit():
    """
    取得目前保留獲利，找不到時回傳 0
    """
    with _lock:
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT reserved FROM reserved_profit WHERE id = 1")
                row = cursor.fetchone()
                if row:
                    return row[0]
        except Exception as e:
            log(f"[錯誤] 查詢保留獲利失敗: {e}\n{traceback.format_exc()}", level="ERROR")
    return 0

def reset_reserved_profit():
    """
    重置保留獲利為 0
    """
    with _lock:
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE reserved_profit SET reserved = 0 WHERE id = 1")
                conn.commit()
            if debug_mode():
                log(f"[DEBUG] 保留獲利已重置為 0", level="DEBUG")
        except Exception as e:
            log(f"[錯誤] 重置保留獲利失敗: {e}\n{traceback.format_exc()}", level="ERROR")

# --- 啟動時初始化資料表與資料夾 ---
init_db()
if debug_mode():
    log("[DEBUG] 狀態管理器初始化完成", level="DEBUG")
