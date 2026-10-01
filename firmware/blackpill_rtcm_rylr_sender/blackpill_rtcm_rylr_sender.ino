#include <Arduino.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

// STM32F401RCT6, STM32 Arduino core 3.0.0.
// Linux TX -> PA10 (USART1 RX). PA9 (USART1 TX) emits 115200 8N1 debug logs.
// For PC debug: PA9 -> USB-TTL RXD, common GND. Do not connect USB-TTL TXD
// to PA10 while the Linux TX is connected there.
// LoRa TXD -> PA3 (USART2 RX); PA2 (USART2 TX) -> LoRa RXD.
// All three boards must share GND. The RYLR998 itself needs 3.3 V power.
// Open this sketch with build_opt.h in the SAME folder: the default 64-byte
// UART RX buffer is too small while an AT+SEND command is written.
// Use MCU PinName values below. Numbers on the board diagram are header
// positions, not Arduino digital pin numbers.

#if !defined(STM32F401xC)
#error "Select Generic STM32F4 series / Generic F401RCTx for STM32F401RCT6"
#endif

Uart LinuxPort(PA_10, PA_9);  // RX, TX
Uart LoraPort(PA_3, PA_2);    // RX, TX

static constexpr uint32_t UART_BAUD = 115200;
static constexpr uint16_t LOCAL_ADDRESS = 69;
static constexpr uint16_t DEST_ADDRESS = 67;
static constexpr uint16_t MAX_RTCM_FRAME = 1029; // 3 header + 1023 data + 3 CRC
static constexpr uint16_t MAX_RADIO_PAYLOAD = 240;
static constexpr uint8_t FRAME_QUEUE_CAPACITY = 8;
static constexpr uint32_t REPLY_TIMEOUT_MS = 3000;
static constexpr uint32_t BOOT_RETRY_MS = 1000;
static constexpr uint8_t MAX_BOOT_ATTEMPTS = 10;
static constexpr uint32_t PACKET_GAP_MS = 10;
static constexpr uint32_t RTCM_PARTIAL_TIMEOUT_MS = 3000;
static constexpr uint32_t DEBUG_REPORT_MS = 1000;
static constexpr uint8_t DEBUG_SAMPLE_BYTES = 12;
static constexpr uint8_t DEBUG_FRAME_LINES_PER_REPORT = 4;
static constexpr uint8_t DEBUG_QUEUE_CAPACITY = 8;
// GPIO functions take Arduino digital pin numbers, so convert PC_13 here.
static const pin_size_t LED_PIN = pinNametoDigitalPin(PC_13); // Active low.

struct RtcmFrame {
  uint16_t length;
  uint8_t bytes[MAX_RTCM_FRAME];
};

static RtcmFrame frameQueue[FRAME_QUEUE_CAPACITY];
static uint8_t queueHead = 0;
static uint8_t queueCount = 0;
static RtcmFrame activeFrame;
static bool activeFrameValid = false;
static uint16_t activeOffset = 0;
static uint16_t inFlightLength = 0;

static uint8_t parseBuffer[MAX_RTCM_FRAME];
static uint16_t parseLength = 0;
static uint32_t lastRtcmByteMs = 0;
static uint32_t validFrames = 0;
static uint32_t badCrcFrames = 0;
static uint32_t droppedFrames = 0;
static uint32_t packetsAccepted = 0;
static uint32_t linuxBytes = 0;
static uint32_t discardedInputBytes = 0;
static uint32_t partialResets = 0;
static uint32_t parseOverflows = 0;
static uint32_t debugSkippedLines = 0;
struct DebugLine {
  uint16_t length;
  char bytes[254];
};
static DebugLine debugQueue[DEBUG_QUEUE_CAPACITY];
static uint8_t debugHead = 0;
static uint8_t debugCount = 0;
static uint16_t debugOffset = 0;
static uint8_t inputSample[DEBUG_SAMPLE_BYTES];
static uint8_t inputSampleLength = 0;
static uint8_t frameLinesThisReport = 0;
static uint32_t lastDebugReportMs = 0;
static uint32_t lastReportedLinuxBytes = 0;
static uint32_t lastReportedValidFrames = 0;
static uint32_t lastReportedBadCrcFrames = 0;
static uint32_t lastReportedDiscardedBytes = 0;
static uint32_t lastReportedPartialResets = 0;

