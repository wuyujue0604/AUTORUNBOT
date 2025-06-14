import os
import json
import time
import hmac
import base64
import hashlib
import requests
import pandas as pd
import traceback
from datetime import datetime, timezone
from dotenv import load_dotenv
from config import debug_mode, get_runtime_config
from logger import log

# 載入環境變數
load_dotenv()
API_KEY = os.getenv("OKX_API_KEY")
API_SECRET = os.getenv("OKX_API_SECRET")
API_PASS = os.getenv("OKX_API_PASSPHRASE")
BASE_URL = "https://www.okx.com"

if not API_KEY or not API_SECRET or not API_PASS:
    log("[錯誤] 請設定 .env 中的 OKX_API_KEY, OKX_API_SECRET, OKX_API_PASSPHRASE", "ERROR")
    raise ValueError("API Key/Secret/Passphrase 未設定")

HEADERS_BASE = {
    "Content-Type": "application/json",
    "OK-ACCESS-KEY": API_KEY,
    "OK-ACCESS-PASSPHRASE": API_PASS
}

def _get_timestamp():
    """取得UTC ISO 8601格式時間字串，精確到毫秒"""
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

def _sign(message: str) -> str:
    """HMAC SHA256 + Base64 簽名"""
    try:
        mac = hmac.new(API_SECRET.encode(), message.encode(), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode()
    except Exception as e:
        log(f"[錯誤][簽名] 產生簽名失敗: {e}\n{traceback.format_exc()}", "ERROR")
        raise

def _signed_request(method: str, endpoint: str, params: dict = None, body: dict = None, retry=3):
    """簽名API請求，含重試與錯誤處理"""
    method = method.upper()
    url = BASE_URL + endpoint
    query_string = ""

    if method == "GET" and params:
        query_string = "?" + "&".join([f"{k}={v}" for k, v in params.items()])
        url += query_string

    sign_body = json.dumps(body) if method == "POST" and body else ""
    timestamp = _get_timestamp()
    message = f"{timestamp}{method}{endpoint}{query_string if method == 'GET' else sign_body}"

    for attempt in range(1, retry + 1):
        try:
            signature = _sign(message)
            headers = {
                **HEADERS_BASE,
                "OK-ACCESS-SIGN": signature,
                "OK-ACCESS-TIMESTAMP": timestamp
            }

            if method == "GET":
                res = requests.get(url, headers=headers, timeout=10)
            else:
                res = requests.post(url, headers=headers, json=body, timeout=10)

            if debug_mode():
                log(f"[DEBUG][API] {method} {url}")
                if body:
                    log(f"[DEBUG][API] Request body: {body}")
                log(f"[DEBUG][API] Response: {res.text}")

            return res.json()
        except Exception as e:
            log(f"[警告][API] 第{attempt}次請求失敗: {e}", "WARN")
            time.sleep(1)

    log(f"[錯誤][API] 請求多次失敗: {method} {url}", "ERROR")
    return {}

def get_market_price(symbol: str):
    """取得最新成交價"""
    data = _signed_request("GET", "/api/v5/market/ticker", {"instId": symbol})
    try:
        if data.get("code") == "0":
            price = float(data["data"][0]["last"])
            if debug_mode():
                log(f"[DEBUG][行情] {symbol} 最新市價: {price}")
            return price
    except Exception as e:
        log(f"[錯誤][行情] 解析市價失敗: {e}\n{traceback.format_exc()}", "ERROR")
    return None

def get_ohlcv(symbol: str, bar="1h", limit=100):
    """取得K線資料（Pandas DataFrame）"""
    res = _signed_request("GET", "/api/v5/market/candles", {"instId": symbol, "bar": bar, "limit": limit})
    if res.get("code") != "0":
        log(f"[錯誤][行情] 無法取得 {symbol} 的 K 線: {res}", "ERROR")
        return None
    raw = res.get("data", [])
    try:
        df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume", "_1", "_2", "_3"])
        df = df[["ts", "open", "high", "low", "close", "volume"]]
        df["ts"] = pd.to_numeric(df["ts"], errors="raise")
        df["ts"] = pd.to_datetime(df["ts"], unit="ms")
        df = df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
        df.sort_values("ts", inplace=True)
        df.reset_index(drop=True, inplace=True)
        if debug_mode():
            log(f"[DEBUG][行情] {symbol} 取得 {len(df)} 根 K 線")
        return df
    except Exception as e:
        log(f"[錯誤][行情] K 線轉換失敗: {e}\n{traceback.format_exc()}", "ERROR")
        return None

def get_leverage(symbol: str):
    """取得合約 long/short 槓桿（cross模式）"""
    res = _signed_request("GET", "/api/v5/account/leverage-info", {
        "instId": symbol,
        "mgnMode": "cross"
    })
    if res.get("code") == "0" and res.get("data"):
        info = res["data"][0]
        long_lev = float(info.get("longLeverage", 1))
        short_lev = float(info.get("shortLeverage", 1))
        if debug_mode():
            log(f"[DEBUG][槓桿] {symbol} long: {long_lev}, short: {short_lev}")
        return long_lev, short_lev
    return 1, 1

def get_trade_balance():
    """取得交易帳戶可用 USDT 餘額"""
    res = _signed_request("GET", "/api/v5/account/balance", {"ccy": "USDT"})
    try:
        if res.get("code") == "0":
            balance = float(res["data"][0]["details"][0]["availBal"])
            if debug_mode():
                log(f"[DEBUG][帳戶] USDT 可用餘額: {balance}")
            return balance
    except Exception as e:
        log(f"[錯誤][帳戶] 餘額解析失敗: {e}\n{traceback.format_exc()}", "ERROR")
    return 0

def transfer_profit_to_funding(currency="USDT", amount=5):
    """轉帳資金至 Funding 帳戶"""
    body = {
        "ccy": currency,
        "amt": str(amount),
        "from": "18",  # 交易帳戶
        "to": "6",     # Funding帳戶
        "type": "0"
    }
    res = _signed_request("POST", "/api/v5/asset/transfer", body=body)
    if res.get("code") == "0":
        log(f"[資金] 已轉帳 {amount} {currency} 至 Funding 帳戶")
        return True
    else:
        log(f"[錯誤][資金] 轉帳失敗: {res}", "ERROR")
        return False

def get_order(symbol: str, ord_id: str):
    """
    查詢單筆訂單狀態
    :param symbol: 合約名稱
    :param ord_id: 訂單編號
    :return: API 回應 dict
    """
    params = {
        "instId": symbol,
        "ordId": ord_id
    }
    res = _signed_request("GET", "/api/v5/trade/order", params=params)
    if debug_mode():
        log(f"[DEBUG][訂單查詢] {symbol} ordId={ord_id} 回應: {res}")
    return res

def place_order(symbol: str, direction: str, size: int, ord_type="market", price: float = None, reduce_only=False):
    side = "buy" if direction == "buy" else "sell"

    config = get_runtime_config()
    hedge_mode = config.get("HEDGE_MODE_ENABLED", False)  # 默認 false，單向持倉

    body = {
        "instId": symbol,
        "tdMode": "cross",
        "side": side,
        "ordType": ord_type,
        "sz": str(size)
    }

    if hedge_mode:
        # 對沖模式，必須帶 posSide
        pos_side = "long" if direction == "buy" else "short"
        body["posSide"] = pos_side
    else:
        # 單向持倉，不能帶 posSide，否則會報錯
        pass  # 不帶 posSide

    if ord_type == "limit" and price is not None:
        body["px"] = str(price)
    if reduce_only:
        body["reduceOnly"] = True

    res = _signed_request("POST", "/api/v5/trade/order", body=body)
    if res.get("code") == "0":
        order_id = res["data"][0].get("ordId", "")
        log(f"[下單][成功] {symbol} {direction} {size} 張 {'[reduceOnly]' if reduce_only else ''} 訂單號: {order_id}")
        return res
    else:
        log(f"[下單][失敗] {symbol} {direction} {size} 張 {'[reduceOnly]' if reduce_only else ''} 錯誤: {res}", "ERROR")
        return res
    
