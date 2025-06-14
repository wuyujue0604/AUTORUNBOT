# modules/risk/risk_manager.py
# 負責資金動態分配與每筆倉位投入金額限制

import os
import sys
from typing import List, Dict
from modules.okx_client import get_balance
from config.config import get_runtime_config

# === 計算每個倉位可用本金（給 auto_selector 專用）===
def allocate_capital_per_position(selected: List[Dict]) -> Dict[str, float]:
    """
    根據多空分佈與保留比例，動態分配每個倉位的可用本金上限（不含槓桿）。
    回傳格式為：{ "BTC-USDT-SWAP": 88.5, "ETH-USDT-SWAP": 92.3, ... }
    """
    config = get_runtime_config()
    balance = get_balance()
    short_reserved_ratio = config.get("SHORT_CAPITAL_RESERVED_RATIO", 0.15)
    max_ratio = config.get("MAX_SINGLE_POSITION_RATIO", 0.15)

    # 預留空單保險金
    reserved_capital = balance * short_reserved_ratio
    usable_capital = balance - reserved_capital
    if usable_capital <= 0:
        return {}

    # 多空分組
    longs = [s for s in selected if s["direction"] == "long"]
    shorts = [s for s in selected if s["direction"] == "short"]
    total_positions = len(longs) + len(shorts)

    if total_positions == 0:
        return {}

    # 動態分配：每個倉位佔可用資金比例（但不能超過最大投入比例）
    long_unit = usable_capital * (len(longs) / total_positions) / max(len(longs), 1)
    short_unit = usable_capital * (len(shorts) / total_positions) / max(len(shorts), 1)

    max_cap_per_position = balance * max_ratio
    allocation = {}
    for s in longs:
        allocation[s["symbol"]] = min(long_unit, max_cap_per_position)
    for s in shorts:
        allocation[s["symbol"]] = min(short_unit, max_cap_per_position)

    return allocation

# === 給 long_trader / short_trader 使用的簡化資金分配邏輯 ===
def get_allocated_budget(config, direction="long", short_count=1, long_count=1):
    """
    根據方向與倉位數量，動態分配每筆可投入的資金上限。
    - 若為多單方向，預留空單保險金後再平均分配
    - 若為空單方向，使用全部資金平均分配
    """
    total_balance = get_balance()
    short_reserve_ratio = float(config.get("SHORT_CAPITAL_RESERVED_RATIO", 0.15))

    if direction == "long":
        usable = total_balance * (1 - short_reserve_ratio)
    else:
        usable = total_balance

    total_count = max(1, short_count + long_count)
    return usable / total_count