enum RadioState : uint8_t {
  WAIT_AT,
  WAIT_ADDRESS,
  WAIT_SET_ADDRESS,
  RADIO_READY,
  WAIT_SEND,
  RADIO_FAULT
};

static RadioState radioState = WAIT_AT;
static uint32_t commandStartedMs = 0;
static uint32_t nextPacketAtMs = 0;
static uint8_t bootAttempts = 0;
static char radioLine[48];
static uint8_t radioLineLength = 0;
static bool radioLineOverflow = false;
static uint32_t ledPulseUntilMs = 0;
static const char *radioFaultReason = "-";

static const char *radioStateName(RadioState state) {
  switch (state) {
    case WAIT_AT: return "WAIT_AT";
    case WAIT_ADDRESS: return "WAIT_ADDRESS";
    case WAIT_SET_ADDRESS: return "WAIT_SET_ADDRESS";
    case RADIO_READY: return "READY";
    case WAIT_SEND: return "WAIT_SEND";
    case RADIO_FAULT: return "FAULT";
  }
  return "UNKNOWN";
}

// Queue complete lines; pumpDebug sends them in chunks that fit even the
// STM32 core's default 64-byte TX buffer. Never wait for debug UART space.
static void debugf(const char *format, ...) {
  if (debugCount == DEBUG_QUEUE_CAPACITY) {
    ++debugSkippedLines;
    return;
  }
  DebugLine &line = debugQueue[(debugHead + debugCount) % DEBUG_QUEUE_CAPACITY];
  va_list args;
  va_start(args, format);
  const int length = vsnprintf(line.bytes, sizeof(line.bytes) - 2, format, args);
  va_end(args);
  if (length < 0 || length >= static_cast<int>(sizeof(line.bytes) - 2)) {
    ++debugSkippedLines;
    return;
  }
  line.bytes[length] = '\r';
  line.bytes[length + 1] = '\n';
  line.length = static_cast<uint16_t>(length + 2);
  ++debugCount;
}

static void pumpDebug() {
  if (!debugCount) {
    return;
  }
  const int freeBytes = LinuxPort.availableForWrite();
  if (freeBytes <= 0) {
    return;
  }
  DebugLine &line = debugQueue[debugHead];
  const size_t remaining = line.length - debugOffset;
  const size_t chunk = remaining < static_cast<size_t>(freeBytes)
                           ? remaining : static_cast<size_t>(freeBytes);
  debugOffset += LinuxPort.write(
      reinterpret_cast<const uint8_t *>(line.bytes + debugOffset), chunk);
  if (debugOffset == line.length) {
    debugHead = (debugHead + 1) % DEBUG_QUEUE_CAPACITY;
    --debugCount;
    debugOffset = 0;
  }
}

static void logFrame(const uint8_t *bytes, uint16_t length, bool valid) {
  if (frameLinesThisReport >= DEBUG_FRAME_LINES_PER_REPORT) {
    return;
  }
  ++frameLinesThisReport;
  char hex[DEBUG_SAMPLE_BYTES * 3];
  size_t used = 0;
  const uint8_t count = length < DEBUG_SAMPLE_BYTES ? length : DEBUG_SAMPLE_BYTES;
  for (uint8_t i = 0; i < count; ++i) {
    used += snprintf(hex + used, sizeof(hex) - used, "%s%02X",
                     i ? " " : "", bytes[i]);
  }
  hex[used] = '\0';
  const unsigned type = length >= 8 ?
      (static_cast<unsigned>(bytes[3]) << 4) | (bytes[4] >> 4) : 0;
  debugf("BP frame CRC=%s type=%u bytes=%u head=%s",
         valid ? "OK" : "BAD", type, static_cast<unsigned>(length), hex);
}

static void faultRadio(const char *reason) {
  const RadioState previous = radioState;
  radioState = RADIO_FAULT;
  radioFaultReason = reason;
  debugf("BP fault reason=%s previous=%s", reason, radioStateName(previous));
}

