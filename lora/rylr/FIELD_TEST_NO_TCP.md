# LoRa RTCM 現場測試流程（只看封包，不使用 TCP）

這份流程用目前的設備測試：STM32MP157 Linux 基站 → Black Pill → 發射端 RYLR998 → 無線 → 接收端 RYLR998 → Windows 電腦 COM5。Windows 上用 `monitor_pc.py` 看封包、RTCM CRC 和衛星觀測數。這次**不用執行** `receive_rtcm.py`、`tcp_gga_track.py` 或 `str2str`。

## 1. 出門前確認接線與供電

| 設備 | 接線／用途 |
| --- | --- |
| 基站 Linux UART TX | Black Pill `PA10`；兩板 `GND` 相連 |
| Black Pill `PA2` | 發射端 RYLR998 `RXD` |
| 發射端 RYLR998 `TXD` | Black Pill `PA3` |
| Black Pill 與發射端 RYLR998 | 共用 `GND`；RYLR998 使用足夠電流的 3.3 V 電源 |
| 接收端 RYLR998 | 接在電腦的 USB-TTL 上，目前是 `COM5`；確認模組供電、TXD/RXD/GND 接妥 |
| Black Pill 除錯（可選） | `PA9` → **另一個** USB-TTL 的 `RXD`，`GND` → `GND`；目前可能是 `COM7` |

- 發射端和接收端 RYLR998 都接上天線後再發射，並確認兩端的頻段、網路 ID、射頻參數等設定相同。程式使用發射端地址 **69**、接收端地址 **67**。
- Black Pill 正常執行時 `BOOT0` 要回到正常啟動位置（低電位）；若剛燒錄完，調回後按一次 `NRST`。測試時不用再進 DFU 或重新燒錄。
- Black Pill 若由 USB-C 供電，測試時保持供電。除錯 USB-TTL 的 `TXD`、`VCC` 不接 Black Pill；`PA10` 留給 Linux TX，避免兩個 TX 同時接同一腳。
- 把基站 GNSS 天線放在開闊處並確認基站已正常輸出 RTCM。先在近距離測通，再拉開距離。

## 2. 基站 Linux：確認 RTCM 正在送往 Black Pill

在 **STM32MP157 Linux 的終端機**執行：

```bash
systemctl status stm-forward.service --no-pager
pgrep -af stm_forward.py
```

若服務顯示 `active (running)`，或 `pgrep` 找到正在執行的 `stm_forward.py`，直接往下一步，**不要再開第二份**，避免搶同一個序列埠。

若未執行，而且你在這台 Linux 上有 repository，從 **repository 根目錄**手動啟動：

```bash
python3 rtk_base/stm_forward.py \
  --input-port /dev/rtk_gps --input-baud 115200 \
  --output-port /dev/ttySTM1 --output-baud 115200
```

此程式預設不開 TCP；會把基站 GNSS 的原始資料送到 Linux UART TX，再到 Black Pill `PA10`。若現場 GNSS 實際埠不是 `/dev/rtk_gps`，改成實際埠。手動啟動時保持這個終端機開著。

## 3. Windows：看無線封包

在 **Windows PowerShell**，先切到本專案根目錄；若你的資料夾位置相同：

```powershell
cd C:\Users\user\rtk_dual_link_toolkit
python .\lora\rylr\monitor_pc.py --port COM5
```

若接收端的 COM 號改變，先用裝置管理員查看，或執行 `python -m serial.tools.list_ports`，再把 `COM5` 換成實際號碼。缺少 `serial` 套件時執行 `python -m pip install pyserial`。結束監看按 `Ctrl+C`。

`COM5` 同一時間只能被一個程式開啟：關閉 Arduino 序列監控器、`receive_rtcm.py` 或其他占用 `COM5` 的程式。這裡的 `COM5` 是**接收端 LoRa**，不是 Black Pill 除錯埠。

想把本次輸出存成文字檔時，改用這組指令：

```powershell
New-Item -ItemType Directory -Force .\runtime\field_test | Out-Null
python .\lora\rylr\monitor_pc.py --port COM5 2>&1 | Tee-Object -FilePath .\runtime\field_test\monitor_com5.txt
```

