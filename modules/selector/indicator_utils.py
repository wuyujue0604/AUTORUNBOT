# modules/selector/indicator_utils.py
# 技術指標計算與策略盤型判斷模組（支援從 config 中載入參數）

import os
import sys
import statistics
import json
from typing import List, Dict

# === 加入根目錄路徑（for 跨資料夾引用）===
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# === 載入動態策略設定檔 ===
def load_strategy_config() -> dict:
    config_path = os.path.join(PROJECT_ROOT, "config/strategy_config.json")
    with open(config_path, "r") as f:
        config = json.load(f)
    config.setdefault("DEFAULT_INDICATOR_PARAMS", {})
    return config

# === RSI 評分 ===
def calc_rsi_score(klines: List[Dict], period: int = None) -> float:
    config = load_strategy_config()
    period = period or config["DEFAULT_INDICATOR_PARAMS"].get("RSI_PERIOD", 14)

    closes = [float(k["close"]) for k in klines]
    if len(closes) < period + 1:
        return 0.5

    gains, losses = [], []
    for i in range(1, period + 1):
        delta = closes[-i] - closes[-i - 1]
        if delta > 0:
            gains.append(delta)
        else:
            losses.append(abs(delta))

    avg_gain = sum(gains) / period if gains else 0.0001
    avg_loss = sum(losses) / period if losses else 0.0001
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi / 100.0, 4)

# === MACD 評分 ===
def calc_macd_score(klines: List[Dict], fast: int = None, slow: int = None) -> float:
    config = load_strategy_config()
    fast = fast or config["DEFAULT_INDICATOR_PARAMS"].get("MACD_FAST", 12)
    slow = slow or config["DEFAULT_INDICATOR_PARAMS"].get("MACD_SLOW", 26)

    closes = [float(k["close"]) for k in klines]
    if len(closes) < slow + 2:
        return 0.5

    def ema(data, period):
        k = 2 / (period + 1)
        ema_values = [data[0]]
        for price in data[1:]:
            ema_values.append(price * k + ema_values[-1] * (1 - k))
        return ema_values

    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    if len(ema_fast) != len(ema_slow):
        min_len = min(len(ema_fast), len(ema_slow))
        ema_fast = ema_fast[-min_len:]
        ema_slow = ema_slow[-min_len:]

    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    max_val = max([abs(x) for x in macd_line]) or 1e-6
    score = macd_line[-1] / max_val
    return round((score + 1) / 2, 4)

# === KDJ 評分 ===
def calc_kdj_score(klines: List[Dict]) -> float:
    if len(klines) < 9:
        return 0.5

    low_list = [float(k["low"]) for k in klines[-9:]]
    high_list = [float(k["high"]) for k in klines[-9:]]
    close = float(klines[-1]["close"])

    low_min = min(low_list)
    high_max = max(high_list)
    rsv = (close - low_min) / (high_max - low_min + 1e-6) * 100

    k = rsv
    d = (2 / 3) * k + (1 / 3) * 50
    j = 3 * k - 2 * d
    j = max(0, min(100, j))
    return round(j / 100, 4)

# === BOLL 評分 ===
def calc_boll_score(klines: List[Dict]) -> float:
    closes = [float(k["close"]) for k in klines[-20:]]
    if len(closes) < 20:
        return 0.5

    mid = statistics.mean(closes)
    std_dev = statistics.stdev(closes)
    upper = mid + 2 * std_dev
    lower = mid - 2 * std_dev
    last = closes[-1]

    pos = (last - lower) / (upper - lower + 1e-6)
    return round(min(1, max(0, pos)), 4)

# === Volume 評分 ===
def calc_volume_score(klines: List[Dict]) -> float:
    volumes = [float(k["vol"]) for k in klines[-20:]]
    if len(volumes) < 5:
        return 0.5

    current = volumes[-1]
    avg = sum(volumes[:-1]) / (len(volumes) - 1)
    ratio = current / (avg + 1e-6)
    score = min(1.0, max(0.0, ratio / 2))
    return round(score, 4)

# === 盤型判斷 ===
def detect_market_mode(klines: List[Dict]) -> str:
    macd_strength = calc_macd_score(klines)
    return "trend" if macd_strength > 0.6 or macd_strength < 0.4 else "range"