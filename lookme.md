# ubuntu
`sudo chmod 666 /dev/ttyUSB0`  open port


# base
`rtk-base-tcp.service` origin service

`systemctl enable rtk-base-tcp.service` enable
`systemctl status rtk-base-tcp.service` look status
`systemctl start rtk-base-tcp.service` start
`systemctl stop rtk-base-tcp.service` stop



# rover lora
```bash
python3 /home/lao2/ros2_ws/src/rtk_dual_link_toolkit/rover/lora_gga_bridge.py \
  --lora-port /dev/ttyUSB1 \
  --gnss-port /dev/ttyUSB0
```

# rover tcp
```bash
python3 /home/lao2/ros2_ws/src/rtk_dual_link_toolkit/rover/tcp_gga_track.py \
  --host 192.168.10.11 --tcp-port 2101 \
  --gnss-port /dev/ttyUSB1 --baud 115200
```

# port test
`python3 ~/ros2_ws/src/rtk_dual_link_toolkit/lora_setting/lora_test.py`