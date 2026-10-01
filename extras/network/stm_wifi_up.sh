#!/bin/sh
set -eu

IFACE="${IFACE:-wlu1u2}"
SSID="${SSID:-Hanilao2}"
PASS="${PASS:-ha5lao28}"
COUNTRY="${COUNTRY:-TW}"
WAIT_SEC="${WAIT_SEC:-35}"
CHECK_IP="${CHECK_IP:-8.8.8.8}"

if ! ip link show "$IFACE" >/dev/null 2>&1; then
  echo "ERROR: interface $IFACE not found"
  exit 1
fi

# Keep current USB-C SSH path pinned if connected over usb0.
if [ -n "${SSH_CLIENT:-}" ]; then
  CLIENT_IP=$(echo "$SSH_CLIENT" | awk '{print $1}' | sed 's/^::ffff://')
  [ -n "$CLIENT_IP" ] && ip route replace "${CLIENT_IP}/32" dev usb0 src 192.168.7.1 metric 0 || true
fi

mkdir -p /etc/wpa_supplicant
cat > "/etc/wpa_supplicant/wpa_supplicant-${IFACE}.conf" <<EOCFG
ctrl_interface=/run/wpa_supplicant
update_config=1
ap_scan=1
country=${COUNTRY}
network={
    ssid="${SSID}"
    scan_ssid=1
    psk="${PASS}"
    key_mgmt=WPA-PSK
}
EOCFG
chmod 600 "/etc/wpa_supplicant/wpa_supplicant-${IFACE}.conf"

ip link set "$IFACE" up
systemctl restart "wpa_supplicant@${IFACE}"

state=""
i=0
while [ "$i" -lt "$WAIT_SEC" ]; do
  i=$((i+1))
  state=$(wpa_cli -i "$IFACE" status 2>/dev/null | awk -F= '/^wpa_state=/{print $2}')
  [ "$state" = "COMPLETED" ] && break
  sleep 1
done

if [ "$state" != "COMPLETED" ]; then
  echo "ERROR: Wi-Fi not associated (state=${state:-unknown})."
  echo "Hint: verify SSID is nearby and on 2.4GHz, then retry."
  wpa_cli -i "$IFACE" scan_results | sed -n '1,15p' || true
  exit 2
fi

# Request DHCP lease (udhcpc stays in background for renewals).
udhcpc -i "$IFACE" -q -n -t 10 -T 3

# Ensure resolv.conf has DNS entries.
if ! grep -q '^nameserver ' /etc/resolv.conf 2>/dev/null; then
  cat > /etc/resolv.conf <<EODNS
nameserver 1.1.1.1
nameserver 8.8.8.8
EODNS
fi

echo "Wi-Fi connected on $IFACE"
ip -4 -o addr show dev "$IFACE" || true
ip route | sed -n '1,10p'
cat /etc/resolv.conf | sed -n '1,5p'

ping -c 1 -W 3 "$CHECK_IP" >/dev/null
echo "Internet check OK via $CHECK_IP"
