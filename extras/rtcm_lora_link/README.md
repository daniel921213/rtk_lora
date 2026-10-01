# RTCM over LoRa (UART Transparent) Reliable Link

> 此目錄是舊版 COBS/ACK 可靠鏈路與測試工具；base/rover 模式切換主流程已改用 str2str 串口透傳，不使用此協定。

這個專案提供一套可在 Python 3.10+ 執行的「RTCM over LoRa(UART transparent)」可靠傳輸原型，重點是：

- 先從 byte stream 解析完整 RTCM3 frame（含 CRC24Q 驗證）
- 再做 transport fragmentation/reassembly
- 使用 COBS + `0x00` delimiter 做 serial 封包邊界
- 使用 ACK-on-Error bitmap 只補缺片

專案可在 Linux/WSL 測試，依賴僅 `pyserial`、`pytest`。

## 1. 設計說明

### 為何不用 raw stream 固定切片
raw stream 沒有可靠邊界，遇到丟包/亂序/插入雜訊後，接收端很難知道切片對齊點，容易整段錯位。

### 為何先辨識完整 RTCM frame
RTCM3 本身有 preamble 與 CRC24Q。先在 sender 端取得完整合法 frame，可把 transport 協定與上層訊息邊界對齊，重組後也可再用 CRC24Q 交叉驗證。

### 為何用 COBS framing
COBS 編碼後資料不含 `0x00`，能在 UART stream 中穩定使用 `0x00` 當 delimiter，解碼錯誤可局部丟棄，不會拖垮整個流程。

### 為何同時做 fragment CRC16 + 最終 RTCM CRC24Q
- CRC16：保護每個 transport fragment，壞片可立即丟棄
- CRC24Q：保護最終重組的 RTCM frame，確保輸出給 GNSS 的內容完整正確

### 為何用 ACK-on-Error + bitmap
接收端以 bitmap 回報缺片，sender 只重傳缺的 `frag_idx`，降低不必要 retransmit。

## 2. 封包格式

### Fragment frame（未 COBS 前）

```text
+--------+--------+--------+--------+----------+----------+-------------+--------+
| ver(1) | msg_id(2)      | flags(1)| frag_idx | frag_cnt | payload_len | payload |
+--------+--------+--------+--------+----------+----------+-------------+--------+
| crc16(2)                                                                         |
+-----------------------------------------------------------------------------------+
```

`flags`:
- bit0 `START`
- bit1 `END`
- bit2 `ACK_REQ`
- bit3 `IS_ACK`
- bit4 `IS_NACK` (保留，現行主流程使用 ACK bitmap)

### ACK frame（未 COBS 前）

```text
+--------+--------+--------+--------+-----------+-----------+--------+--------+
| ver(1) | msg_id(2)      | flags(1)| frag_cnt(1)| bitmap_len | bitmap | status |
+--------+--------+--------+--------+-----------+-----------+--------+--------+
| crc16(2)                                                                      |
+--------------------------------------------------------------------------------+
```

`status`:
- `0 partial`
- `1 complete`
- `2 invalid`
- `3 abort`

bitmap 定義：bit=1 表示該 `frag_idx` 已收到。

### 最外層 serial framing

```text
wire_packet = COBS(transport_frame) + 0x00
```

## 3. 狀態機（文字版）

### Sender state machine
1. `WAIT_RTCM_FRAME`: 由 RTCM parser 取得完整 frame
2. `FRAGMENT_AND_SEND_WINDOW`: 傳送一個 window 內所有 pending fragments
3. `WAIT_ACK`: 等待 ACK bitmap
4. `PROCESS_ACK`:
   - `complete`: 結束該 msg_id
   - `partial`: 只重傳缺片
   - `invalid/abort`: 重試（超過 `max_retries` 放棄）
5. `NEXT_WINDOW` 或 `GIVE_UP`

### Receiver state machine
1. `WAIT_DELIMITED_FRAME`: 以 `0x00` 切出 frame
2. `COBS_DECODE`
3. `FRAGMENT_DECODE_AND_CRC16`
4. `REASSEMBLY_UPDATE`: 更新 bitmap
5. `SEND_ACK`: 回 partial/complete/invalid/abort
6. `ON_COMPLETE`: 重組完整 RTCM 並驗 CRC24Q，成功才輸出
7. `GC_TIMEOUT`: 超時 entry 清除並回 abort

