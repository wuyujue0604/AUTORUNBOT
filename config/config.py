# config.py
# 動態載入 config.json，支援熱更新

import os
import sys
import json

# === 專案根目錄 ===
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# === 全域參數快取 ===
_config_cache = None
_config_path = os.path.join(PROJECT_ROOT, "strategy_config.json")

def get_runtime_config(force_reload: bool = False) -> dict:
    global _config_cache
    if _config_cache is None or force_reload:
        if not os.path.exists(_config_path):
            raise FileNotFoundError(f"⚠️ 找不到 strategy_config.json：{_config_path}")
        with open(_config_path, "r") as f:
            _config_cache = json.load(f)
    return _config_cache

def debug_mode() -> bool:
    config = get_runtime_config()
    return config.get("DEBUG", False)

def test_mode() -> bool:
    config = get_runtime_config()
    return config.get("TEST_MODE", False)