# RTCM → RYLR998 AT+SEND

這是 `rtk_base` 的 RYLR 分支版本，不是透明串口 LoRa。
目的位址預設 **67**；基地台模組位址為 **69**。

## 資料格式

```text
AT+SEND=67,<本段 byte 數>,<RTCM 原始 bytes>\r\n
```

不使用 UTF-8、hex、Base64，也不附加自訂無線標頭。先從 GNSS/TCP 串流
提取 CRC-24Q 正確的完整 RTCM3 frame，再切成最多 240 bytes 的片段。
完整 RTCM3 frame 最多 1029 bytes，因此可能需要多個 AT+SEND。
每次都必須收到本地模組的 `+OK` 才繼續；`+ERR` 或逾時會中止程式。
`+OK` **不是 rover 的接收確認**，本版沒有 ACK/重傳或片段序號。

rover 按 RTCM3 長度重組並驗證 CRC。遺失/損毀造成的不完整資料不會送進 GNSS；
持續收到資料時會在累積足夠 bytes 後重新同步，停止接收時會清除逾時殘片。
無線容量不足時應降低基地台 RTCM 訊息種類/頻率；此版不保證壅塞時的資料新鮮度。

## 安裝與無線設定

需要 Python 3、`pyserial>=3.5`。`tcp`/`both` 模式另外需要 RTKLIB `str2str`。
保持 repository 目錄結構；兩端都必須包含共用的 `lora/rylr/rtcm_transport.py`，
不能只複製本目錄。STM32 既有 str2str 後備路徑為
`/home/root/rtk_base/stm_bin/str2str`，也可以用 `--str2str` 指定。

使用 `lora/rylr/test.py` 先設定模組，設定後離開終端釋放串口：

| 參數 | STM32 | 電腦 rover |
| --- | --- | --- |
| ADDRESS | 69 | 67 |
| NETWORKID | 18 | 18 |
| BAND | 923000000 | 923000000 |
| PARAMETER | 5,9,1,24 | 5,9,1,24 |
| MODE | 0 | 0 |
| IPR | 115200 | 115200 |
| CPIN | 相同／皆無密碼 | 相同／皆無密碼 |

頻率若需在重啟後保留，依手冊用 `raw AT+BAND=923000000,M`，再查詢確認。
本版不自動修改模組參數。`PARAMETER` 也可使用其他兩端一致的設定。

## 基地台已輸出 RTCM：只啟動發送

在 repository 根目錄執行，確認沒有其他程式占用 GNSS/LoRa 串口：

```bash
python3 rtk_base/rylr_sender/send_rtcm.py \
  --gnss-port /dev/rtk_gps --gnss-baud 115200 \
  --lora-port /dev/lora --dest 67
```

若已有基地台 TCP server，不要重複開啟 GNSS 串口：

```bash
python3 rtk_base/rylr_sender/send_rtcm.py \
  --tcp-host 127.0.0.1 --tcp-port 2101 \
  --lora-port /dev/lora --dest 67
```

TCP 中斷、LoRa `+ERR`、`+OK` 逾時都會退出並回傳非零狀態，可由 service 重啟。
`--payload-size 240`、`--packet-gap 0.01`、`--ok-timeout 3` 可調整；
不會自動重送逾時片段，避免重複或命令重疊。

## 先設定 GNSS 基地台再發送

沿用同目錄 `setting_base_stm.py` 的 GNSS 設定流程。
請**明確提供正確的基地台座標**，勿直接沿用檔案內舊預設位置。
緯經度參數格式沿用原程式的 NMEA `ddmm.mmmm`，高度為橢球高（公尺）。

```bash
python3 rtk_base/rylr_sender/setting_base_stm.py /dev/rtk_gps 0.0.0.0 \
  --mode lora --lora-port /dev/lora --dest 67 \
  --base-pos <緯度_ddmm> <經度_dddmm> <橢球高_m>
```

- `--mode lora`（此分支預設）：直接 GNSS → RTCM parser → RYLR。
- `--mode both`：str2str GNSS → TCP:2101，再由 RYLR sender 訂閱同一 TCP server；TCP 仍可供其他 client 使用。
- `--mode tcp`：只開原始 TCP RTCM server。

設定 GNSS 後會先關閉設定用串口，再讓 streamer 開啟，避免重複讀取。

## 驗證

```bash
python3 -m unittest discover -s lora/rylr/tests -v
```

協定測試涵蓋全部 byte 值、任意 UART 切割、240-byte 限制、CRC、遺失片段及逾時。
整合測試會啟動真正的 sender/receiver/tracker/str2str，用 PTY 模擬 UART/GNSS；
未安裝 str2str 時該整合測試標示 skip。

手冊將 data 描述為 ASCII；本次兩顆實機已驗證 raw binary 包含 0x00–0xFF
及 CR/LF 可傳輸。其他韌體版本仍應先實測。

## 2026-09-29 驗證結果

- 11 項測試通過，包括真實 str2str + PTY GNSS + GGA CSV 回傳。
- 兩顆 RYLR998 已實測全部 256 種 byte 值可原樣傳送。
- 新 sender 在 STM32（69）發送一筆 778-byte 合成 RTCM3 frame，切成 4 個
  無線 payload；電腦（67）receiver 收到後 CRC 正確，經真實 str2str 轉發到
  模擬 GNSS PTY，778 bytes 完全一致。
- 當時未連接實際 GNSS；尚未驗證真實差分資料、持續吞吐量或 RTK FIX 收斂。
- 測試暫時將 STM32 調為 923 MHz，結束後還原測試前的 915 MHz；
  正式啟動仍須先統一兩端頻率。
