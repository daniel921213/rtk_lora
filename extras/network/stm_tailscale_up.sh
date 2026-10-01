#!/bin/sh
set -eu

SOCK="/run/tailscale/tailscaled.sock"
AUTHKEY="${TS_AUTHKEY:-}"
TS_UP_TIMEOUT="${TS_UP_TIMEOUT:-30}"

mkdir -p /var/lib/tailscale /run/tailscale

if ! command -v tailscaled >/dev/null 2>&1 || ! command -v tailscale >/dev/null 2>&1; then
  echo "ERROR: tailscale/tailscaled not installed"
  exit 1
fi

# Fast fail when Internet is unavailable.
if ! ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1; then
  echo "ERROR: Internet not ready. Run /usr/sbin/stm_wifi_up.sh first."
  exit 3
fi

systemctl start tailscaled

# Wait for tailscaled socket.
i=0
while [ "$i" -lt 20 ]; do
  i=$((i+1))
  [ -S "$SOCK" ] && break
  sleep 1
done

if [ ! -S "$SOCK" ]; then
  echo "ERROR: tailscaled socket not ready: $SOCK"
  exit 2
fi

run_tailscale_up() {
  if [ -n "$AUTHKEY" ]; then
    tailscale --socket="$SOCK" up --accept-dns=true --authkey="$AUTHKEY" "$@"
  else
    tailscale --socket="$SOCK" up --accept-dns=true "$@"
  fi
}

if command -v timeout >/dev/null 2>&1; then
  set +e
  if [ -n "$AUTHKEY" ]; then
    timeout "$TS_UP_TIMEOUT" tailscale --socket="$SOCK" up --accept-dns=true --authkey="$AUTHKEY" "$@"
    rc=$?
  else
    timeout "$TS_UP_TIMEOUT" tailscale --socket="$SOCK" up --accept-dns=true "$@"
    rc=$?
  fi
  set -e

  if [ "$rc" -eq 124 ]; then
    echo "ERROR: tailscale up timed out after ${TS_UP_TIMEOUT}s"
    exit 4
  fi
  [ "$rc" -eq 0 ] || exit "$rc"
else
  run_tailscale_up "$@"
fi

echo "tailscale status:"
tailscale --socket="$SOCK" status
