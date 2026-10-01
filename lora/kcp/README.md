# LoRa KCP：240-byte 分包與重組

使用上游 **KCP C 核心**，Python 透過 ctypes 呼叫，適用 Linux 上的雙向透明 LoRa 串口；`file_transfer.py` 也支援 RYLR AT 模組。
提供可靠、依序、保留訊息邊界的傳輸，包含分片、重組、ACK 與重傳。
無須 UDP/IP；兩端必須同時能收發，不能用於無回傳路徑的單向廣播。

## 安裝與建置

在專案根目錄執行（需要 C compiler、make、Python 3.9+）：

```bash
python3 -m pip install -r requirements.txt
make -C lora/kcp
make -C lora/kcp test
```

`liblorakcp.so` 在本機建置，不提交版本控制。核心已隨附，不需執行時下載。
測試包含模擬丟包、損壞、亂序、重複、雙向傳輸、時間戳回繞、最大訊息，以及 PTY 串口 CLI 整合測試。

## RYLR 大檔案傳輸（STM ↔ 筆電）

`file_transfer.py` 將檔案切成 4096-byte 區塊交給 KCP；可傳最大 1 GiB 的檔案，不受單則訊息上限限制。
收件端使用暫存檔，驗證總長度與 SHA-256 後才原子建立正式檔案，拒絕覆寫，並把驗證結果回覆發件端。
兩端成功會印出 `COMPLETE` 並退出；中斷、逾時或驗證失敗會回傳非零結束碼。
無壓縮、無斷點續傳；SHA-256 保證內容一致，不提供身分認證。

已確認本次硬體為 RYLR AT 模組，筆電地址 67、STM 地址 69，UART 115200。
以下命令保留模組目前設定，不會修改頻段、地址或空中參數。

先在 STM 啟動接收（輸出檔必須不存在）：

```bash
python3 /home/root/lora/kcp/file_transfer.py receive \
  --radio rylr --peer 67 --port /dev/lora --conv 1002 \
  --file /home/root/lora/kcp/results/trajectory.json --pps 5 --timeout 900
```

筆電發送：

```bash
python3 lora/kcp/file_transfer.py send \
  --radio rylr --peer 69 --port /dev/lora --conv 1002 \
  --file lora/example_files/rosbag2_2026_02_25-12_40_24_0_rtk_trajectory.json \
  --pps 5 --timeout 880
```

PNG 同樣使用 `--file` 指定。反向傳送時對調 send/receive 與檔案路徑，STM 的 peer 仍是 67，筆電的 peer 仍是 69。
每次測試換新的 `--conv`；接收端 timeout 應包含啟動等待時間。
透明模組使用 `--radio transparent`，不需 `--peer`。
預設最多 2 frames/s、最小 RTO 3000 ms、總逾時 1800 秒；上述實機測試使用 5 frames/s。
進度 `data` 是發件端已交給 KCP／收件端已重組寫入的檔案 bytes；發件端數字不是收件確認。

RYLR 適配重用 `lora/rylr/rtcm_transport.py`：

- 每個 `AT+SEND` 的 **無線 payload ≤240 bytes**；UART 命令本身另有 AT 標頭與 CR/LF，因此可能超過 240 bytes。
- 依 `+RCV` 的長度欄位讀取二進位資料，不把 CR/LF、逗號或 NUL 誤認為 payload 邊界。
- 等待本地 `+OK` 時仍處理收到的 KCP 資料／ACK；`+OK` 不代表遠端已收到。
- `--peer` 同時指定發送地址及接收來源過濾；不是加密或身分認證。
- 部署時保留 `lora/kcp/` 與 `lora/rylr/rtcm_transport.py` 的相對目錄關係。

STM32MP157 的 Linux 為 ARMv7 hard-float，不能直接使用筆電的 x86-64 `.so`。
有 ARM toolchain 時可另外產生 ARM 版本再部署，勿覆蓋本機版：

```bash
arm-linux-gnueabihf-gcc -O2 -fPIC -shared \
  lora/kcp/shim.c lora/kcp/vendor/ikcp.c -o /tmp/liblorakcp.so
scp /tmp/liblorakcp.so root@192.168.10.11:/home/root/lora/kcp/
```

## 單則訊息收發範例（透明串口）

先啟動接收端；輸出檔必須不存在：

