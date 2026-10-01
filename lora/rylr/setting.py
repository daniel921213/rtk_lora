import serial
import time
import shlex

PORT = "/dev/lora"
BAUD = 115200

ser = serial.Serial(
    PORT,
    BAUD,
    bytesize=serial.EIGHTBITS,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    timeout=0.5
)


def send_at(cmd):
    ser.reset_input_buffer()

    ser.write((cmd + "\r\n").encode())
    ser.flush()

    time.sleep(0.2)

    response = ser.read_all()

    if response:
        print(response.decode(errors="replace").strip())
    else:
        print("No response")


def help_menu():
    print("""
================ RYLR998 Terminal ================

基本：
  at                  測試模組
  info                顯示所有主要設定
  help                顯示此說明
  exit                離開

Address：
  addr                 查詢 Address
  addr 1               設定 Address = 1

Network ID：
  net                  查詢 Network ID
  net 10               設定 Network ID = 10

頻率：
  band                 查詢頻率
  band 915000000       設定 915 MHz
  band 868000000       設定 868 MHz

LoRa 參數：
  param                查詢 LoRa 參數
  param 9 7 1 12       SF9 / BW125k / CR4/5 / Preamble12

發射功率：
  power                查詢發射功率
  power 15             設定 15 dBm

UART baud rate：
  baud                 查詢 UART baud rate
  baud 115200          設定 115200

加密：
  key                  查詢 CPIN
  key 12345678         設定 8 字元密碼

模式：
  mode                 查詢模式
  mode 0               一般模式
  mode 1               Sleep mode

傳送：
  send 2 HELLO
                      傳 HELLO 給 Address 2

直接 AT：
  raw AT+ADDRESS?
  raw AT+PARAMETER=9,7,1,12

===================================================
""")


def show_info():
    commands = [
        "AT+ADDRESS?",
        "AT+NETWORKID?",
        "AT+BAND?",
        "AT+PARAMETER?",
        "AT+CRFOP?",
        "AT+IPR?",
        "AT+MODE?",
        "AT+CPIN?"
    ]

    for cmd in commands:
        print(f"> {cmd}")
        send_at(cmd)


print(f"Opened {PORT} @ {BAUD}")
help_menu()


while True:
    try:
        line = input("RYLR998> ").strip()

        if not line:
            continue

        args = shlex.split(line)
        cmd = args[0].lower()

        # ------------------------------------------------
        # 系統
        # ------------------------------------------------

        if cmd in ["exit", "quit", "q"]:
            break

        elif cmd in ["help", "h", "?"]:
            help_menu()

        elif cmd == "at":
            send_at("AT")

        elif cmd == "info":
            show_info()

        # ------------------------------------------------
        # ADDRESS
        # ------------------------------------------------

        elif cmd == "addr":

            if len(args) == 1:
                send_at("AT+ADDRESS?")
            else:
                send_at(f"AT+ADDRESS={args[1]}")

        # ------------------------------------------------
        # NETWORK ID
        # ------------------------------------------------

        elif cmd == "net":

            if len(args) == 1:
                send_at("AT+NETWORKID?")
            else:
                send_at(f"AT+NETWORKID={args[1]}")

        # ------------------------------------------------
        # BAND
        # ------------------------------------------------

        elif cmd == "band":

            if len(args) == 1:
                send_at("AT+BAND?")
            else:
                send_at(f"AT+BAND={args[1]}")

        # ------------------------------------------------
        # PARAMETER
        # ------------------------------------------------

        elif cmd == "param":

            if len(args) == 1:
                send_at("AT+PARAMETER?")

            elif len(args) == 5:

                sf = args[1]
                bw = args[2]
                cr = args[3]
                preamble = args[4]

                send_at(
                    f"AT+PARAMETER={sf},{bw},{cr},{preamble}"
                )

            else:
                print("Usage:")
                print("  param")
                print("  param 9 7 1 12")

        # ------------------------------------------------
        # RF POWER
        # ------------------------------------------------

        elif cmd == "power":

            if len(args) == 1:
                send_at("AT+CRFOP?")
            else:
                send_at(f"AT+CRFOP={args[1]}")

        # ------------------------------------------------
        # UART BAUD RATE
        # ------------------------------------------------

        elif cmd == "baud":

            if len(args) == 1:
                send_at("AT+IPR?")

            else:

                new_baud = int(args[1])

                send_at(f"AT+IPR={new_baud}")

                print()
                print("注意：模組 Baud rate 已改變")
                print(f"請重新啟動此程式並設定 BAUD = {new_baud}")

        # ------------------------------------------------
        # CPIN
        # ------------------------------------------------

        elif cmd == "key":

            if len(args) == 1:
                send_at("AT+CPIN?")
            else:
                send_at(f"AT+CPIN={args[1]}")

        # ------------------------------------------------
        # MODE
        # ------------------------------------------------

        elif cmd == "mode":

            if len(args) == 1:
                send_at("AT+MODE?")
            else:
                send_at(f"AT+MODE={args[1]}")

        # ------------------------------------------------
        # SEND
        # ------------------------------------------------

        elif cmd == "send":

            if len(args) < 3:

                print("Usage:")
                print("  send <address> <message>")
                print("例如：")
                print("  send 2 HELLO")

            else:

                address = int(args[1])
                if not 0 <= address <= 65535:
                    raise ValueError("Address must be between 0 and 65535")
                message = " ".join(args[2:])

                length = len(message.encode())
                if length > 240:
                    raise ValueError("Message must be at most 240 bytes")

                send_at(
                    f"AT+SEND={address},{length},{message}"
                )

        # ------------------------------------------------
        # RAW AT COMMAND
        # ------------------------------------------------

        elif cmd == "raw":

            if len(args) < 2:
                print("Usage: raw AT+COMMAND")
            else:
                send_at(" ".join(args[1:]))

        else:

            print("未知指令")
            print("輸入 help 查看指令")
            
    except KeyboardInterrupt:
        print("\n離開")
        break

    except Exception as e:
        print(f"Error: {e}")


ser.close()
