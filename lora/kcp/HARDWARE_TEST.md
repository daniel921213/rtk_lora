# STM ↔ 筆電 KCP／RYLR 實機測試

測試日期：2026-09-29（以筆電時鐘為準；STM 系統時鐘有偏差）。

## 硬體與條件

- 筆電：Linux x86-64，`/dev/lora → /dev/ttyUSB0`，RYLR 地址 67。
- STM：`root@192.168.10.11`，STM32MP157 ARMv7 Linux，`/dev/lora → /dev/ttyUSB0`，RYLR 地址 69。
- 兩端查詢值一致：NETWORKID=18、BAND=915000000、PARAMETER=5,9,1,24、MODE=0；UART 115200 baud。未修改模組設定。
- 每秒最多 5 個無線 payload；每個 payload ≤240 bytes，含 KCP／CRC／COBS。AT UART 命令標頭不計入無線 payload。
- KCP MTU=234、單片資料最多 210 bytes、最小 RTO=3000 ms；檔案區塊 4096 bytes，未壓縮。
- 使用有線 SSH 部署與啟動程式、查看日誌及計算遠端雜湊；檔案內容經 LoRa 傳輸，未用 SSH/SCP 複製範例檔。
- 回傳來源直接使用 STM 經 LoRa 收到的檔案。

## 已完成結果

| 方向 | 檔案 | bytes | 收件端完成時間 | 發件端完成時間 | SHA-256 |
|---|---|---:|---:|---:|---|
| 筆電 → STM | JSON | 32,884 | 36.25 s | 43.51 s | 一致 |
| 筆電 → STM | PNG | 156,828 | 159.22 s | 166.38 s | 一致 |
| STM → 筆電 | JSON | 32,884 | 35.52 s | 40.24 s | 一致 |
| STM → 筆電 | PNG | 156,828 | 158.88 s | 163.58 s | 一致 |

時間從各自程式啟動計算，包含啟動／協定等待；發件端還包含最後確認的保留時間，不代表純空中傳輸時間。
各次成功收發程序均以 0 結束；成功日誌未偵測到 CRC／AT 解析錯誤，這不等於量測或證明無線零丟包。

## 原始檔雜湊

- `rosbag2_2026_02_25-12_40_24_0_rtk_trajectory.json`
  - SHA-256：`56a8325d0e1ff5a5955f2851b2276cd8c139bc46ab7398b403571d932b8c96b4`
- `rosbag2_2026_02_25-12_40_24_0_rtk_trajectory.png`
  - SHA-256：`84e8dbd5e8aeba82b3a371110ad09e767912b80a4f73cb0710b8836dfb857e96`

## 輸出與日誌

- conv `9292102`：[runtime/kcp-hardware/20260929-211006-9292102](../../runtime/kcp-hardware/20260929-211006-9292102/result.json)
  - 收檔：`/home/root/lora/kcp/results/20260929-211006-rosbag2_2026_02_25-12_40_24_0_rtk_trajectory.json`
- conv `9292103`：[runtime/kcp-hardware/20260929-211104-9292103](../../runtime/kcp-hardware/20260929-211104-9292103/result.json)
  - 收檔：`/home/root/lora/kcp/results/20260929-211104-rosbag2_2026_02_25-12_40_24_0_rtk_trajectory.png`
- conv `9292104`：[runtime/kcp-hardware/20260929-211409-9292104](../../runtime/kcp-hardware/20260929-211409-9292104/result.json)
  - 收檔：`/home/lao2/ros2_ws/src/rtk_dual_link_toolkit/runtime/kcp-hardware/20260929-211409-9292104/rosbag2_2026_02_25-12_40_24_0_rtk_trajectory.json`
- conv `9292105`：[runtime/kcp-hardware/20260929-211452-9292105](../../runtime/kcp-hardware/20260929-211452-9292105/result.json)
  - 收檔：`/home/lao2/ros2_ws/src/rtk_dual_link_toolkit/runtime/kcp-hardware/20260929-211452-9292105/rosbag2_2026_02_25-12_40_24_0_rtk_trajectory.png`

原始執行日誌與回傳檔保存在 `runtime/kcp-hardware/`（依專案規則忽略於 Git）。

## 本次修正與軟體驗證

- 第一輪透明串口測試未傳成功；原始探測收到 `+ERR=1`，查詢確認為 RYLR AT 模組，改用 `--radio rylr` 後成功。
- 新增 `file_transfer.py`：分塊傳送、長度／SHA-256 驗證、暫存檔清理、原子且不覆寫的收檔、遠端驗證回覆。
- 新增 `radio_io.py`：使用既有 RYLR binary-safe parser，等待本地 +OK 同時處理遠端 KCP 資料。
- ARM `.so` 在筆電使用 GCC 12 ARM hard-float toolchain 交叉編譯，已在 STM Python 3.12 載入並實機執行；未修改 STM 系統套件或 Wi-Fi。
- `make -C lora/kcp test`：11 項通過，包含實際 PNG 的 PTY／RYLR 模擬收發、遺失／亂序／損壞、CRC、檔案 SHA-256 失敗及禁止覆寫。
- 既有 RYLR transport 的 10 項單元測試通過。

操作命令見 [README](README.md)。
