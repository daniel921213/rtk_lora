# Black Pill RTCM → RYLR998 sender

Open `blackpill_rtcm_rylr_sender.ino` in Arduino IDE. Keep `build_opt.h` beside it;
the sketch needs the larger interrupt-driven UART receive buffer defined there.

## Wiring

| Source | Destination |
| --- | --- |
| STM32 Linux USART TX | Black Pill PA10 (USART1 RX) |
| Black Pill PA9 (USART1 TX) | STM32 Linux USART RX (optional) |
| Black Pill PA2 (USART2 TX) | RYLR998 RXD |
| RYLR998 TXD | Black Pill PA3 (USART2 RX) |
| All GND pins | Common ground |

## See what the Black Pill receives and decodes

The sketch sends text diagnostics on **PA9 (USART1 TX), 115200 baud, 8N1**.
Connect PA9 to a USB-TTL adapter's **RXD** and connect their **GND** pins.
Plug that adapter into the PC and open its COM port in a serial monitor at
115200 baud. Leave the adapter's TXD and VCC disconnected: Linux TX already
drives PA10 and the board has its own power. This debug COM port is separate
from the PC receiver LoRa COM port. Alternatively, PA9 may go to the Linux
board's UART RX if its forwarding process is set up to read replies.

Re-upload this updated sketch first. If reusing the USB-TTL adapter used for
serial flashing, remove **only its TXD wire from PA10** after upload, reconnect
Linux TX to PA10, and leave the adapter RXD on PA9 and GND connected. Release
BOOT0 and press NRST to run the application. On Windows, with `pyserial`
installed, you can watch the debug COM port using:

```powershell
python -m serial.tools.miniterm COM6 115200
```

Replace COM6 with the adapter's actual port; the receiver LoRa can remain on
its separate COM5. Use Ctrl+] to exit miniterm.

After opening the monitor, press NRST on the Black Pill to see startup lines.
The firmware reports a `BP stat` line each second:

| Field | Meaning |
| --- | --- |
| `rxB` | Bytes received from Linux in the last second; zero means no input |
| `raw` | First 12 input bytes in hex in that second; NMEA often begins `24` (`$`), RTCM3 begins `D3` |
| `ok`, `bad`, `noise` | CRC-valid RTCM frames, bad CRC frames, and discarded input bytes in that second |
| `part`, `ov` | Partial RTCM timeouts in that second and total parse-buffer overflows |
| `q`, `drop`, `sent` | Pending frames, total old frames dropped, and total chunks accepted by the local radio |
| `radio`, `fault` | Current AT state and last fault reason |
| `skipped` | Total debug lines skipped because the debug line queue was full |

Use the counters to locate the break in the chain: `rxB=0` means no bytes
arrived at PA10; `rxB>0` with `ok=0` means no CRC-valid RTCM was decoded;
`ok>0` with `radio=FAULT` points to the local RYLR AT link; rising `sent`
means the local RYLR accepted chunks, so an empty receiver monitor then calls
for checking receiver address and matching LoRa settings.

`BP frame` lines show the decoded RTCM type, frame length, CRC result, and the
first 12 bytes in hex. At most four frame lines are emitted per second to keep
the logging bounded. `BP radio` and `BP fault` lines show AT setup, responses,
and the specific reason for a fast-blinking C13 LED. Debug lines are queued
and sent in short nonblocking chunks; if the queue fills, lines are dropped
instead of blocking RTCM processing. The local
radio's `+OK` confirms only that it accepted a chunk, not that the remote
receiver got it.

The numbers printed beside pins in the board diagram are header positions,
not Arduino digital pin numbers. Keep the sketch's `PA_10`, `PA_9`, `PA_3`,
`PA_2`, and `PC_13` pin names. For Generic F401RCTx, digital pin 31 is PC0;
PA10 is digital pin 10.

Use 3.3 V logic and a 3.3 V supply for the RYLR998. The LoRa supply must
handle its transmit current; do not feed the bare module from 5 V.

In Arduino IDE, use **STM32 MCU based boards → Generic STM32F4 series →
Generic F401RCTx** if the chip marking is `STM32F401RCT6`. Pick the upload
method supported by the board and your programmer (for example SWD/ST-Link).
The sketch uses 115200 baud, 8N1 on both UARTs. It assumes the PC LoRa is
address 67 and sets the local module to address 69 only if needed. It does not
change BAND, NETWORKID, PARAMETER, IPR, MODE, or CPIN; those must match the
other module. When the RYLR998 accepts a packet, the PC13 LED pulses briefly.
A fast continuous blink indicates an AT error or timeout; reset after fixing
the wiring or radio settings.

The firmware extracts CRC-valid RTCM3 messages from the Linux UART stream,
ignoring other bytes such as NMEA. It sends each whole message as consecutive
raw binary fragments of at most 240 bytes with `AT+SEND=67,...`, waiting for
the local `+OK` before the next fragment. There is no radio-level ACK or
retransmission. If the radio cannot keep up, older queued RTCM messages are
dropped to avoid transmitting stale corrections. Reduce the base RTCM output
rate if that happens.

The Linux program `rtk_base/stm_forward.py` can supply raw RTCM over USART3.
The PC radio should report `+RCV=69,<length>,<raw bytes>,<RSSI>,<SNR>`.
Use a binary-aware receiver for RTCM: the raw bytes can include CR/LF and are
not readable as ordinary text.

On Windows, find the USB-TTL COM port in Device Manager, then from the project
root run `python lora/rylr/monitor_pc.py --port COM5` (replace `COM5` with the
actual port). The monitor prints each packet's first 32 bytes in hex and each
complete RTCM3 frame that passes CRC-24Q. Close other serial monitors before
running it, because only one program can read the COM port at a time.
