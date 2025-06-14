# modules/okx_client.py
# OKX API 簡易客戶端：查詢 ticker/K線、帳戶資訊、下單與槓桿設定

import os
import sys
import time
import hmac
import base64
import json
import requests
from datetime import datetime
from dotenv import load_dotenv

# === 環境變數讀取（API KEY）===
load_dotenv()
API_KEY = os.getenv("OKX_API_KEY")
API_SECRET = os.getenv("OKX_API_SECRET")
API_PASSPHRASE = os.getenv("OKX_API_PASSPHRASE")

BASE_URL = "https://www.okx.com"
API_URL = f"{BASE_URL}/api/v5"

# === OKX API 簽名產生器 ===
def get_headers(method, endpoint, body=""):
    ts = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
    prehash = f"{ts}{method}{endpoint}{body}"
    sign = hmac.new(
        API_SECRET.encode(), prehash.encode(), digestmod="sha256"
    ).digest()
    sign_b64 = base64.b64encode(sign).decode()

    return {
        "Content-Type": "application/json",
        "OK-ACCESS-KEY": API_KEY,
        "OK-ACCESS-SIGN": sign_b64,
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": API_PASSPHRASE,
        "x-simulated-trading": "1" if os.getenv("SIMULATED", "false") == "true" else "0"
    }

# === 取得所有 ticker ===
def get_all_tickers(instType="SWAP"):
    url = f"{API_URL}/market/tickers?instType={instType}"
    try:
        response = requests.get(url, timeout=5)
        return response.json().get("data", [])
    except Exception as e:
        print(f"⚠️ 無法取得 tickers: {e}")
        return []

# === 取得 K 線資料（1分鐘線）===
def get_kline_1m(symbol: str, limit: int = 30):
    url = f"{API_URL}/market/candles?instId={symbol}&bar=1m&limit={limit}"
    try:
        response = requests.get(url, timeout=5)
        raw_data = response.json().get("data", [])
        result = []
        for item in reversed(raw_data):
            result.append({
                "ts": int(item[0]),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "vol": float(item[5])
            })
        return result
    except Exception as e:
        print(f"⚠️ 無法取得 {symbol} 的 K 線: {e}")
        return []

# === 查詢帳戶可用 USDT 餘額 ===
def get_balance():
    endpoint = "/api/v5/account/balance"
    url = BASE_URL + endpoint
    try:
        response = requests.get(url, headers=get_headers("GET", endpoint))
        data = response.json().get("data", [])[0]
        for asset in data["details"]:
            if asset["ccy"] == "USDT":
                return float(asset["availBal"])
        return 0
    except Exception as e:
        print(f"⚠️ 餘額查詢失敗：{e}")
        return 0

# === 取得最新市價 ===
def get_latest_price(symbol: str):
    url = f"{API_URL}/market/ticker?instId={symbol}"
    try:
        response = requests.get(url, timeout=5)
        data = response.json().get("data", [])[0]
        return float(data["last"])
    except Exception as e:
        print(f"⚠️ 價格查詢失敗：{e}")
        return None

# === 查詢最小下單限制（數量與金額）===
def get_min_order_amount(symbol: str):
    url = f"{API_URL}/public/instruments?instType=SWAP"
    try:
        response = requests.get(url, timeout=10)
        data = response.json().get("data", [])
        for item in data:
            if item["instId"] == symbol:
                return float(item["minSz"]), float(item["minSz"]) * float(item["ctVal"])
        return None, None
    except Exception as e:
        print(f"⚠️ 查詢最小下單限制失敗: {e}")
        return None, None

# === 設定槓桿倍數 ===
def set_leverage(instId: str, leverage: int, mode: str = "isolated", side: str = "long"):
    endpoint = "/api/v5/account/set-leverage"
    url = BASE_URL + endpoint
    body = {
        "instId": instId,
        "lever": str(leverage),
        "mgnMode": mode,
        "posSide": side  # long / short
    }
    try:
        response = requests.post(url, headers=get_headers("POST", endpoint, json.dumps(body)), json=body)
        return response.json()
    except Exception as e:
        print(f"⚠️ 設定槓桿失敗：{e}")
        return {"code": "-1", "msg": str(e)}

# === 下市價單 ===
def place_order(symbol: str, size: float, side: str = "buy", pos_side: str = "long"):
    """
    side = buy/sell（買進 / 賣出）
    pos_side = long/short（多單 / 空單）
    """
    endpoint = "/api/v5/trade/order"
    url = BASE_URL + endpoint
    body = {
        "instId": symbol,
        "tdMode": "isolated",
        "side": side,
        "ordType": "market",
        "posSide": pos_side,
        "sz": str(size)
    }
    try:
        response = requests.post(url, headers=get_headers("POST", endpoint, json.dumps(body)), json=body)
        return response.json()
    except Exception as e:
        print(f"⚠️ 下單失敗：{e}")
        return {"code": "-1", "msg": str(e)}