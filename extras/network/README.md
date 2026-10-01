# Network 腳本（STM + 電腦端）

這個資料夾收錄「STM 端實際使用中」的網路與 Tailscale 手動啟動腳本副本，並提供電腦端一鍵遠端執行工具。

## 檔案說明

- `stm_wifi_up.sh`
  - 在 STM 端啟用 `wlu1u2` Wi-Fi，嘗試 DHCP，並做外網檢查。
  - 會優先保護 USB 管理路徑（避免 SSH 管理連線被搶走）。
- `stm_tailscale_up.sh`
  - 在 STM 端手動啟動 `tailscaled` 並執行 `tailscale up --accept-dns=true`。
  - 預設不做開機自啟動設定。
- `stm_net_tailscale_up.sh`
  - STM 端整合入口：先 Wi-Fi，再 Tailscale。
- `pc_run_stm_network.sh`
  - 電腦端腳本：透過 SSH 觸發 STM 的上述腳本。

## STM 端手動執行

```bash
# 只連 Wi-Fi
/usr/sbin/stm_wifi_up.sh

# 只啟動 tailscale（可帶 TS_AUTHKEY）
TS_AUTHKEY='tskey-auth-xxxx' /usr/sbin/stm_tailscale_up.sh

# 一次完成 Wi-Fi + tailscale
TS_AUTHKEY='tskey-auth-xxxx' /usr/sbin/stm_net_tailscale_up.sh
```

## 電腦端遠端執行

在 `rtk_dual_link_toolkit/extras/network/`：

```bash
chmod +x pc_run_stm_network.sh

# 預設動作：up（Wi-Fi + tailscale）
./pc_run_stm_network.sh

# 指定 auth key
TS_AUTHKEY='tskey-auth-xxxx' ./pc_run_stm_network.sh up

# 只連 Wi-Fi
./pc_run_stm_network.sh wifi

# 只啟動 tailscale
TS_AUTHKEY='tskey-auth-xxxx' ./pc_run_stm_network.sh tailscale

# 看狀態
./pc_run_stm_network.sh status
```

可用環境變數：

- `STM_HOST`（預設 `100.74.38.74`）
- `STM_USER`（預設 `root`）
- `TS_AUTHKEY`（選填，給 `up`/`tailscale`）

## 同步提醒

`stm_*.sh` 是從 STM `/usr/sbin/` 複製進來的副本。  
若你在 STM 上更新腳本，建議再同步回本資料夾，避免版本漂移。