static void reportDiagnostics(uint32_t now) {
  if ((uint32_t)(now - lastDebugReportMs) < DEBUG_REPORT_MS) {
    return;
  }
  char hex[DEBUG_SAMPLE_BYTES * 3];
  size_t used = 0;
  for (uint8_t i = 0; i < inputSampleLength; ++i) {
    used += snprintf(hex + used, sizeof(hex) - used, "%s%02X",
                     i ? " " : "", inputSample[i]);
  }
  hex[used] = '\0';
  debugf("BP stat rxB=%lu ok=%lu bad=%lu noise=%lu part=%lu ov=%lu q=%u drop=%lu sent=%lu radio=%s fault=%s raw=%s skipped=%lu",
         static_cast<unsigned long>(linuxBytes - lastReportedLinuxBytes),
         static_cast<unsigned long>(validFrames - lastReportedValidFrames),
         static_cast<unsigned long>(badCrcFrames - lastReportedBadCrcFrames),
         static_cast<unsigned long>(discardedInputBytes - lastReportedDiscardedBytes),
         static_cast<unsigned long>(partialResets - lastReportedPartialResets),
         static_cast<unsigned long>(parseOverflows),
         static_cast<unsigned>(queueCount),
         static_cast<unsigned long>(droppedFrames),
         static_cast<unsigned long>(packetsAccepted),
         radioStateName(radioState), radioFaultReason,
         inputSampleLength ? hex : "-",
         static_cast<unsigned long>(debugSkippedLines));
  lastReportedLinuxBytes = linuxBytes;
  lastReportedValidFrames = validFrames;
  lastReportedBadCrcFrames = badCrcFrames;
  lastReportedDiscardedBytes = discardedInputBytes;
  lastReportedPartialResets = partialResets;
  inputSampleLength = 0;
  frameLinesThisReport = 0;
  lastDebugReportMs = now;
}

static uint32_t crc24q(const uint8_t *data, uint16_t length) {
  uint32_t crc = 0;
  for (uint16_t i = 0; i < length; ++i) {
    crc ^= static_cast<uint32_t>(data[i]) << 16;
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc <<= 1;
      if (crc & 0x1000000UL) {
        crc ^= 0x1864CFBUL;
      }
    }
  }
  return crc & 0xFFFFFFUL;
}

static void enqueueFrame(const uint8_t *bytes, uint16_t length) {
  if (queueCount == FRAME_QUEUE_CAPACITY) {
    // An old correction is less useful than a new one when the radio is slow.
    queueHead = (queueHead + 1) % FRAME_QUEUE_CAPACITY;
    --queueCount;
    ++droppedFrames;
  }
  const uint8_t tail = (queueHead + queueCount) % FRAME_QUEUE_CAPACITY;
  frameQueue[tail].length = length;
  memcpy(frameQueue[tail].bytes, bytes, length);
  ++queueCount;
}

static void discardUntilNextPreamble() {
  uint16_t next = 1;
  while (next < parseLength && parseBuffer[next] != 0xD3) {
    ++next;
  }
  if (next < parseLength) {
    discardedInputBytes += next;
    parseLength -= next;
    memmove(parseBuffer, parseBuffer + next, parseLength);
  } else {
    discardedInputBytes += parseLength;
    parseLength = 0;
  }
}

static void parseAvailableRtcm() {
  while (parseLength) {
    if (parseBuffer[0] != 0xD3) {
      discardUntilNextPreamble();
      continue;
    }
    if (parseLength < 3) {
      return;
    }
    if ((parseBuffer[1] & 0xFC) != 0) {
      discardUntilNextPreamble();
      continue;
    }
    const uint16_t payloadLength =
        (static_cast<uint16_t>(parseBuffer[1] & 0x03) << 8) | parseBuffer[2];
    const uint16_t frameLength = payloadLength + 6;
    if (parseLength < frameLength) {
      return;
    }
    const uint32_t receivedCrc =
        (static_cast<uint32_t>(parseBuffer[frameLength - 3]) << 16) |
        (static_cast<uint32_t>(parseBuffer[frameLength - 2]) << 8) |
        parseBuffer[frameLength - 1];
    if (crc24q(parseBuffer, frameLength - 3) == receivedCrc) {
      logFrame(parseBuffer, frameLength, true);
      enqueueFrame(parseBuffer, frameLength);
      ++validFrames;
      parseLength -= frameLength;
      if (parseLength) {
        memmove(parseBuffer, parseBuffer + frameLength, parseLength);
      }
    } else {
      logFrame(parseBuffer, frameLength, false);
      ++badCrcFrames;
      discardUntilNextPreamble();
    }
  }
}

