# modules/trader/order_utils.py
# OKX 下單工具模組，負責下單、設定槓桿、查詢規格與錯誤轉譯

import os
import requests
import time
import hmac
import hashlib
import base64
import json
from dotenv import load_dotenv

# === 載入 API 金鑰與網址 ===
load_dotenv()
API_KEY = os.getenv("OKX_API_KEY")
API_SECRET = os.getenv("OKX_API_SECRET")
API_PASSPHRASE = os.getenv("OKX_API_PASSPHRASE")
BASE_URL = "https://www.okx.com"

HEADERS = {
    "Content-Type": "application/json",
    "OK-ACCESS-KEY": API_KEY,
    "OK-ACCESS-PASSPHRASE": API_PASSPHRASE
}

# === 計算簽名 ===
def sign_request(timestamp, method, request_path, body=""):
    msg = f"{timestamp}{method}{request_path}{body}"
    mac = hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256)
    d = mac.digest()
    return base64.b64encode(d).decode()

# === 傳送 REST 請求 ===
def send_request(method, path, payload=None):
    url = BASE_URL + path
    body = json.dumps(payload) if payload else ""
    timestamp = str(time.time())

    HEADERS["OK-ACCESS-TIMESTAMP"] = timestamp
    HEADERS["OK-ACCESS-SIGN"] = sign_request(timestamp, method, path, body)

    response = requests.request(method, url, headers=HEADERS, data=body)
    try:
        return response.json()
    except Exception:
        return {"code": "99999", "msg": "回應非 JSON 格式"}

# === 設定槓桿 ===
def set_leverage(symbol: str, leverage: int, mode="isolated"):
    """
    建倉前必須設定槓桿
    """
    payload = {
        "instId": symbol,
        "lever": str(leverage),
        "mgnMode": mode
    }
    res = send_request("POST", "/api/v5/account/set-leverage", payload)
    return res

# === 查詢最小下單量與金額限制 ===
def get_instrument_rules(symbol: str):
    res = send_request("GET", f"/api/v5/public/instruments?instType=SWAP")
    if res.get("code") != "0":
        return None

    for inst in res.get("data", []):
        if inst["instId"] == symbol:
            return {
                "minSz": float(inst["minSz"]),
                "ctVal": float(inst.get("ctVal", 1)),
                "minNotional": float(inst.get("minNotional", 5))
            }
    return None

# === 中文錯誤訊息對照表 ===
ERROR_MAP = {
    "51000": "參數錯誤，請檢查下單格式",
    "51001": "幣種錯誤，請確認是否支援",
    "51002": "帳戶餘額不足，請確認資金是否足夠",
    "51006": "下單數量小於最小下單量",
    "51008": "下單金額小於最小金額限制",
    "51119": "開倉金額低於限制，請檢查最小值",
    "58001": "API 金鑰無效或未啟用",
    "58002": "API 權限不足，請確認已啟用交易權限"
}

def explain_error(code):
    return ERROR_MAP.get(code, f"未知錯誤（代碼 {code}）")

# === 下單功能（建倉用） ===
def place_order(symbol: str, side: str, size: float, leverage: int, mode="isolated"):
    """
    建立市價單下單：side = buy / sell
    """
    # 設定槓桿
    lev_res = set_leverage(symbol, leverage, mode)
    if lev_res.get("code") != "0":
        return {"success": False, "msg": f"槓桿設定失敗：{explain_error(lev_res.get('code'))}"}

    payload = {
        "instId": symbol,
        "tdMode": mode,
        "side": side,
        "ordType": "market",
        "sz": str(size)
    }
    res = send_request("POST", "/api/v5/trade/order", payload)

    if res.get("code") == "0":
        return {"success": True, "orderId": res["data"][0]["ordId"]}
    else:
        return {"success": False, "msg": explain_error(res.get("code"))}