### timeout / retry / abort
- sender `ack_timeout` 到期未收到 ACK -> retry
- sender 超過 `max_retries` -> give up
- receiver reassembly entry 超過 `reassembly_timeout` -> GC + abort ACK
- sender 收到 `invalid/abort` 會重試；若連續超限則放棄該 msg_id

## 4. 專案結構

```text
rtcm_lora_link/
  README.md
  requirements.txt
  protocol.py
  rtcm.py
  cobs_codec.py
  crc.py
  sender.py
  receiver.py
  ack.py
  reassembly.py
  serial_io.py
  link_simulator.py
  test_vectors.py
  tests/
    test_crc.py
    test_cobs.py
    test_rtcm_parser.py
    test_protocol_roundtrip.py
    test_reassembly_loss.py
    test_reassembly_reorder.py
    test_end_to_end_sim.py
```

## 5. 使用方法

## 安裝

```bash
cd rtcm_lora_link
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Sender

```bash
python3 sender.py \
  --in-serial /dev/ttyUSB0 \
  --baud 9600 \
  --out-serial /dev/ttyUSB1 \
  --out-baud 9600 \
  --frag-payload 32 \
  --window-size 4 \
  --ack-timeout 0.5 \
  --max-retries 5 \
  --log-file sender.log
```

檔案模式：

```bash
python3 sender.py \
  --in-file rtcm_dump.bin \
  --out-serial /dev/ttyUSB1 \
  --out-baud 9600 \
  --frag-payload 32 \
  --window-size 4
```

## Receiver

```bash
python3 receiver.py \
  --in-serial /dev/ttyUSB1 \
  --baud 9600 \
  --ack-serial /dev/ttyUSB1 \
  --ack-baud 9600 \
  --out-file received_rtcm.bin \
  --reassembly-timeout 5 \
  --log-file receiver.log
```

輸出到 GNSS serial：

```bash
python3 receiver.py \
  --in-serial /dev/ttyUSB1 \
  --baud 9600 \
  --out-serial /dev/ttyUSB0 \
  --out-baud 9600 \
  --enable-fake-delay \
  --fake-delay-min-s 2 \
  --fake-delay-max-s 5 \
  --output-interval-s 5
```

## Link simulator

```bash
python3 link_simulator.py \
  --input-file sample_rtcm.bin \
  --loss 0.1 \
  --reorder 0.05 \
  --dup 0.02 \
  --bitflip 0.001 \
  --min-delay-ms 10 \
  --max-delay-ms 80 \
  --frag-payload 32 \
  --window-size 4
```

## 測試

```bash
pytest -q
```

## 6. Logging 與 Metrics

sender/receiver 使用 JSON lines，重點欄位包含：

- Sender: `msg_id`, `frag_idx`, `frag_cnt`, `tx/retransmit`, `ack_received`, `missing_fragments`, `retry_count`, `msg_success/msg_give_up`
- Receiver: `fragment_crc`, `bitmap_state`, `reassembly_complete`, `rtcm_crc`, `output`

summary 指標：
- `total_rtcm_frames_in`
- `total_rtcm_frames_out`
- `total_fragments_tx`
- `total_fragments_retx`
- `fragment_loss_detected`
- `reassembly_success`
- `reassembly_fail`
- `avg_reassembly_latency`
- `max_reassembly_latency`

## 7. 已知限制

- 目前以「單方向單一 in-flight msg_id」為主，不做多 msg_id 交錯並行
- 先針對 UART half-duplex / 簡化 full-duplex 情境
- 尚未加入 FEC
- 尚未做動態速率調整
- receiver 目前收到 fragment 後即回 ACK，未做複雜 ACK 合併策略

## 8. 注意事項

- 不使用 `readline()` 做 binary 邊界
- payload 全程用 `bytes`，不做字串 `decode()`
- COBS delimiter 固定 `0x00`
- decode/CRC/索引錯誤只丟棄單包並記 log，不讓 process 崩潰

## 9. Phase 對照

### Phase 1 MVP（已完成）
- RTCM parser
- fragment/ack protocol
- COBS framing
- sender/receiver
- simulator
- pytest 基本測試
- 本地檔案模式 roundtrip

### Phase 2 加值（已完成）
- 結構化 logging
- metrics summary
- loss/reorder/dup/bitflip/delay 模擬
- README 封包格式與狀態機補全
- 端到端與重傳策略測試