static void pollLinux() {
  // Bounded work keeps LoRa replies responsive even during a busy RTCM stream.
  for (uint16_t i = 0; i < 512 && LinuxPort.available() > 0; ++i) {
    const int value = LinuxPort.read();
    if (value < 0) {
      break;
    }
    const uint32_t now = millis();
    ++linuxBytes;
    if (inputSampleLength < DEBUG_SAMPLE_BYTES) {
      inputSample[inputSampleLength++] = static_cast<uint8_t>(value);
    }
    if (parseLength && (uint32_t)(now - lastRtcmByteMs) > RTCM_PARTIAL_TIMEOUT_MS) {
      parseLength = 0;
      ++partialResets;
    }
    lastRtcmByteMs = now;
    if (parseLength < MAX_RTCM_FRAME) {
      parseBuffer[parseLength++] = static_cast<uint8_t>(value);
      parseAvailableRtcm();
    } else {
      parseLength = 0;
      ++parseOverflows;
    }
  }
}

static bool writeRadio(const uint8_t *bytes, size_t length) {
  size_t written = 0;
  while (written < length) {
    const size_t count = LoraPort.write(bytes + written, length - written);
    if (count == 0) {
      faultRadio("radio UART write failed");
      return false;
    }
    written += count;
  }
  return true;
}

static void sendSimpleCommand(const char *command, RadioState nextState) {
  if (!writeRadio(reinterpret_cast<const uint8_t *>(command), strlen(command)) ||
      !writeRadio(reinterpret_cast<const uint8_t *>("\r\n"), 2)) {
    return;
  }
  LoraPort.flush();
  radioState = nextState;
  commandStartedMs = millis();
  debugf("BP radio tx=%s state=%s", command, radioStateName(nextState));
}

static void sendAT() {
  ++bootAttempts;
  sendSimpleCommand("AT", WAIT_AT);
}

static void handleRadioLine(const char *line) {
  if (strncmp(line, "+OK", 3) == 0 || strncmp(line, "+ADDRESS=", 9) == 0 ||
      strncmp(line, "+ERR", 4) == 0) {
    debugf("BP radio rx=%s state=%s", line, radioStateName(radioState));
  }
  if (strncmp(line, "+ERR", 4) == 0) {
    faultRadio("module +ERR"); // Do not overlap a rejected or uncertain command.
    return;
  }
  if (radioState == WAIT_AT && strcmp(line, "+OK") == 0) {
    sendSimpleCommand("AT+ADDRESS?", WAIT_ADDRESS);
    return;
  }
  if (radioState == WAIT_ADDRESS && strncmp(line, "+ADDRESS=", 9) == 0) {
    char *end = nullptr;
    const unsigned long address = strtoul(line + 9, &end, 10);
    if (end == line + 9 || *end != '\0' || address > 65535UL) {
      faultRadio("invalid address reply");
    } else if (address != LOCAL_ADDRESS) {
      sendSimpleCommand("AT+ADDRESS=69", WAIT_SET_ADDRESS);
    } else {
      radioState = RADIO_READY;
    }
    return;
  }
  if (radioState == WAIT_SET_ADDRESS && strcmp(line, "+OK") == 0) {
    radioState = RADIO_READY;
    return;
  }
  if (radioState == WAIT_SEND && strcmp(line, "+OK") == 0) {
    activeOffset += inFlightLength;
    inFlightLength = 0;
    ++packetsAccepted;
    ledPulseUntilMs = millis() + 35;
    if (activeOffset >= activeFrame.length) {
      activeFrameValid = false;
    }
    nextPacketAtMs = millis() + PACKET_GAP_MS;
    radioState = RADIO_READY;
  }
}

