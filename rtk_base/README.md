# rtk_base

`rtk_base` 用來把 GNSS 接收機切到 Base 模式，並把 RTCM 修正資料透過 `str2str` 轉發到 lora 或 network(tcp)。

此套件本身不依賴 ros

- stm 上已做開機自啟動
    - 檔案位置：`/etc/systemd/system/rtk-base-tcp.service`
    - 啟用服務：`systemctl enable rtk-base-tcp.service`
    - 停止服務：`systemctl stop rtk-base-tcp.service`
    - 手動開啟：`systemctl start rtk-base-tcp.service`
    - 查看狀態：`systemctl status rtk-base-tcp.service`

## 目錄與主要腳本

- `setting_base.py`: 入口腳本（先 `set_base()`，再 `set_server()`）
- `rtk_base.py`: Base 設定與串流主邏輯

- `setup_ros_env.sh`: 建立 `venv`、編譯 RTKLIB `str2str`、安裝 Python 依賴
- `set_vpn.py`: VPN 腳本，使用北科 vpn 發送 rtcm 時用。

## 目前程式行為（依現況）

`setting_base.py` 預設會：

1. 以固定座標設定 Base（`rtk_base.py` 內目前硬編碼）：
   - `lat(ddmm)=2502.5945000`
   - `lon(ddmm)=12132.1615000`
   - `alt=46.3`
2. 啟動：str2str 轉發

# 環境安裝（不分 ROS1/ROS2）

```bash
cd ~/ros2_ws/src/rtk_base
bash setup_ros_env.sh
source venv/bin/activate
```

`setup_ros_env.sh` 會安裝：

- `pygnssutils`
- `pyserial`
- RTKLIB 的 `str2str`（複製到 `venv/bin/str2str`）
- 其腳本名稱雖為 `setup_ros_env.sh`，但實際上是通用 Python/RTKLIB 環境初始化

# 啟動方式

使用預設值（`/dev/ttyUSB0`, `10.42.0.1`）：

```bash
cd ~/ros2_ws/src/rtk_base
source venv/bin/activate
./setting_base.py
```


# 注意事項

- `rtk_base.py` 中有 `get_setting_gps()`（可由 NTRIP 取座標）函式，但目前 `set_base()` 使用的是硬編碼座標。
- `setting_base.py` 內含 `sudo chmod 777 <port>`，執行時可能需要 sudo 權限。
- 程式會直接呼叫系統命令 `str2str`，若命令不存在請確認 `venv/bin` 在 PATH 中，或改用完整路徑。

# 疑難排解

- 找不到序列埠：
  - 檢查線材與權限，確認裝置名稱（`/dev/ttyUSB*`）。
- `set position error` / `set frequency error` 持續出現：
  - 檢查接收機是否回應 `$PLSR` 訊息，以及傳輸率是否一致（115200）。
- `str2str: not found`：
  - 重新執行 `setup_ros_env.sh`，或確認 `RTKLIB/app/str2str/gcc/str2str` 已編譯成功。

---

# RTCM + LoRa 透明傳輸測試工具

## 檔案

- `measure_rtcm_rate.py`: 量測 base RTCM source 真實輸出速率

## 依賴

- Python 3.8+
- `pyserial`

安裝：

```bash
pip install pyserial
```

## 量測 RTCM source 速率

- 評估 rtcm 資料量

```bash
python3 extras/measure_rtcm_rate.py \
  --port /dev/ttyUSB0 \
  --baud 115200 \
  --duration 600 \
  --out-dir logs/rate_test \
  --tag base_rtcm
```

輸出：

- `logs/rate_test/base_rtcm/rate_log.csv`：每秒資料量及累計資料量
- `logs/rate_test/base_rtcm/summary.json`：平均、最大、最小、P95 等統計

## 不同衛星資料傳送

```bash
systemctl stop rtk-base-tcp.service
python3 extra/rtcm_profile_rotator.py --profile A --mode tcp
```
- A:最小
- B:
- C:
- D:
- E:
- F:原始
---

## STM32 原始 RTK 資料轉發至 USART3

`stm_forward.py` 將輸入串口收到的 bytes 原封不動送到 USART3，不解碼、
不移除空白、不篩選 RTCM，也不加封包或換行。預設輸入為 `/dev/rtk_gps`
（RTK 接收機串口），輸出為 `/dev/ttySTM1`（CN2 pin8 TX、pin10 RX）。
兩端預設 115200、8N1，無流量控制；此程式只做單向轉發。

在 STM32 上執行：

```bash
python3 stm_forward.py
# 若 RTK 接收機接在其他串口：
python3 stm_forward.py --input-port /dev/ttyUSB1 --input-baud 115200
```

可用 `--output-port` 與 `--output-baud` 修改輸出。需先啟用 USART3、
確認輸入裝置存在，並停止其他讀取同一串口的程式。
輸入持續速率需小於等於輸出可承受速率，以免串口緩衝區溢位。
串口錯誤或寫入逾時會停止並回傳非零退出碼，不自動重送，避免重複資料。

同時保留 TCP 與 USART3 輸出（同一個程式讀取 RTK 串口）：

```bash
python3 stm_forward.py --input-port /dev/rtk_gps --tcp-port 2101
```

TCP 預設綁定 `0.0.0.0`，客戶端連至 STM32 的 `192.168.10.11:2101`。
TCP 與 USART3 都輸出原始 bytes；新連線從當下串流開始，不補送歷史資料。
最多 16 個 TCP 客戶端，每個最多緩衝 256 KiB；過慢的客戶端會斷線，
以免阻塞 USART3。TCP 僅供接收，不會把客戶端命令送回 GNSS。
STM32 使用 `stm-forward.service` 開機啟動；舊 `rtk-base-tcp.service`
保持停用，避免重複讀取 `/dev/rtk_gps`。
