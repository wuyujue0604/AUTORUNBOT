import sys
import os
import time
import traceback
from datetime import datetime
from config import get_runtime_config
from logger import log
from auto_selector import run_selector
from order_executor import run_order_executor
from position_monitor import run_position_monitor
import order_notifier  # 通知模組

# 將當前目錄加入模組路徑，確保可正確 import
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def main_loop():
    error_count = 0
    max_errors = 5

    last_selector_time = 0
    last_position_monitor_time = 0

    config = get_runtime_config()
    selector_interval = config.get("SELECTOR_LOOP_INTERVAL", 45)  # 選幣間隔
    position_monitor_interval = config.get("POSITION_MONITOR_LOOP_INTERVAL", 5)  # 持倉監控間隔

    order_notifier.start_notification_thread()
    log("[主控] 交易系統啟動，開始單線程非阻塞週期任務")

    while True:
        try:
            now = time.time()
            now_dt = datetime.now()

            # 持倉監控定時執行
            if now - last_position_monitor_time >= position_monitor_interval:
                log("=" * 50)
                log(f"🕒 [持倉監控] 開始執行: {now_dt.strftime('%Y-%m-%d %H:%M:%S')}")
                try:
                    start_time = time.perf_counter()
                    run_position_monitor()
                    duration = time.perf_counter() - start_time
                    log(f"✅ [持倉監控] 執行完畢，耗時 {duration:.2f} 秒")
                except Exception as e:
                    log(f"[錯誤][持倉監控] 發生例外: {e}\n{traceback.format_exc()}", level="ERROR")
                last_position_monitor_time = now

            # 選幣定時執行（含下單）
            if now - last_selector_time >= selector_interval:
                log("=" * 50)
                log(f"🕒 [選幣+下單] 開始執行: {now_dt.strftime('%Y-%m-%d %H:%M:%S')}")
                try:
                    start_time = time.perf_counter()
                    run_selector()
                    selector_duration = time.perf_counter() - start_time
                    log(f"✅ [選幣] 執行完畢，耗時 {selector_duration:.2f} 秒")
                except Exception as e:
                    log(f"[錯誤][選幣] 發生例外: {e}\n{traceback.format_exc()}", level="ERROR")

                try:
                    start_time = time.perf_counter()
                    trades = run_order_executor()
                    executor_duration = time.perf_counter() - start_time
                    log(f"✅ [下單模組] 執行完畢，耗時 {executor_duration:.2f} 秒")

                    if trades and isinstance(trades, list):
                        for trade in trades:
                            order_notifier.queue_trade(trade)
                except Exception as e:
                    log(f"[錯誤][下單] 發生例外: {e}\n{traceback.format_exc()}", level="ERROR")

                last_selector_time = now

            time.sleep(0.1)  # 小睡避免CPU全忙
            error_count = 0  # 成功後重置錯誤計數

        except KeyboardInterrupt:
            log("🛑 使用者中斷執行，已安全退出。")
            break

        except Exception as e:
            error_count += 1
            log(f"[錯誤] 主程序例外: {e}\n{traceback.format_exc()}", level="ERROR")
            if error_count >= max_errors:
                log(f"[致命] 連續錯誤超過 {max_errors} 次，系統暫停運行", level="ERROR")
                break
            time.sleep(10)

if __name__ == "__main__":
    main_loop()
