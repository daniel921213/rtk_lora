# LoRa 檔案傳送與接收

電腦端傳送指定檔案，STM32MP157A-DK1（Linux）接收並儲存檔案。兩端使用 Python 3 與 `pyserial>=3.5`，預設 `/dev/lora`、115200 baud、8N1。這是 Linux 使用者空間程式，不是 Cortex-M 韌體。

模組需設定為相同頻道、空中速率與透明雙向傳輸模式，且串口不能被其他程式占用；本程式不修改模組設定。

## 執行

先在 STM32（`root@192.168.10.11`）執行：

```bash
cd ~/lora_sender
python3 receive_file.py --port /dev/lora --verbose
```

接收結果預設放在程式旁的 `received_files/`。接收程式持續監聽，可按 Ctrl+C 停止；完成後仍需保持執行，才能回覆遺失 ACK 後的重傳。

在電腦的本專案根目錄執行：

```bash
# 不給檔案參數時，使用 example_files 裡的 RTK JSON
python3 lora_sender/send_file.py --port /dev/lora

# 或指定檔案
python3 lora_sender/send_file.py lora_sender/example_files/rosbag2_2026_02_25-12_40_24_0_rtk_trajectory.json --port /dev/lora
```

若使用其他 USB 裝置，以 `--port /dev/ttyUSB0` 等覆寫。接收端可用 `--output-dir /path/to/files` 指定輸出目錄。目的檔案已存在時拒絕覆寫，請使用另一個輸出目錄或自行移走舊檔。

預設 UART 發送上限 `--rate 200` bytes/s，每段檔案 payload 120 bytes（`--chunk-size 1..120`），等待 ACK 最多 20 秒（`--ack-timeout`），最多額外重傳 5 次（`--retries`）。速率含傳輸框架，不等於檔案吞吐量；ACK、無線延遲與重傳會增加耗時。範例 JSON 288,450 bytes，壓縮後為 54,844 bytes，低速 LoRa 仍需數分鐘至十多分鐘。可依實際空中速率調整 `--rate`；`--rate 0` 取消限速，但可能使模組緩衝溢位。

接收端 `--idle-timeout 120` 表示未收到可接受資料達 120 秒後丟棄未完成傳輸。若增加傳送端 ACK timeout，接收端 idle timeout 也應相應加大。中斷後重新執行會從頭傳送，沒有跨程序斷點續傳。

## 協定與驗證

- 原始檔案採 zlib 壓縮；若壓縮無效则直接傳原始 bytes，接收結果與原檔逐位元組相同。
- 協定版本 LF02；起始 metadata 使用緊湊二進位格式，範例起始框架 150 bytes、資料框架最多 190 bytes，均可單次 UART 寫入。過長框架分次寫入時額外間隔 2 秒。兩端必須使用相同版本。
- 框架採 base64 加換行定界，內容包含版本、隨機傳輸 ID、類型、序號、payload 與 CRC32。串口 write 邊界不當作封包邊界，每次 UART write 預設 200 bytes，可用 `--write-size 1..200` 調整；實際可用大小及速率仍取決於模組與無線鏈路。
- 傳送端依序傳 metadata、資料段、完成訊息；每段等待 ACK，逾時重傳。接收端對重複段只回 ACK，不重複寫入。
- 收完後驗證原始長度及 SHA-256，才以原檔名原子建立結果；沒有完成驗證的資料不會成為輸出檔案。完成 ACK 遺失時可重回 ACK。
- 單檔上限 64 MiB；接收端使用 RAM 暫存壓縮資料及解壓資料。協定適用單一傳送端與單一接收端，不提供加密或身分認證。
- NavSatFix 的 status 欄位按原檔保留，不重新詮釋為 RTK fixed/float。

本機測試（無需無線設備）：

```bash
python3 -m unittest discover -s lora_sender -v
```

涵蓋範例檔完整還原、ACK 遺失重傳（含最後 ACK）、損壞與截斷框架、序號錯誤、SHA-256 失敗、空檔與禁止覆寫。

## 本次實機檢查

接收程式部署於 `root@192.168.10.11:/home/root/lora_sender`，使用 `/dev/lora`、115200 baud。已驗證 31、54、120、200 bytes 的單向訊息全部完整收到，3 個 190-byte 協定框架也逐位元組一致。LF02 檔案起始框架已收到 STM32 的 ACK，但後續第一段資料仍逾時；純文字双向測試的第一個 ACK 正常，後續本機收到大量非預期位元組。這些結果尚不足以確定原因，範例 JSON 尚未完成實機傳送。

執行紀錄在遠端 `receiver.log`。`--verbose` 顯示每次回應的類型（A = ACK，E = error）、序號及傳輸 ID；每 15 秒顯示實收 bytes、有效框架及有效 payload。背景接收程序的 PID 記錄於 `receiver.pid`；若要改用 `lora/lora_test.py` 手動測試，先停止此程序，避免兩個程式同時讀取串口：

```bash
kill "$(cat ~/lora_sender/receiver.pid)"
```