static void pollRadio() {
  for (uint16_t i = 0; i < 128 && LoraPort.available() > 0; ++i) {
    const int value = LoraPort.read();
    if (value < 0) {
      break;
    }
    if (value == '\n') {
      if (!radioLineOverflow) {
        radioLine[radioLineLength] = '\0';
        handleRadioLine(radioLine);
      }
      radioLineLength = 0;
      radioLineOverflow = false;
    } else if (value != '\r' && !radioLineOverflow) {
      if (static_cast<size_t>(radioLineLength + 1) < sizeof(radioLine)) {
        radioLine[radioLineLength++] = static_cast<char>(value);
      } else {
        radioLineOverflow = true;
      }
    }
  }
}

static void sendNextChunk() {
  if (!activeFrameValid) {
    if (queueCount == 0) {
      return;
    }
    activeFrame.length = frameQueue[queueHead].length;
    memcpy(activeFrame.bytes, frameQueue[queueHead].bytes, activeFrame.length);
    queueHead = (queueHead + 1) % FRAME_QUEUE_CAPACITY;
    --queueCount;
    activeOffset = 0;
    activeFrameValid = true;
  }

  const uint16_t remaining = activeFrame.length - activeOffset;
  inFlightLength = remaining > MAX_RADIO_PAYLOAD ? MAX_RADIO_PAYLOAD : remaining;
  char header[24];
  const int headerLength = snprintf(header, sizeof(header), "AT+SEND=%u,%u,",
                                    static_cast<unsigned>(DEST_ADDRESS),
                                    static_cast<unsigned>(inFlightLength));
  if (headerLength <= 0 || headerLength >= static_cast<int>(sizeof(header))) {
    faultRadio("send header format failed");
    return;
  }
  if (!writeRadio(reinterpret_cast<const uint8_t *>(header), headerLength) ||
      !writeRadio(activeFrame.bytes + activeOffset, inFlightLength) ||
      !writeRadio(reinterpret_cast<const uint8_t *>("\r\n"), 2)) {
    return;
  }
  LoraPort.flush();
  radioState = WAIT_SEND;
  commandStartedMs = millis();
  debugf("BP send to=%u bytes=%u offset=%u", static_cast<unsigned>(DEST_ADDRESS),
         static_cast<unsigned>(inFlightLength), static_cast<unsigned>(activeOffset));
}

static void updateLed(uint32_t now) {
  if (radioState == RADIO_FAULT) {
    digitalWrite(LED_PIN, ((now / 100) & 1) ? HIGH : LOW);
  } else {
    digitalWrite(LED_PIN, (int32_t)(ledPulseUntilMs - now) > 0 ? LOW : HIGH);
  }
}

void setup() {
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);
  LinuxPort.begin(UART_BAUD, SERIAL_8N1);
  LoraPort.begin(UART_BAUD, SERIAL_8N1);
  lastDebugReportMs = millis();
  debugf("BP boot Linux=PA10/PA9 LoRa=PA3/PA2 baud=%lu",
         static_cast<unsigned long>(UART_BAUD));
  while (LoraPort.available() > 0) {
    LoraPort.read();
  }
  sendAT();
}

void loop() {
  pollLinux();
  pollRadio();

  const uint32_t now = millis();
  if (parseLength && (uint32_t)(now - lastRtcmByteMs) > RTCM_PARTIAL_TIMEOUT_MS) {
    parseLength = 0;
    ++partialResets;
  }
  if (radioState == WAIT_AT && (uint32_t)(now - commandStartedMs) > BOOT_RETRY_MS) {
    if (bootAttempts < MAX_BOOT_ATTEMPTS) {
      sendAT();
    } else {
      faultRadio("AT boot retries exhausted");
    }
  } else if ((radioState == WAIT_ADDRESS || radioState == WAIT_SET_ADDRESS ||
              radioState == WAIT_SEND) &&
             (uint32_t)(now - commandStartedMs) > REPLY_TIMEOUT_MS) {
    faultRadio(radioState == WAIT_SEND ? "AT+SEND reply timeout" :
               "radio setup reply timeout");
  }

  if (radioState == RADIO_READY && (int32_t)(now - nextPacketAtMs) >= 0) {
    sendNextChunk();
  }
  updateLed(now);
  reportDiagnostics(now);
  pumpDebug();
}
