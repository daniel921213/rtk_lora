#!/usr/bin/env bash
set -euo pipefail

STM_HOST="${STM_HOST:-100.74.38.74}"
STM_USER="${STM_USER:-root}"
TS_AUTHKEY="${TS_AUTHKEY:-}"

usage() {
  cat <<'EOF'
Usage:
  pc_run_stm_network.sh [up|wifi|tailscale|status]

Environment variables:
  STM_HOST    STM Tailscale IP or hostname (default: 100.74.38.74)
  STM_USER    SSH user (default: root)
  TS_AUTHKEY  Optional tailscale auth key passed to STM when running up/tailscale
EOF
}

ACTION="${1:-up}"
TARGET="${STM_USER}@${STM_HOST}"

run_remote() {
  ssh -o ConnectTimeout=8 "$TARGET" "$@"
}

run_remote_with_key() {
  local remote_cmd="$1"
  if [[ -n "$TS_AUTHKEY" ]]; then
    run_remote "export TS_AUTHKEY='$TS_AUTHKEY'; $remote_cmd"
  else
    run_remote "$remote_cmd"
  fi
}

case "$ACTION" in
  up)
    run_remote_with_key "/usr/sbin/stm_net_tailscale_up.sh"
    ;;
  wifi)
    run_remote "/usr/sbin/stm_wifi_up.sh"
    ;;
  tailscale)
    run_remote_with_key "/usr/sbin/stm_tailscale_up.sh"
    ;;
  status)
    run_remote '
      set -e
      echo "HOST: $(hostname)"
      echo "--- wlu1u2 ---"
      ip -4 -o addr show dev wlu1u2 || true
      echo "--- route ---"
      ip route | sed -n "1,12p"
      echo "--- tailscaled ---"
      printf "enabled: "; systemctl is-enabled tailscaled.service || true
      printf "active : "; systemctl is-active tailscaled.service || true
      echo "--- tailscale ip ---"
      tailscale --socket=/run/tailscale/tailscaled.sock ip -4 2>/dev/null || true
      echo "--- resolv.conf ---"
      sed -n "1,8p" /etc/resolv.conf || true
    '
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    echo "Unknown action: $ACTION" >&2
    usage
    exit 1
    ;;
esac
