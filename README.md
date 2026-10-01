# RTK Dual Link Toolkit

RTCM 透傳工具，依 Base、Rover 與額外工具分開存放。

## 重點對齊你的需求

- 不做開機自啟動（無 systemd 安裝流程）
- 支援 base 模式切換：
  - 輪詢模式（180 秒）
  - 指定 profile 模式（A~F）
- 切換模式前會先停止前一個可能殘留的發送程序
- base 支援 `tcp` / `lora` / `both`
  - `both`：TCP + LoRa（RTCM 透傳）同時送
  - `tcp`：僅 TCP
  - `lora`：僅 LoRa（str2str 直接輸出 RTCM 到串口）
- rover 支援來源切換：
  - `tcp`（走一般 IP 網路）
  - `lora`（LoRa 串口原始資料直接轉送到 GNSS）
- RTK / LoRa 預設 baud 都是 `115200`
- 初始化流程會檢查接收機是否有 GGA
- LoRa 主流程不加 COBS 封裝、分片或 ACK，不加入假延遲；RTCM 訊息頻率由 profile 決定
- 兩端 LoRa 模組需預先設定為 UART 透傳模式，無線參數一致；本工具不修改模組設定
- 額外 LoRa 假資料測試：100/200/300/400/500B 輪詢
  - 電腦端終端只顯示：`id / size / elapsed / Bps`
  - 其餘重傳細節僅寫入 log

## 透傳需要哪些程式

資料流：Base GNSS → str2str → Base LoRa → 無線 → Rover LoRa → Rover GNSS。

| 端點 | 檔案 | 用途 |
| --- | --- | --- |
| Base | `base/switch_base_mode.sh` | 啟動、停止、切換模式的入口 |
| Base | `base/base_mode_controller.py` | 讀取設定、初始化與管理轉發程序 |
| Base | `base/init_base_receiver.py` + `base/rtk_base_stm.py` | 初始化 Base 接收機 |
| Base | `base/rtcm_profile_rotator.py` | 執行 str2str，選擇／輪詢 RTCM profile |
| Base | `base/configs/` + `base/profiles/` | 串口、傳輸模式與 RTCM 訊息設定 |
| Rover | `rover/init_rover_receiver.py` | 初始化 Rover 接收機 |
| Rover | `rover/rover_source_switch.sh` | 用 str2str 在背景轉送 LoRa／TCP 修正資料 |
| Rover | `rover/lora_gga_bridge.py` | 替代轉送入口：透傳 LoRa，並透過同一個 GNSS 連線讀取 GGA |

Rover 的兩個轉送入口擇一執行。`lora_gga_bridge.py` 本身不初始化接收機，使用前先執行初始化。
`rover/configs/` 是設定參考，目前 shell 入口使用環境變數，**不會讀取這些 JSON**。

## 目錄

```text
base/
  configs/                 Base 設定
  profiles/                A～F RTCM profiles
  *.py、switch_base_mode.sh Base 初始化與轉發
rover/
  configs/                 Rover 設定參考
  init_rover_receiver.py
  rover_source_switch.sh
  lora_gga_bridge.py
  tcp_gga_track.py          TCP 接收與軌跡記錄
  read_gga.py               GGA 讀取工具
  monitor_rover_quality.py  定位品質查看工具
extras/
  lora_setting/            LoRa 模組設定與串口測試
  network/                 Wi-Fi、Tailscale、SSH 輔助腳本
  tools/                   舊版假 RTCM 測試
  rtcm_lora_link/           舊版 COBS／分片／ACK 協定
  diagnostics/             原 base 目錄中的定位品質診斷工具
  lookme.md                原操作筆記
runtime/                   執行時自動產生的 PID、狀態與紀錄
```

一般 LoRa 透傳不需要 `extras/rtcm_lora_link` 或 `extras/tools`。LoRa 模組的 UART 透傳與無線參數需事先配置一致。
部署時保留 `base/` 或 `rover/` 資料夾這一層；各端主流程不需複製另一端資料夾。

## 快速使用

## 1) Base（STM）

先初始化 base（確認 GGA）：

```bash
python3 base/init_base_receiver.py --port /dev/ttyUSB1
```

模式切換（會先停前一模式，會做初始化）：

```bash
# 輪詢 180s + TCP+LoRa
./base/switch_base_mode.sh rotate-both

# 輪詢 180s + TCP only
./base/switch_base_mode.sh rotate-tcp

# 輪詢 180s + LoRa only
./base/switch_base_mode.sh rotate-lora

# 指定 profile（A~F）
./base/switch_base_mode.sh fixed B

# 查狀態 / 停止
./base/switch_base_mode.sh status
./base/switch_base_mode.sh stop
```

## 2) Rover

初始化 rover 接收機（確保 GGA + PAIR431）：

```bash
python3 rover/init_rover_receiver.py --port /dev/ttyUSB1
```

切換修正來源(會做初始化)：

```bash
# LoRa（透傳）
./rover/rover_source_switch.sh lora

# TCP
./rover/rover_source_switch.sh tcp 100.125.163.60

# 狀態 / 停止
./rover/rover_source_switch.sh status
./rover/rover_source_switch.sh stop

```

同時透傳並查看 GGA（先停止其他轉送程序，再初始化）：

```bash
./rover/rover_source_switch.sh stop
python3 rover/init_rover_receiver.py --port /dev/ttyUSB1
python3 rover/lora_gga_bridge.py --lora-port /dev/ttyUSB0 --gnss-port /dev/ttyUSB1
```

此 bridge 只需 `pyserial`，不需 `str2str`。串口請依實際接線調整。

獨立查看定位品質（先停止占用同一 GNSS 串口的程式）：

```bash
python3 rover/monitor_rover_quality.py --port /dev/ttyUSB1 --baud 115200 --duration 60
```

## 3) 舊版 COBS/ACK 假資料測試（100/200/300/400/500B）

以下工具仍使用舊版 COBS/ACK 協定，僅供成對測試，不可與目前的透傳主流程混用。

STM 端發送：

```bash
python3 extras/tools/stm_fake_rtcm_sweep_sender.py \
  --out-serial /dev/ttyUSB1 --out-baud 115200 \
  --hz 8 --sizes 100,200,300,400,500 \
  --repeat 100000000
```

電腦端接收（終端會印 id/size/elapsed/Bps）：

```bash
python3 extras/tools/pc_fake_rtcm_sweep_receiver.py \
  --in-serial /dev/ttyUSB0 \
  --baud 115200
```

## 4) 依賴

Base 與 Rover shell 入口的轉發皆需 `str2str`（Rover 可用 `STR2STR=/path/to/str2str` 指定路徑）。

接收機初始化與舊版測試工具的 Python 依賴：

```bash
pip install pyserial
```

`extras/rtcm_lora_link/requirements.txt` 也可直接安裝。

## 5) STM + 電腦端網路腳本

已把 STM 上使用的網路腳本副本放進 `extras/network/`，同時新增電腦端輔助腳本：

```bash
cd extras/network
./pc_run_stm_network.sh status
TS_AUTHKEY='tskey-auth-xxxx' ./pc_run_stm_network.sh up
```

完整操作請看：

- `extras/network/README.md`



