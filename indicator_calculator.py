import pandas as pd
import numpy as np
from config import get_runtime_config, debug_mode

# === 📌 技術指標計算工具 ===

def calc_rsi(df, period=14):
    """
    計算 RSI 指標（相對強弱指標）
    :param df: 含有 'close' 欄位的 DataFrame
    :param period: 計算週期，預設14
    :return: RSI 值序列（pandas Series）
    """
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calc_macd(df, fast=12, slow=26, signal=9):
    """
    計算 MACD 指標（移動平均收斂擴散指標）
    :param df: 含有 'close' 欄位的 DataFrame
    :param fast: 快速 EMA 週期，預設12
    :param slow: 慢速 EMA 週期，預設26
    :param signal: 信號線 EMA 週期，預設9
    :return: MACD 差離值序列（pandas Series）
    """
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return hist

def calc_ma(df, period=20):
    """
    計算移動平均線（MA）
    :param df: 含有 'close' 欄位的 DataFrame
    :param period: 週期，預設20
    :return: MA 序列（pandas Series）
    """
    return df['close'].rolling(window=period).mean()

def calc_bollinger(df, period=20, dev=2):
    """
    計算布林通道上下軌
    :param df: 含有 'close' 欄位的 DataFrame
    :param period: 週期，預設20
    :param dev: 標準差倍數，預設2
    :return: (upper_band, lower_band) 兩條序列
    """
    ma = calc_ma(df, period)
    std = df['close'].rolling(window=period).std()
    upper = ma + dev * std
    lower = ma - dev * std
    return upper, lower

def calc_adx(df, period=14):
    """
    計算 ADX 指標（平均方向指標）
    :param df: 含有 'high', 'low', 'close' 欄位的 DataFrame
    :param period: 週期，預設14
    :return: ADX 序列（pandas Series）
    """
    plus_dm = df['high'].diff()
    minus_dm = df['low'].diff().abs()
    tr = df[['high', 'low', 'close']].max(axis=1) - df[['high', 'low', 'close']].min(axis=1)
    atr = tr.rolling(window=period).mean()
    # 防止除以0
    atr = atr.replace(0, np.nan)
    pdi = 100 * plus_dm.rolling(window=period).mean() / atr
    ndi = 100 * minus_dm.rolling(window=period).mean() / atr
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi)
    adx = dx.rolling(window=period).mean()
    return adx

def calc_kdj(df, n=9, k_period=3, d_period=3):
    """
    計算 KDJ 指標
    :param df: 含有 'high', 'low', 'close' 欄位的 DataFrame
    :param n: RSV 計算週期，預設9
    :param k_period: K 線平滑週期，預設3
    :param d_period: D 線平滑週期，預設3
    :return: J 線序列（pandas Series）
    """
    low_min = df['low'].rolling(window=n).min()
    high_max = df['high'].rolling(window=n).max()
    denom = (high_max - low_min)
    # 防止除以0
    rsv = pd.Series(np.where(denom == 0, 0, 100 * (df['close'] - low_min) / denom), index=df.index)
    k = rsv.ewm(com=k_period-1, adjust=False).mean()
    d = k.ewm(com=d_period-1, adjust=False).mean()
    j = 3 * k - 2 * d
    return j

# === 📈 主邏輯：計算技術指標方向與信心 ===

def calculate_indicators(df, symbol, timeframe, disabled_indicators=None):
    """
    計算多項技術指標，整合成買賣方向與信心分數。
    :param df: K 線 DataFrame
    :param symbol: 幣種名稱（字串）
    :param timeframe: 時間框架（字串）
    :param disabled_indicators: 要停用的指標清單
    :return: dict，包含 symbol, direction, score, indicators 詳細數值
    """
    if len(df) < 2:
        if debug_mode():
            print(f"[DEBUG] {symbol} {timeframe} K 線資料不足，跳過指標計算")
        return {
            "symbol": symbol,
            "direction": "none",
            "score": 0,
            "indicators": {}
        }

    config = get_runtime_config()
    disabled = disabled_indicators or config.get("DISABLED_INDICATORS", [])
    weights = config.get("INDICATOR_WEIGHTS", {})

    latest = df.iloc[-1]
    indicators = {}
    score = 0
    direction_votes = {"buy": 0, "sell": 0}

    # RSI 指標判斷
    if "RSI" not in disabled:
        rsi = calc_rsi(df)
        rsi_val = rsi.iloc[-1]
        if pd.notna(rsi_val):
            indicators["RSI"] = round(rsi_val, 2)
            if rsi_val > 70:
                direction_votes["sell"] += 1
                score += weights.get("RSI", 1.0)
            elif rsi_val < 30:
                direction_votes["buy"] += 1
                score += weights.get("RSI", 1.0)

    # MACD 指標判斷
    if "MACD" not in disabled:
        macd = calc_macd(df)
        macd_val = macd.iloc[-1]
        if pd.notna(macd_val):
            indicators["MACD"] = round(macd_val, 4)
            if macd_val > 0:
                direction_votes["buy"] += 1
                score += weights.get("MACD", 1.0)
            else:
                direction_votes["sell"] += 1
                score += weights.get("MACD", 1.0)

    # MA 指標判斷
    if "MA" not in disabled:
        ma = calc_ma(df)
        ma_val = ma.iloc[-1]
        if pd.notna(ma_val):
            indicators["MA"] = round(ma_val, 4)
            if latest["close"] > ma_val:
                direction_votes["buy"] += 1
                score += weights.get("MA", 1.0)
            else:
                direction_votes["sell"] += 1
                score += weights.get("MA", 1.0)

    # BOLL 指標判斷
    if "BOLL" not in disabled:
        upper, lower = calc_bollinger(df)
        upper_val = upper.iloc[-1]
        lower_val = lower.iloc[-1]
        if pd.notna(upper_val) and pd.notna(lower_val):
            indicators["BOLL_UP"] = round(upper_val, 4)
            indicators["BOLL_LO"] = round(lower_val, 4)
            if latest["close"] < lower_val:
                direction_votes["buy"] += 1
                score += weights.get("BOLL", 1.0)
            elif latest["close"] > upper_val:
                direction_votes["sell"] += 1
                score += weights.get("BOLL", 1.0)

    # ADX 指標判斷
    if "ADX" not in disabled:
        adx = calc_adx(df)
        adx_val = adx.iloc[-1]
        if pd.notna(adx_val):
            indicators["ADX"] = round(adx_val, 2)
            if adx_val > 25:
                direction_votes["buy"] += 1
                score += weights.get("ADX", 1.0)

    # KDJ 指標判斷
    if "KDJ" not in disabled:
        kdj = calc_kdj(df)
        kdj_val = kdj.iloc[-1]
        if pd.notna(kdj_val):
            indicators["KDJ"] = round(kdj_val, 2)
            if kdj_val < 20:
                direction_votes["buy"] += 1
                score += weights.get("KDJ", 1.0)
            elif kdj_val > 80:
                direction_votes["sell"] += 1
                score += weights.get("KDJ", 1.0)

    # 綜合投票決定方向
    direction = "none"
    if direction_votes["buy"] > direction_votes["sell"]:
        direction = "buy"
    elif direction_votes["sell"] > direction_votes["buy"]:
        direction = "sell"

    if debug_mode():
        print(f"[DEBUG] {symbol} 方向：{direction} | 信心分數：{round(score,2)}")

    return {
        "symbol": symbol,
        "direction": direction,
        "score": round(score, 2),
        "indicators": indicators
    }