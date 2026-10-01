# TCP RTCM 大小擷取

在專案根目錄執行（只需 Python 3 標準函式庫）：

```bash
python3 lora/test/rtcm_tcp_capture.py --host 192.168.10.11 --port 2101 --duration 15
```

不指定參數時也是上述設定。可使用腳本絕對路徑從其他目錄執行。
程式只讀取原始 TCP 資料，不寫入 GNSS 或 LoRa，不送握手或重傳；不支援需要認證的 NTRIP。

每次在 `runtime/rtcm_capture/時間戳記/` 建立：

- `raw.rtcm3`：原始收到的所有 bytes。
- `valid.rtcm3`：通過 CRC24Q 的完整 RTCM3 訊框。
- `frames.csv`：每筆完成接收時間、類型、payload 大小、完整大小與 MSM 衛星／訊號數。
- `summary.json`：各類型筆數、最小／最大／平均大小、每秒接收量與整段平均速率。

`--output-dir /path/to/results` 可指定結果的父目錄，每次仍會另建子目錄。
`--duration` 從 TCP 連線成功且輸出檔案開啟後開始計時；Ctrl+C 提早結束也會保存統計。
連線失敗、提前斷線或完全沒有有效訊框時回傳非零退出碼。

完整大小包含 3-byte RTCM 標頭及 3-byte CRC，不含 TCP/IP 開銷。
每秒區間按本機接收時間計算，從 0 起算；跨區間訊框算在收齊的那一秒。
最後不足一秒的區間會標示 partial second；表內是區間 bytes，總平均以實際秒數計算。
TCP read 邊界不是 RTCM 訊框邊界，也不代表同一 GNSS 觀測週期；程式會累積及拆解完整訊框。
`discarded_bytes` 是解析時跳過的資料量（含非 RTCM／CRC 不符資料或起始半包），不等於無線掉包量。
`pending_bytes` 是停止時尚未解析完成的尾端資料。
MSM 遮罩為空時會提示，不能將這種流量視為正常完整衛星觀測時的需求。
解析器沿用專案 `extras/rtcm_lora_link/rtcm.py` 與 `crc.py`，需保留其相對目錄位置。
