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
    dest=67,
    payload_size=240,
    packet_gap=0.01,
    ok_timeout=3.0,
    str2str=None,
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
        dest=dest, payload_size=payload_size, packet_gap=packet_gap,
        ok_timeout=ok_timeout, str2str=str2str,
    )
    try:
        rover.set_base()
        rover.set_server()
    except Exception as e:
        print("base startup failed:", e)
        print("hint: check serial port, baudrate(115200), GNSS NMEA output, and PLSR response")
        raise
    finally:
        rover.ser.close()


def parse_args(argv):
    parser = argparse.ArgumentParser(description="RTK base startup with raw RTCM over RYLR AT+SEND")
    parser.add_argument("port", nargs="?", default="/dev/rtk_gps", help="GNSS command port, e.g. /dev/ttyUSB1")
    parser.add_argument("server_ip", nargs="?", default="192.168.10.11", help="TCP server bind IP for tcp/both mode")
    parser.add_argument("--mode", choices=["tcp", "lora", "both"], default="lora", help="output mode") # both
    parser.add_argument("--lora-port", default="/dev/lora", help="LoRa serial port for lora/both mode") #dev/lora
    parser.add_argument("--lora-baud", type=int, default=115200, help="LoRa baudrate")
    parser.add_argument('--dest', type=int, default=67)
    parser.add_argument('--payload-size', type=int, choices=range(1, 241), default=240, metavar='1..240')
    parser.add_argument('--packet-gap', type=float, default=0.01)
    parser.add_argument('--ok-timeout', type=float, default=3.0)
    parser.add_argument('--str2str', help='str2str executable for tcp/both mode')
    parser.add_argument("--tcp-port", type=int, default=2101, help="TCP server port")
#2502.5903509", "lon": "12132.0629832", "alt": 27.177999999999997
#b'$GNGGA,102427.200,2502.5392501,N,12132.1121059,E,4,57,0.48,16.290,M,15.2,M,0,3335*75\r\n
#2502.5384271", "lon": "12132.1127602", "alt": 26.97
    parser.add_argument("--base-pos", nargs=3, type=float, default=[2502.5384271, 12132.1127602, 26.97], help="Lat Lon Alt")
    #parser.add_argument("--base-pos", nargs=3, type=float, default=None, help="Lat Lon Alt")
    args = parser.parse_args(argv)
    if not 0 <= args.dest <= 65535 or args.packet_gap < 0 or args.ok_timeout <= 0:
        parser.error('dest must be 0..65535, packet-gap >= 0, ok-timeout > 0')
    return args


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
        dest=args.dest, payload_size=args.payload_size, packet_gap=args.packet_gap,
        ok_timeout=args.ok_timeout, str2str=args.str2str,
    )


if __name__ == "__main__":
    main(sys.argv[1:])
