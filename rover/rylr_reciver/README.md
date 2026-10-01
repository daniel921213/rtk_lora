# RYLR998 RTCM 接收、GNSS 轉發與 GGA 品質監看

保留使用者指定的目錄拼字 `rylr_reciver`。依賴與模組參數見
`../../rtk_base/rylr_sender/README.md`。

```text
STM32 GNSS → send_rtcm.py → AT+SEND=67,size,raw → LoRa
  → receive_rtcm.py → CRC 正確的 RTCM3 → TCP 127.0.0.1:2102
  → tcp_gga_track.py → str2str → GNSS
                      ← GGA ← str2str（-b 1）← GNSS
```

## 啟動（repository 根目錄，兩個終端）

先確認 rover GNSS 已在 rover 模式；需要時可在啟動以下程式前，執行既有
`init_rover_receiver.py --port /dev/ttyUSB1 --baud 115200`。
若 GNSS 已正確設定，不必重新初始化。

終端 1：接收 LoRa，預設只接受來源位址 69。

```bash
python3 rover/rylr_reciver/receive_rtcm.py \
  --lora-port /dev/lora --source 69 --tcp-port 2102
```

終端 2：交由 str2str 寫入實際 GNSS 串口，接回 GGA 並記錄 RTK 座標。

```bash
python3 rover/rylr_reciver/tcp_gga_track.py \
  --host 127.0.0.1 --tcp-port 2102 \
  --gnss-port /dev/ttyUSB1 --baud 115200 \
  --str2str "$HOME/.local/bin/str2str"
```

`--str2str` 預設使用 PATH 中的 `str2str`。GNSS 裝置名稱請依實際接線調整。
不要同時啟動舊的 `lora_gga_bridge.py`、`read_gga.py` 或其他 GNSS 串口讀取程式。
本目錄既有 `rover_source_switch.sh`／`lora_gga_bridge.py` 是舊透明模式副本，
**未接入這條 AT 協定路徑**；請使用以上兩個入口。

### LoRa 在 Windows COM5、rover GNSS 在另一台 Linux 時

`lora/rylr/monitor_pc.py` 只顯示無線封包，**沒有 TCP 輸出**。先關閉它，
再從 Windows 的 repository 根目錄啟動接收端；同一時間只能有一支程式開 COM5：

```powershell
python rover/rylr_reciver/receive_rtcm.py --lora-port COM5 --source 69 --listen 0.0.0.0 --tcp-port 2102
```

`receive_rtcm.py` 每 5 秒顯示封包數、CRC 錯誤、RSSI/SNR 和 TCP client 數；
它只把 CRC 正確的完整 RTCM frame 放到 TCP。`0.0.0.0` 讓另一台機器可連入，
Linux rover 應使用 **Windows 電腦的區域網路 IP**，不能使用 `127.0.0.1`。
讓 Windows 防火牆允許 TCP 2102 後，在 rover Linux 的 repository 根目錄執行：

```bash
python3 rover/rylr_reciver/tcp_gga_track.py \
  --host <Windows電腦IP> --tcp-port 2102 \
  --gnss-port /dev/ttyUSB1 --baud 115200 \
  --str2str "$HOME/.local/bin/str2str"
```

這支 tracker 維持 TCP 修正資料輸入，透過 `str2str` 寫入 rover GNSS，
並在終端顯示 GGA 的定位品質、衛星數、HDOP 和差分資料年齡，同時記錄 CSV。
若 `str2str` 已在 PATH 中，可省略 `--str2str`。同一個 GNSS 串口不要再開
`monitor_rover_quality.py` 或其他讀取程式。

## 行為與監看

- `+RCV=<來源>,<byte 數>,<raw bytes>,<RSSI>,<SNR>` 依 byte 數解析。
  不用 `readline()` 或逗號 split 來切 payload；二進位中的逗號、CR/LF、NUL 都保留。
- 先重組並驗證 RTCM3 CRC，再輸出完整 frame，AT 回覆、RSSI/SNR 不會進入 GNSS。
- 沒有 TCP client 時丟棄已完成的 frame，不保存舊差分資料；新 client 從下一個完整 frame 開始。
- 每個 TCP client 緩衝限制 8192 bytes，超過時斷線，避免慢 client 持續積壓。
- `--frame-timeout 3` 控制無新 payload 時清除不完整 RTCM 的時間。
- 接收器每 5 秒顯示封包/bytes/有效 frames/CRC 錯誤/丟棄 bytes/RSSI/SNR。
- tracker 每 5 秒顯示 `NO FIX / SINGLE / DGPS / RTK FLOAT / RTK FIXED`、
  衛星數、HDOP、差分齡期及距上次有效 GGA 的秒數，可辨識 GGA 停更。
- stdout 顯示原始 GGA；CSV 只保存通過 NMEA checksum 且品質為 4 或 5 的座標。
- CSV 預設在 repository 的 `runtime/tracks/`；用 `--track /path/new.csv` 自訂，
  不覆寫既有檔案。
- tracker 對接收器 TCP 斷線會每 3 秒重連。str2str 退出則 tracker 報錯退出。
- Ctrl+C 關閉程式；tracker 同時清理它啟動的 str2str 子程序。

## 直接以 str2str 轉發（不做 GGA 監看時）

可用下面指令**取代終端 2**，不要和 tracker 同時開啟同一 GNSS：

```bash
str2str -in tcpcli://127.0.0.1:2102 -out serial://ttyUSB1:115200:8:n:1
```

所有 str2str stream 都不加 `#rtcm3`，保留已驗證的 bytes，不做訊息轉換。
本版沒有無線 ACK/重傳；CRC 可擋住損毀資料，不能找回遺失的完整 frame。

## 2026-09-29 驗證結果

- 11 項測試通過，包括真實 str2str + PTY GNSS + GGA CSV 回傳。
- 兩顆 RYLR998 已實測全部 256 種 byte 值可原樣傳送。
- 新 sender 在 STM32（69）發送一筆 778-byte 合成 RTCM3 frame，切成 4 個
  無線 payload；電腦（67）receiver 收到後 CRC 正確，經真實 str2str 轉發到
  模擬 GNSS PTY，778 bytes 完全一致。
- 當時未連接實際 GNSS；尚未驗證真實差分資料、持續吞吐量或 RTK FIX 收斂。
- 測試暫時將 STM32 調為 923 MHz，結束後還原測試前的 915 MHz；
  正式啟動仍須先統一兩端頻率。
