#!/usr/bin/env python3
import argparse
import os
import sys
from rtk_base_stm import rtk_base


def _chmod_port(port):
    chmod_cmd = "chmod 777 " + port
    if os.geteuid() == 0:
        os.system(chmod_cmd)
    else:
        os.system("sudo " + chmod_cmd)


def excute(
    port="/dev/rtk_gps",
    server_ip="192.168.10.11",
    mode="tcp",
    lora_port="/dev/ttyUSB0",
    lora_baud=115200,
    tcp_port=2101,
    base_pos=None,
):
    _chmod_port(port)
    if mode in ("lora", "both") and lora_port != port:
        _chmod_port(lora_port)

    rover = rtk_base(
        port,
        server_ip,
        output_mode=mode,
        lora_port=lora_port,
        lora_baud=lora_baud,
        tcp_port=tcp_port,
        base_pos=base_pos, 
    )
    try:
        rover.set_base()
        rover.set_server()
    except Exception as e:
        print("base startup failed:", e)
        print("hint: check serial port, baudrate(115200), GNSS NMEA output, and PLSR response")
        raise


def parse_args(argv):
    parser = argparse.ArgumentParser(description="RTK base startup script (STM)")
    parser.add_argument("port", nargs="?", default="/dev/rtk_gps", help="GNSS command port, e.g. /dev/ttyUSB1")
    parser.add_argument("server_ip", nargs="?", default="192.168.10.11", help="TCP server bind IP for tcp/both mode")
    parser.add_argument("--mode", choices=["tcp", "lora", "both"], default="both", help="output mode") # both
    parser.add_argument("--lora-port", default="/dev/lora", help="LoRa serial port for lora/both mode") #dev/lora
    parser.add_argument("--lora-baud", type=int, default=115200, help="LoRa baudrate")
    parser.add_argument("--tcp-port", type=int, default=2101, help="TCP server port")
#2502.5903509", "lon": "12132.0629832", "alt": 27.177999999999997
#b'$GNGGA,102427.200,2502.5392501,N,12132.1121059,E,4,57,0.48,16.290,M,15.2,M,0,3335*75\r\n
#2502.5384271", "lon": "12132.1127602", "alt": 26.97
    parser.add_argument("--base-pos", nargs=3, type=float, default=[2502.5384271, 12132.1127602, 26.97], help="Lat Lon Alt")
    #parser.add_argument("--base-pos", nargs=3, type=float, default=None, help="Lat Lon Alt")
    return parser.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    excute(
        port=args.port,
        server_ip=args.server_ip,
        mode=args.mode,
        lora_port=args.lora_port,
        lora_baud=args.lora_baud,
        tcp_port=args.tcp_port,
        base_pos=args.base_pos,
    )


if __name__ == "__main__":
    main(sys.argv[1:])
