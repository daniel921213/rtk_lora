#!/usr/bin/env python3
import serial
import time
import os
from queue import Queue
import json
import time
# from pygnssutils import GNSSNTRIPClient
try:
    from pygnssutils import GNSSNTRIPClient
except ImportError:
    GNSSNTRIPClient = None
#import subprocess
import math
import argparse
import shutil
import socket
import subprocess
from send_rtcm import run as send_rtcm

def convert_gps(ddmm):
    # 提取度數和分數
    ddmm = float(ddmm)
    degrees = int(ddmm // 100)
    minutes = ddmm - degrees * 100
    
    # 將分數轉換為度數
    decimal_degrees = degrees + (minutes / 60)
    
    return decimal_degrees

def trans_ecef(lat,lon,alt):
    # WGS84 constants
    a = 6378137.0         # Earth's semi-major axis in meters
    f = 1 / 298.257223563 # Earth's flattening
    e2 = f * (2 - f)      # Square of eccentricity
    # Convert latitude, longitude, and altitude to radians
    lat = convert_gps(lat)
    lon = convert_gps(lon)
    alt = float(alt)
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    
    # Calculate prime vertical radius of curvature
    N = a / math.sqrt(1 - e2 * math.sin(lat_rad)**2)
    
    # Calculate ECEF coordinates
    X = (N + alt) * math.cos(lat_rad) * math.cos(lon_rad)
    Y = (N + alt) * math.cos(lat_rad) * math.sin(lon_rad)
    Z = (N * (1 - e2) + alt) * math.sin(lat_rad)
    
    return X, Y, Z

def checksum(buf):
    checkSumL = 0
    checkSumR = 0
    buf_len = len(buf)

    for ind in range(buf_len):
        checkSumL ^= ord(buf[ind])  # Convert character to integer

    checkSumR = checkSumL & 0x0F
    checkSumL = (checkSumL >> 4) & 0x0F
    temp1 = chr(checkSumL + ord('A') - 10) if checkSumL >= 10 else chr(checkSumL + ord('0'))
    temp2 = chr(checkSumR + ord('A') - 10) if checkSumR >= 10 else chr(checkSumR + ord('0'))
    return temp1 + temp2

class rtk_base:
    def __init__(
        self,
        port,
        server_ip,
        output_mode="tcp",
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
        self.port = port
        self.server_ip = server_ip
        self.output_mode = str(output_mode).lower()
        self.lora_port = lora_port
        self.lora_baud = int(lora_baud)
        self.tcp_port = int(tcp_port)
        self.base_pos = base_pos
        self.dest = dest
        self.payload_size = payload_size
        self.packet_gap = packet_gap
        self.ok_timeout = ok_timeout
        self.str2str = str2str or shutil.which('str2str') or '/home/root/rtk_base/stm_bin/str2str'
        if self.output_mode in ('lora', 'both') and os.path.realpath(port) == os.path.realpath(lora_port):
            raise ValueError('GNSS and LoRa must use different serial ports')
        if self.output_mode not in ("tcp", "lora", "both"):
            raise ValueError(f"unsupported output_mode: {self.output_mode}")
        self.ser = serial.Serial(port=self.port, baudrate=115200, bytesize=8, parity='N', 
                            stopbits=1, timeout=1, rtscts=False, dsrdtr=False, exclusive=True)
        try:
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
        except Exception:
            pass

    def _send_command(self, payload):
        command = "$" + str(payload) + "*" + str(checksum(payload)) + "\r\n"
        self.ser.write(command.encode())
        self.ser.flush()
        print("tx:", payload)

    def _read_line(self):
        raw = self.ser.read_until(b"\n")
        if not raw:
            return ""
        return raw.decode(errors="ignore").strip()

    def _wait_plsr(self, key, timeout_s=10):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            line = self._read_line()
            if not line:
                continue
            if line.startswith("$PLSR"):
                print("rx:", line)
                msg = line.split(",")
                if len(msg) > 1 and msg[1] in [key, "ERR"]:
                    return msg
        return None

    def _serial_name(self, port_path):
        p = str(port_path).strip()
        if "/" in p:
            return os.path.basename(p)
        return p

    def reset(self):
        #self.wait_device()
        chbase = "PLSC,MCBASE,0"
        self._send_command(chbase)
        print("reset to rover")
        time.sleep(1)

    def wait_device(self, timeout_s=8):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            res_msg = self._read_line()
            if not res_msg:
                continue
            if res_msg.startswith("$GNGGA") or res_msg.startswith("$GPGGA"):
                msg = res_msg.split(',')
                if len(msg) > 11 and not (msg[2] == '' or msg[4] == '' or msg[9] == '' or msg[11] == ''):
                    return True
        return False

    def set_base(self):
        #self.wait_device()
        self.reset()
        if self.base_pos != None:
            base_gps = self.base_pos
            print(f"using setting base_pos : {base_gps}")
        else:
            print("getting base gps pose")
            base_gps = self.get_setting_gps()
        #$GNGGA,112522.000,2502.5607000,N,12132.1568000,E,1,49,0.61,30.1,M,15.2,M,1,3335*74\r\n'
        #'$GNGGA,114518.000,2502.5945000,N,12132.1615000,E,1,58,0.49,46.3,M,15.2,M,,*45\r\n'
        #base_gps = [2502.5945000,12132.1615000,46.3]
        #base_gps = [2502.6009000, 12132.0704000, 28.6]
#Received GPS: b'$GNGGA,115621.000,2502.5944729,N,12132.1610238,E,1,60,0.47,30.628,M,15.2,M,1,3335*70\r\n'
# Received GPS: b'$GNGGA,120653.000,2502.6009000,N,12132.0704000,E,2,60,0.51,28.6,M,15.2,M,73,3335*42\r\n'
        # self.wait_device()
        if not self.wait_device(timeout_s=5):
            print("warn: no GGA data before set base, continue anyway")
        time.sleep(2)
        #set base position
        setting = trans_ecef(base_gps[0],base_gps[1],base_gps[2])
        set_base = "PLSC,SETBASEXYZ," + str(setting[0]) + "," + str(setting[1]) + "," + str(setting[2])
        got_basexyz = False
        for _ in range(3):
            self._send_command(set_base)
            msg = self._wait_plsr("BASEXYZ", timeout_s=8)
            if msg is None:
                print("wait BASEXYZ timeout, retry...")
                continue
            if msg[1] == "ERR":
                print("set position error")
                if GNSSNTRIPClient is not None:
                    try:
                        base_gps = self.get_setting_gps()
                        setting = trans_ecef(base_gps[0],base_gps[1],base_gps[2])
                        set_base = "PLSC,SETBASEXYZ," + str(setting[0]) + "," + str(setting[1]) + "," + str(setting[2])
                    except Exception as e:
                        print("get_setting_gps failed:", e)
                continue
            if msg[1] == "BASEXYZ":
                print("set position correct")
                got_basexyz = True
                break
        if not got_basexyz:
            raise RuntimeError("set BASEXYZ timeout: no $PLSR,BASEXYZ response")

        time.sleep(2)
        #change to base mode
        print("set frequency")
        got_fixrate = False
        for _ in range(5):
            self._send_command("PLSC,FIXRATE,1")
            time.sleep(0.5)
            self._send_command("PLSC,FIXRATE,?")
            msg = self._wait_plsr("FIXRATE", timeout_s=5)
            if msg is None:
                print("wait FIXRATE timeout, retry...")
                continue
            if len(msg) > 2 and msg[2].split("*")[0] == "1":
                print("set frequency correct")
                got_fixrate = True
                break
            print("set frequency error")
        if not got_fixrate:
            raise RuntimeError("set FIXRATE timeout: no valid $PLSR,FIXRATE,1 response")

        time.sleep(2)
        self._send_command("PLSC,MCBASE,1")
        print("switch to base mode sent")
            
    def set_server(self):
        """Release the configuration handle before streaming RTCM."""
        self.ser.close()
        child = None
        try:
            if self.output_mode in ('tcp', 'both'):
                port = os.path.realpath(self.port)
                if not port.startswith('/dev/'):
                    raise ValueError('GNSS serial path must resolve inside /dev/')
                command = [self.str2str, '-in', f'serial://{port[5:]}:115200:8:n:1',
                           '-out', f'tcpsvr://{self.server_ip}:{self.tcp_port}']
                print('run:', ' '.join(command), flush=True)
                child = subprocess.Popen(command)
                if self.output_mode == 'tcp':
                    if child.wait() != 0:
                        raise RuntimeError('str2str failed')
                    return
                host = '127.0.0.1' if self.server_ip == '0.0.0.0' else self.server_ip
                deadline = time.monotonic() + 8
                while True:
                    if child.poll() is not None:
                        raise RuntimeError('str2str exited during startup')
                    try:
                        with socket.create_connection((host, self.tcp_port), timeout=0.2):
                            break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise RuntimeError('str2str TCP startup timeout')
                        time.sleep(0.1)
            send_rtcm(argparse.Namespace(
                gnss_port=self.port, gnss_baud=115200,
                tcp_host=host if self.output_mode == 'both' else None,
                tcp_port=self.tcp_port, lora_port=self.lora_port,
                lora_baud=self.lora_baud, dest=self.dest,
                payload_size=self.payload_size, packet_gap=self.packet_gap,
                ok_timeout=self.ok_timeout))
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()

    def get_setting_gps(self):
        if GNSSNTRIPClient is None:
            raise RuntimeError("pygnssutils is not installed on STM")
        rtcm_queue = Queue()
        settings = {
        "server": "210.61.148.245",  # NTRIP server
        "port": 2101,                   # NTRIP port
        "mountpoint": "LO-221432",
        "ntripuser": "TaipeiTech",  
        "ntrippassword": "1234",
        "output":rtcm_queue,
        }
        count = 0
        with GNSSNTRIPClient() as client:
            if client.run(**settings):
                while True:
                    time.sleep(0.01)  
                    if not rtcm_queue.empty():
                        raw_data, parsed_data = rtcm_queue.get()
                        # print(raw_data)
                        self.ser.write(raw_data)
                        raw_msg = str(self.ser.read_until())
                        print(raw_msg)
                        msg = raw_msg.split(",")
                        if msg[0] == "b'$GNGGA" and count == 300:
                            base_gps = [msg[2],msg[4],float(msg[9]) + float(msg[11])]
                            print("using nonRTK gps")
                            client.stop()
                            time.sleep(1)
                            self.log_base_position(base_gps, msg[6])
                            return base_gps
                        if msg[0] == "b'$GNGGA" and msg[6] == "4":
                            base_gps = [msg[2],msg[4],float(msg[9]) + float(msg[11])]
                            print("using RTK gps")
                            client.stop()
                            time.sleep(1)
                            self.log_base_position(base_gps, msg[6])
                            return base_gps
                        if msg[0] == "b'$GNGGA" and msg[6] == "2" and count >= 200:
                        # if msg[0] == "b'$GNGGA" and msg[6] == "2":
                            base_gps = [msg[2],msg[4],float(msg[9]) + float(msg[11])]
                            print("using dgps")
                            client.stop()
                            time.sleep(1)
                            self.log_base_position(base_gps, msg[6])
                            return base_gps
                        if msg[0] == "b'$GNGGA" and not(msg[2] == '' or msg[4] == '' or msg[9] == '' or msg[11] == ''):
                            count += 1
                        # 測試-------暫時強制給一個座標，讓它在室內也能啟動 Base 模式
                        # base_gps = [2502.5566136, 12132.1432212, 30.28]
                        # return base_gps
                        

    def log_base_position(self, pos, quality):
        """
        將座標、等級與時間戳記寫入紀錄檔 (不覆蓋)
        """
        log_file = "base_history_log.json"
        
        # 建立一筆新的資料
        new_entry = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"), # 格式化時間：2026-05-12 14:30:05
            "lat": pos[0],
            "lon": pos[1],
            "alt": pos[2],
            "quality": quality
        }
        
        # 使用 'a' 模式 (append) 打開檔案，這樣就不會覆蓋舊資料
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(new_entry) + "\n") # 每筆紀錄存成一行
            print(f"系統訊息：已將新座標紀錄至 {log_file}")
        except Exception as e:
            print(f"無法儲存紀錄: {e}")