```bash
python3 lora/kcp/link.py receive --port /dev/ttyUSB0 --conv 1001 --file /tmp/received.bin --timeout 180
```

再啟動發送端：

```bash
python3 lora/kcp/link.py send --port /dev/ttyUSB1 --conv 1001 --file /tmp/input.bin --timeout 120
```

- 每次 CLI 傳一則二進位訊息，最大 **26,670 bytes**；超過上限會拒絕，並非任意大小檔案傳輸工具。
- 雙方 `--conv` 必須相同，每次重新啟動一組連線請換新的 uint32 ID，避免舊封包混入。沒有自動握手或斷線續傳。
- 發送端在所有片段得到對端 KCP ACK 後結束；ACK 不代表接收端檔案已持久化到磁碟。
- 接收端完成重組後寫檔，繼續服務 ACK 到 `--timeout`，避免最後 ACK 遺失使發送端卡住。確認發送端成功後也可 Ctrl+C（結束碼 130）。
- 預設 115200 baud、最多 5 個 UART frame/s、最小重傳等待 1500 ms。可用 `--baud`、`--pps`、`--rto-ms` 調整。
- `--timeout` 從各自啟動時計算；接收端請留足啟動等待及傳輸時間。逾時未完成會以非零結束碼退出。

兩個 LoRa 模組須已配置相容的頻道、空中速率與透明傳輸模式；本工具不改模組設定、不操作 M0/M1 或 AUX。
若模組會加入 RSSI、地址或其他串口資料，需先關閉該功能或另寫適配層。
半雙工模組可能產生資料與 ACK 碰撞；KCP 可重傳，但不提供無線媒體存取排程。
請按實際空中速率調低 `--pps`、提高 `--rto-ms`；透明模組尚未實測，RYLR 測試結果另見 [實機測試報告](HARDWARE_TEST.md)。

## 封包格式

```text
COBS(KCP packet + CRC32 little-endian) + 0x00
```

| 項目 | 上限 |
|---|---:|
| KCP 封裝 frame（透明 UART／RYLR 無線 payload，含分隔符） | 240 bytes |
| KCP packet MTU | 234 bytes |
| KCP segment 標頭 | 24 bytes |
| 單個資料 segment 有效負載 | 210 bytes |
| CRC32 | 4 bytes |
| COBS 額外空間 + 分隔符 | 2 bytes |

最後一片及 ACK 可以小於 240 bytes，不補零。COBS 還原 UART byte stream 的封包邊界；CRC 錯誤、過長或格式不合法的 frame 會丟棄並重新同步。
這保證的是軟體 frame 大小；透明模組可能合併或再切分 UART 資料，不能保證實際空中封包邊界。
CRC 只檢查損壞，沒有驗證身分或加密。

## Python 整合

`transport.py` 的 `KcpLink` 可供程式長期雙向收發：

```python
from lora.kcp.transport import KcpLink

with KcpLink(conv=1001) as link:
    link.send(b'large binary message')
    # 在單一執行緒的事件迴圈中：
    # link.feed(serial_bytes)          # 任意長度的 UART 讀取資料
    # link.update(monotonic_ms)        # 一般每 50 ms 呼叫
    # link.outgoing.popleft()          # 依鏈路速率送出完整 frame
    # link.receive()                  # 取得已重組的 bytes 訊息列表
    # link.pending == 0               # 前一訊息已 ACK，可以 send 下一則
```

必須持續驅動事件迴圈，即使只接收也要傳送 ACK。請定期取走重組訊息並排空輸出；輸出積壓時 CLI 會暫緩 update，但持續讀取串口。
API 每次只接受一則尚未完成 ACK 的訊息，以限制發送佇列；輸出佇列超過 256 frames 會明確報錯。
若要傳長期 RTCM stream，應依訊息邊界呼叫 API 並處理背壓；本次未更動既有 RTK 程式。

## 上游來源與授權

- Repository: https://github.com/skywind3000/kcp
- Pinned commit: `b1a7a2101dcbb96017681a500d6b82bbe5a88766`
- 原始 `ikcp.c`、`ikcp.h` 與 MIT LICENSE 存於 `vendor/`，未修改上游核心。
- `shim.c` 只設定 MTU、視窗、更新間隔、RTO，並暴露斷線狀態。
