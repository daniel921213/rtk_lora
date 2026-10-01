#!/bin/sh
set -eu

/usr/sbin/stm_wifi_up.sh
/usr/sbin/stm_tailscale_up.sh "$@"
