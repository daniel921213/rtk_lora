"""RTCM-over-LoRa sender with fragmentation and ACK-on-Error retransmission."""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from ack import all_fragments_received, missing_indices_from_bitmap
from cobs_codec import COBSDecodeError, cobs_decode, cobs_encode
from protocol import (
    AckStatus,
    FLAG_ACK_REQ,
    FLAG_END,
    FLAG_START,
    PROTOCOL_VERSION,
    AckFrame,
    Fragment,
    decode_ack,
    encode_fragment,
    is_ack_wire_frame,
)
from rtcm import RTCMStreamParser, extract_rtcm_message_type
from serial_io import DelimitedFrameReader, ByteStreamEndpoint, iter_file_chunks, open_serial_endpoint


@dataclass(slots=True)
class SenderMetrics:
    """Sender-side counters for reliability analysis."""

    total_rtcm_frames_in: int = 0
    total_rtcm_frames_out: int = 0
    total_fragments_tx: int = 0
    total_fragments_retx: int = 0
    fragment_loss_detected: int = 0
    give_up_count: int = 0
    ack_received_count: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(slots=True)
class SenderConfig:
    """Runtime configuration for sender transport behavior."""

    frag_payload: int = 32
    window_size: int = 4
    ack_timeout: float = 0.5
    max_retries: int = 5


