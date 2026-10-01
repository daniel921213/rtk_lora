import serial
import threading
import sys

# 請替換為您實際的序列埠名稱與 Baudrate
PORT = '/dev/lora'
BAUD = 115200

try:
    ser = serial.Serial(PORT, BAUD, timeout=0.1)
    print(f"✅ 已連線至 {PORT}，可以開始輸入訊息進行 LoRa 測試。")
except Exception as e:
    print(f"❌ 序列埠開啟失敗: {e}")
    sys.exit()

# 接收資料的執行緒函數
def receive_data():
    while True:
        if ser.in_waiting > 0:
            try:
                # 讀取並解碼，忽略無法解碼的亂碼
                data = ser.readline().decode('utf-8', errors='ignore').strip()
                if data:
                    print(f"\n[收到 LoRa 訊息] 👉 {data}\n請輸入訊息: ", end="", flush=True)
            except Exception:
                pass

# 啟動接收執行緒 (設定 daemon=True 讓主程式結束時一併結束)
rx_thread = threading.Thread(target=receive_data, daemon=True)
rx_thread.start()

# 發送資料的主迴圈
try:
    while True:
        msg = input("請輸入訊息: ")
        if msg:
            # 加上換行符號後發送 (編碼為 byte)
            ser.write((msg + '\n').encode('utf-8'))
            print(f"[已發送] {msg}")
except KeyboardInterrupt:
    print("\n測試結束。")
    ser.close()