有資料時會看到類似：

```text
[packet 362] bytes=28 RSSI=-6 SNR=10 hex=d3 00 16 ...
  [RTCM 362] type=1074 bytes=28 CRC=OK sats=0 sigs=0 obs=0 EMPTY
[summary] packets=... CRC_OK=... CRC_BAD=... malformed_radio=... MSM_with_obs=... MSM_empty=...
```

## 4. 判斷測試結果

| 畫面 | 代表什麼 |
| --- | --- |
| `[packet]` 持續增加 | 接收端 LoRa 已收到來自地址 69 的無線資料 |
| `[RTCM] CRC=OK`、`CRC_OK` 持續增加 | 收到的 RTCM 封包通過 CRC 驗證，封包內容完整 |
| `CRC_BAD` 或 `malformed_radio` 增加 | 有毀損封包、串口資料異常或無線接收資料不完整，記下數值與測試距離 |
| `MSM_with_obs` 增加，且 `sats`、`obs` 大於 0 | 基站有送出實際衛星觀測資料；這是本次測試要確認的重點 |
| `MSM_empty` 增加、`sats=0 obs=0 EMPTY` | 封包傳輸正常，但基站送來的觀測訊息是空的；檢查基站 GNSS 天線、搜星狀態與 RTCM 輸出設定 |

`type=1005` 是基站座標訊息；`1074`、`1084`、`1094`、`1114`、`1124` 等是 MSM 觀測訊息。你之前在室內看到的 `CRC=OK` 已證明這條 LoRa 傳輸鏈可用，但當時的部分 MSM 訊息 `sats=0 obs=0`，所以到戶外要特別看 `MSM_with_obs` 是否開始增加。

這支監看程式只檢查**有收到的封包**。它沒有封包序號，不能單靠 `CRC_BAD=0` 證明途中完全沒有漏包，也不能證明 rover 已得到 RTK FIX。現場可在不同距離各跑幾分鐘，記下時間、距離、`packets`、`CRC_OK`、`CRC_BAD`、`malformed_radio`、`MSM_with_obs`、`MSM_empty`、RSSI、SNR。

## 5. 若 COM5 沒有資料：看 Black Pill 除錯

若已接另一個 USB-TTL 到 Black Pill `PA9`，在 **另一個 Windows PowerShell** 開啟它的 COM 埠（下面以 `COM7` 為例）：

```powershell
python -m serial.tools.miniterm COM7 115200
```

按 `Ctrl+]` 離開。剛打開時可按 Black Pill 的 `NRST` 看啟動訊息。這個除錯埠和接收端 `COM5` 是兩個不同裝置。

| Black Pill 日誌 | 優先檢查 |
| --- | --- |
| `BP stat rxB=0` 持續出現 | Linux 轉送程式、Linux TX → `PA10`、共地與 115200 鮑率 |
| `rxB>0`、`ok=0`、`noise` 很多 | `PA10` 收到的內容不是有效 RTCM；檢查波特率、接線與基站輸出 |
| `BP frame CRC=OK`、`ok>0` | Black Pill 已正確解出基站 RTCM |
| `sent` 持續增加，`radio=READY` | 發射端 RYLR998 已接受送出命令；若 COM5 仍空白，查接收端 LoRa 的供電、地址 67、天線和兩端射頻設定 |
| `radio=FAULT`，C13 快速持續閃 | 查看 `BP fault` 原因，檢查發射端 LoRa 的 3.3 V、`PA2/PA3` 接線與 AT 回應；排除後按 `NRST` |

`BP radio rx=+OK` 或 `sent` 增加，只表示**發射端模組接受了命令**；必須在 COM5 看到 `[packet]`，才算接收端真的收到。C13 偶爾短閃是發射端接受封包的提示。

## 現場最短指令清單

```text
基站 Linux：systemctl status stm-forward.service --no-pager
基站 Linux：pgrep -af stm_forward.py
Windows 專案根目錄：python .\lora\rylr\monitor_pc.py --port COM5
可選的 Black Pill 除錯：python -m serial.tools.miniterm COM7 115200
```