class Sender:
    """Fragment RTCM frames, transmit with COBS framing, and handle ACK bitmap retransmit."""

    def __init__(
        self,
        tx_endpoint: ByteStreamEndpoint,
        ack_endpoint: ByteStreamEndpoint,
        config: SenderConfig | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.tx_endpoint = tx_endpoint
        self.ack_endpoint = ack_endpoint
        self.config = config or SenderConfig()
        self.logger = logger or logging.getLogger("rtcm_sender")

        self.metrics = SenderMetrics()
        self._ack_reader = DelimitedFrameReader(ack_endpoint)
        self._next_msg_id = 0

        # Debug traces used in tests.
        self.tx_events: list[dict[str, object]] = []
        self.ack_events: list[dict[str, object]] = []

    def _log(self, event: str, **fields: object) -> None:
        payload = {"ts": time.time(), "event": event, **fields}
        self.logger.info(json.dumps(payload, ensure_ascii=False))

    def _allocate_msg_id(self) -> int:
        msg_id = self._next_msg_id
        self._next_msg_id = (self._next_msg_id + 1) & 0xFFFF
        return msg_id

    def _fragment_frame(self, frame: bytes, msg_id: int) -> list[Fragment]:
        frag_payload = self.config.frag_payload
        if frag_payload <= 0 or frag_payload > 255:
            raise ValueError("frag_payload must be in 1..255")

        chunks = [frame[i : i + frag_payload] for i in range(0, len(frame), frag_payload)]
        frag_cnt = len(chunks)
        if frag_cnt == 0:
            chunks = [b""]
            frag_cnt = 1
        if frag_cnt > 255:
            raise ValueError("fragment count exceeds protocol limit 255")

        fragments: list[Fragment] = []
        for idx, payload in enumerate(chunks):
            flags = 0
            if idx == 0:
                flags |= FLAG_START
            if idx == frag_cnt - 1:
                flags |= FLAG_END
            if ((idx + 1) % self.config.window_size == 0) or (idx == frag_cnt - 1):
                flags |= FLAG_ACK_REQ

            fragments.append(
                Fragment(
                    ver=PROTOCOL_VERSION,
                    msg_id=msg_id,
                    flags=flags,
                    frag_idx=idx,
                    frag_cnt=frag_cnt,
                    payload=payload,
                )
            )
        return fragments

    def _send_fragment(self, fragment: Fragment, retransmit: bool, retry_count: int) -> None:
        raw = encode_fragment(fragment)
        framed = cobs_encode(raw) + b"\x00"
        self.tx_endpoint.write(framed)

        if retransmit:
            self.metrics.total_fragments_retx += 1
            tx_kind = "retransmit"
        else:
            tx_kind = "tx"
        self.metrics.total_fragments_tx += 1

        self.tx_events.append(
            {
                "msg_id": fragment.msg_id,
                "frag_idx": fragment.frag_idx,
                "frag_cnt": fragment.frag_cnt,
                "retransmit": retransmit,
            }
        )

        self._log(
            "fragment_sent",
            msg_id=fragment.msg_id,
            frag_idx=fragment.frag_idx,
            frag_cnt=fragment.frag_cnt,
            action=tx_kind,
            retry_count=retry_count,
        )

    def _wait_for_ack(self, msg_id: int, timeout: float) -> AckFrame | None:
        deadline = time.monotonic() + timeout

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None

            frame = self._ack_reader.read_frame(timeout=remaining)
            if frame is None:
                return None
            if not frame:
                self._log("ack_drop", reason="empty_frame")
                continue

            try:
                raw = cobs_decode(frame)
            except COBSDecodeError as exc:
                self._log("ack_drop", reason="cobs_decode_failed", detail=str(exc))
                continue

            if not is_ack_wire_frame(raw):
                self._log("ack_drop", reason="not_ack_frame")
                continue

            try:
                ack = decode_ack(raw)
            except Exception as exc:  # protocol decode errors
                self._log("ack_drop", reason="ack_decode_failed", detail=str(exc))
                continue

            if ack.msg_id != msg_id:
                self._log("ack_drop", reason="msg_id_mismatch", ack_msg_id=ack.msg_id, msg_id=msg_id)
                continue

            self.metrics.ack_received_count += 1
            missing_all = missing_indices_from_bitmap(ack.bitmap, ack.frag_cnt)
            self.ack_events.append(
                {
                    "msg_id": ack.msg_id,
                    "status": int(ack.status),
                    "missing": list(missing_all),
                }
            )
            self._log(
                "ack_received",
                msg_id=ack.msg_id,
                status=int(ack.status),
                missing_fragments=missing_all,
            )
            return ack

    def send_rtcm_frame(self, frame: bytes) -> bool:
        """Send one complete RTCM frame and return True on successful delivery."""
        msg_id = self._allocate_msg_id()
        rtcm_type = extract_rtcm_message_type(frame)
        fragments = self._fragment_frame(frame, msg_id)
        frag_cnt = len(fragments)

        self.metrics.total_rtcm_frames_in += 1
        self._log(
            "rtcm_enqueue",
            msg_id=msg_id,
            rtcm_len=len(frame),
            rtcm_type=rtcm_type,
            frag_cnt=frag_cnt,
        )

        window_size = max(1, self.config.window_size)

        for window_start in range(0, frag_cnt, window_size):
            window_end = min(frag_cnt, window_start + window_size)
            window_indices = set(range(window_start, window_end))
            pending = set(window_indices)
            retry_count = 0

            while pending:
                retransmit = retry_count > 0
                for idx in sorted(pending):
                    self._send_fragment(fragments[idx], retransmit=retransmit, retry_count=retry_count)

                ack: AckFrame | None = None
                ack_deadline = time.monotonic() + self.config.ack_timeout
                while True:
                    remaining = ack_deadline - time.monotonic()
                    if remaining <= 0:
                        break

                    candidate = self._wait_for_ack(msg_id, timeout=remaining)
                    if candidate is None:
                        break
                    ack = candidate

                    if ack.frag_cnt != frag_cnt:
                        break
                    if ack.status in (AckStatus.INVALID, AckStatus.ABORT):
                        break
                    if ack.status == AckStatus.COMPLETE and all_fragments_received(ack.bitmap, ack.frag_cnt):
                        break

                    missing_now = set(
                        missing_indices_from_bitmap(
                            ack.bitmap,
                            ack.frag_cnt,
                            start_idx=window_start,
                            end_idx=window_end,
                        )
                    )
                    # Receiver may emit early partial ACKs while later fragments are still in flight.
                    # Keep waiting within the same ACK timeout to collect a more up-to-date bitmap.
                    if not missing_now:
                        break

                if ack is None:
                    retry_count += 1
                    self._log(
                        "ack_timeout",
                        msg_id=msg_id,
                        window_start=window_start,
                        window_end=window_end,
                        retry_count=retry_count,
                    )
                    if retry_count > self.config.max_retries:
                        self.metrics.give_up_count += 1
                        self._log(
                            "msg_give_up",
                            msg_id=msg_id,
                            reason="ack_timeout",
                            retry_count=retry_count,
                        )
                        return False
                    continue

                if ack.frag_cnt != frag_cnt:
                    retry_count += 1
                    self._log(
                        "ack_invalid",
                        msg_id=msg_id,
                        reason="frag_cnt_mismatch",
                        ack_frag_cnt=ack.frag_cnt,
                        expected_frag_cnt=frag_cnt,
                        retry_count=retry_count,
                    )
                    if retry_count > self.config.max_retries:
                        self.metrics.give_up_count += 1
                        self._log("msg_give_up", msg_id=msg_id, reason="ack_frag_cnt_mismatch")
                        return False
                    pending = set(window_indices)
                    continue

                if ack.status == AckStatus.COMPLETE and all_fragments_received(ack.bitmap, ack.frag_cnt):
                    self.metrics.total_rtcm_frames_out += 1
                    self._log("msg_success", msg_id=msg_id, reason="ack_complete")
                    return True

                if ack.status in (AckStatus.INVALID, AckStatus.ABORT):
                    retry_count += 1
                    pending = set(window_indices)
                    self._log(
                        "ack_error_status",
                        msg_id=msg_id,
                        status=int(ack.status),
                        retry_count=retry_count,
                    )
                    if retry_count > self.config.max_retries:
                        self.metrics.give_up_count += 1
                        self._log("msg_give_up", msg_id=msg_id, reason="ack_error_status")
                        return False
                    continue

                missing = set(
                    missing_indices_from_bitmap(
                        ack.bitmap,
                        ack.frag_cnt,
                        start_idx=window_start,
                        end_idx=window_end,
                    )
                )
                if missing:
                    self.metrics.fragment_loss_detected += len(missing)
                    pending = missing
                    retry_count += 1
                    self._log(
                        "window_missing",
                        msg_id=msg_id,
                        window_start=window_start,
                        window_end=window_end,
                        missing=sorted(missing),
                        retry_count=retry_count,
                    )
                    if retry_count > self.config.max_retries:
                        self.metrics.give_up_count += 1
                        self._log("msg_give_up", msg_id=msg_id, reason="max_retries_exceeded")
                        return False
                else:
                    pending.clear()

        self.metrics.total_rtcm_frames_out += 1
        self._log("msg_success", msg_id=msg_id, reason="all_windows_acked")
        return True

    def send_rtcm_frames(self, frames: Iterable[bytes]) -> SenderMetrics:
        """Send all frames from iterable source."""
        for frame in frames:
            self.send_rtcm_frame(frame)
        return self.metrics



def setup_logger(name: str, log_file: str | None) -> logging.Logger:
    """Create JSON-lines logger for sender/receiver tools."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    handler: logging.Handler
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_file, encoding="utf-8")
    else:
        handler = logging.StreamHandler()

    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def iter_rtcm_frames_from_file(path: str) -> Iterable[bytes]:
    """Parse RTCM3 frames from binary file."""
    parser = RTCMStreamParser()
    for chunk in iter_file_chunks(path, chunk_size=512):
        for frame in parser.feed(chunk):
            yield frame


def run_sender_cli(args: argparse.Namespace) -> int:
    """Run sender CLI command."""
    logger = setup_logger("rtcm_sender", args.log_file)

    tx = open_serial_endpoint(args.out_serial, args.out_baud, timeout=0.1)
    if args.ack_serial and (args.ack_serial != args.out_serial or args.ack_baud != args.out_baud):
        ack_ep = open_serial_endpoint(args.ack_serial, args.ack_baud, timeout=0.1)
    else:
        ack_ep = tx

    sender = Sender(
        tx_endpoint=tx,
        ack_endpoint=ack_ep,
        config=SenderConfig(
            frag_payload=args.frag_payload,
            window_size=args.window_size,
            ack_timeout=args.ack_timeout,
            max_retries=args.max_retries,
        ),
        logger=logger,
    )

    try:
        if args.in_file:
            sender.send_rtcm_frames(iter_rtcm_frames_from_file(args.in_file))
        else:
            if not args.in_serial:
                raise ValueError("either --in-file or --in-serial must be provided")
            source = open_serial_endpoint(args.in_serial, args.baud, timeout=0.1)
            parser = RTCMStreamParser()
            while True:
                chunk = source.read(512, timeout=0.1)
                if not chunk:
                    continue
                for frame in parser.feed(chunk):
                    sender.send_rtcm_frame(frame)
    except KeyboardInterrupt:
        pass
    finally:
        tx.close()
        if ack_ep is not tx:
            ack_ep.close()

    logger.info(json.dumps({"event": "sender_summary", **sender.metrics.to_dict()}))
    return 0



def build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI parser for sender tool."""
    parser = argparse.ArgumentParser(description="RTCM over LoRa sender")
    parser.add_argument("--in-serial", type=str, default=None, help="Input RTCM serial device path")
    parser.add_argument("--baud", type=int, default=9600, help="Input serial baud rate")
    parser.add_argument("--in-file", type=str, default=None, help="Input RTCM binary file")

    parser.add_argument("--out-serial", type=str, required=True, help="LoRa TX serial device path")
    parser.add_argument("--out-baud", type=int, default=115200, help="LoRa TX baud rate")
    parser.add_argument("--ack-serial", type=str, default=None, help="ACK RX serial device path")
    parser.add_argument("--ack-baud", type=int, default=115200, help="ACK RX baud rate")

    parser.add_argument("--frag-payload", type=int, default=32, choices=[24, 32, 40], help="Fragment payload bytes")
    parser.add_argument("--window-size", type=int, default=4, help="Sliding window size")
    parser.add_argument("--ack-timeout", type=float, default=0.5, help="ACK timeout (seconds)")
    parser.add_argument("--max-retries", type=int, default=5, help="Max retries per window")
    parser.add_argument("--log-file", type=str, default=None, help="Path to JSON lines log file")
    return parser


if __name__ == "__main__":
    raise SystemExit(run_sender_cli(build_arg_parser().parse_args()))
