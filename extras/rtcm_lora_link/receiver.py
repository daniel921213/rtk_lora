"""RTCM-over-LoRa receiver with COBS decoding, CRC checks, and reassembly."""

from __future__ import annotations

import argparse
import json
import logging
import random
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Protocol

from cobs_codec import COBSDecodeError, cobs_decode, cobs_encode
from protocol import (
    AckStatus,
    AckFrame,
    decode_fragment,
    encode_ack,
    is_ack_wire_frame,
)
from reassembly import ReassemblyManager
from sender import setup_logger
from serial_io import BinaryFileSink, ByteStreamEndpoint, DelimitedFrameReader, open_serial_endpoint


class BinarySink(Protocol):
    """Output sink for complete RTCM frames."""

    def write(self, data: bytes) -> int:
        ...


@dataclass(slots=True)
class ReceiverMetrics:
    """Receiver-side metrics."""

    total_fragments_rx: int = 0
    fragment_crc_fail: int = 0
    cobs_decode_fail: int = 0
    protocol_decode_fail: int = 0
    total_acks_tx: int = 0
    total_rtcm_frames_out: int = 0
    total_rtcm_frames_dropped_by_interval: int = 0
    output_fail: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(slots=True)
class ReceiverConfig:
    """Runtime config for receiver."""

    reassembly_timeout: float = 5.0
    poll_timeout: float = 0.1
    enable_fake_delay: bool = False
    fake_delay_min_s: float = 1.0
    fake_delay_max_s: float = 5.0
    output_interval_s: float = 0.0


@dataclass(slots=True)
class ScheduledOutput:
    """A completed RTCM frame scheduled for delayed output."""

    msg_id: int
    rtcm: bytes
    latency_s: float | None
    fake_delay_s: float
    ready_at: float


class MultiSink:
    """Fan-out sink writing to multiple outputs."""

    def __init__(self, sinks: list[BinarySink]) -> None:
        self.sinks = sinks

    def write(self, data: bytes) -> int:
        for sink in self.sinks:
            sink.write(data)
        return len(data)

    def close(self) -> None:
        for sink in self.sinks:
            close = getattr(sink, "close", None)
            if callable(close):
                close()


class Receiver:
    """Decode incoming COBS transport frames and reassemble RTCM messages."""

    def __init__(
        self,
        in_endpoint: ByteStreamEndpoint,
        ack_endpoint: ByteStreamEndpoint,
        out_sink: BinarySink,
        config: ReceiverConfig | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.in_endpoint = in_endpoint
        self.ack_endpoint = ack_endpoint
        self.out_sink = out_sink
        self.config = config or ReceiverConfig()
        self.logger = logger or logging.getLogger("rtcm_receiver")

        self.metrics = ReceiverMetrics()
        self._reader = DelimitedFrameReader(in_endpoint)
        self._reassembly = ReassemblyManager(timeout_s=self.config.reassembly_timeout)
        self._scheduled_outputs: deque[ScheduledOutput] = deque()
        self._next_output_at = 0.0

        if self.config.fake_delay_min_s < 0:
            raise ValueError("fake_delay_min_s must be >= 0")
        if self.config.fake_delay_max_s < self.config.fake_delay_min_s:
            raise ValueError("fake_delay_max_s must be >= fake_delay_min_s")
        if self.config.output_interval_s < 0:
            raise ValueError("output_interval_s must be >= 0")

    def _log(self, event: str, **fields: object) -> None:
        payload = {"ts": time.time(), "event": event, **fields}
        self.logger.info(json.dumps(payload, ensure_ascii=False))

    def _send_ack(self, ack: AckFrame) -> None:
        raw = encode_ack(ack)
        framed = cobs_encode(raw) + b"\x00"
        self.ack_endpoint.write(framed)
        self.metrics.total_acks_tx += 1
        self._log(
            "ack_sent",
            msg_id=ack.msg_id,
            frag_cnt=ack.frag_cnt,
            status=int(ack.status),
            bitmap=ack.bitmap.hex(),
        )

    def _handle_gc(self) -> None:
        for record in self._reassembly.collect_garbage():
            self._log(
                "reassembly_timeout",
                msg_id=record.msg_id,
                received_count=record.received_count,
                expected_count=record.expected_count,
                bitmap=record.bitmap.hex(),
                timeout_count=record.timeout_count,
            )
            self._send_ack(record.abort_ack)

    def _sample_fake_delay(self) -> float:
        if not self.config.enable_fake_delay:
            return 0.0
        if self.config.fake_delay_max_s == self.config.fake_delay_min_s:
            return self.config.fake_delay_min_s
        return random.uniform(self.config.fake_delay_min_s, self.config.fake_delay_max_s)

    def _emit_output(self, msg_id: int, rtcm: bytes, latency_s: float | None, fake_delay_s: float) -> None:
        try:
            self.out_sink.write(rtcm)
            self.metrics.total_rtcm_frames_out += 1
            self._log(
                "reassembly_complete",
                msg_id=msg_id,
                rtcm_crc="ok",
                output="success",
                latency_s=latency_s,
                fake_delay_s=fake_delay_s,
            )
        except Exception as exc:
            self.metrics.output_fail += 1
            self._log(
                "reassembly_complete",
                msg_id=msg_id,
                rtcm_crc="ok",
                output="fail",
                detail=str(exc),
                fake_delay_s=fake_delay_s,
            )

    def _schedule_output(self, msg_id: int, rtcm: bytes, latency_s: float | None) -> None:
        delay_s = self._sample_fake_delay()
        if delay_s <= 0 and self.config.output_interval_s <= 0:
            self._emit_output(msg_id=msg_id, rtcm=rtcm, latency_s=latency_s, fake_delay_s=0.0)
            return

        job = ScheduledOutput(
            msg_id=msg_id,
            rtcm=rtcm,
            latency_s=latency_s,
            fake_delay_s=delay_s,
            ready_at=time.monotonic() + delay_s,
        )
        self._scheduled_outputs.append(job)
        self._log(
            "fake_delay_enqueue",
            msg_id=msg_id,
            rtcm_len=len(rtcm),
            delay_s=delay_s,
            output_interval_s=self.config.output_interval_s,
            queue_len=len(self._scheduled_outputs),
        )

    def _flush_scheduled_outputs(self) -> None:
        now = time.monotonic()

        if self.config.output_interval_s <= 0:
            while self._scheduled_outputs and self._scheduled_outputs[0].ready_at <= now:
                job = self._scheduled_outputs.popleft()
                self._emit_output(
                    msg_id=job.msg_id,
                    rtcm=job.rtcm,
                    latency_s=job.latency_s,
                    fake_delay_s=job.fake_delay_s,
                )
            return

        if not self._scheduled_outputs or now < self._next_output_at:
            return

        ready_jobs: list[ScheduledOutput] = []
        pending_jobs: deque[ScheduledOutput] = deque()
        while self._scheduled_outputs:
            job = self._scheduled_outputs.popleft()
            if job.ready_at <= now:
                ready_jobs.append(job)
            else:
                pending_jobs.append(job)
        self._scheduled_outputs = pending_jobs

        if ready_jobs:
            dropped = ready_jobs[:-1]
            if dropped:
                self.metrics.total_rtcm_frames_dropped_by_interval += len(dropped)
                self._log(
                    "scheduled_output_drop",
                    reason="output_interval_latest_only",
                    dropped_count=len(dropped),
                    output_interval_s=self.config.output_interval_s,
                )
            job = ready_jobs[-1]
            self._emit_output(
                msg_id=job.msg_id,
                rtcm=job.rtcm,
                latency_s=job.latency_s,
                fake_delay_s=job.fake_delay_s,
            )
            self._next_output_at = time.monotonic() + self.config.output_interval_s

    def process_once(self, timeout: float | None = None) -> None:
        """Process one incoming delimiter-framed packet."""
        self._flush_scheduled_outputs()
        frame = self._reader.read_frame(timeout=timeout if timeout is not None else self.config.poll_timeout)
        if frame is None:
            self._handle_gc()
            return

        if not frame:
            self._log("frame_drop", reason="empty_delimited_frame")
            self._handle_gc()
            return

        try:
            raw = cobs_decode(frame)
        except COBSDecodeError as exc:
            self.metrics.cobs_decode_fail += 1
            self._log("frame_drop", reason="cobs_decode_failed", detail=str(exc))
            self._handle_gc()
            return

        if len(raw) < 4:
            self.metrics.protocol_decode_fail += 1
            self._log("frame_drop", reason="raw_too_short")
            self._handle_gc()
            return

        if is_ack_wire_frame(raw):
            # Receiver main path ignores ACK frames.
            self._log("frame_ignore", reason="ack_frame_received")
            self._handle_gc()
            return

        try:
            fragment = decode_fragment(raw)
        except Exception as exc:
            message = str(exc)
            if "CRC16" in message:
                self.metrics.fragment_crc_fail += 1
                self._log("fragment_drop", reason="fragment_crc_fail", detail=message)
            else:
                self.metrics.protocol_decode_fail += 1
                self._log("fragment_drop", reason="fragment_decode_fail", detail=message)
            self._handle_gc()
            return

        self.metrics.total_fragments_rx += 1
        self._log(
            "fragment_rx",
            msg_id=fragment.msg_id,
            frag_idx=fragment.frag_idx,
            frag_cnt=fragment.frag_cnt,
            fragment_crc="ok",
        )

        result = self._reassembly.process_fragment(fragment)
        self._send_ack(result.ack)
        self._log(
            "bitmap_state",
            msg_id=result.ack.msg_id,
            bitmap=result.ack.bitmap.hex(),
            status=int(result.ack.status),
        )

        if result.status == AckStatus.COMPLETE and result.completed_rtcm is not None:
            self._schedule_output(
                msg_id=fragment.msg_id,
                rtcm=result.completed_rtcm,
                latency_s=result.latency_s,
            )
        elif result.status == AckStatus.INVALID:
            self._log(
                "reassembly_invalid",
                msg_id=fragment.msg_id,
                rtcm_crc="fail",
            )

        self._handle_gc()
        self._flush_scheduled_outputs()

    def run(self, stop_event: threading.Event) -> None:
        """Run processing loop until stop_event is set."""
        while not stop_event.is_set():
            self.process_once(timeout=self.config.poll_timeout)

    def summary(self) -> dict[str, object]:
        """Combined receiver summary including reassembly metrics."""
        reassembly_metrics = self._reassembly.metrics
        summary: dict[str, object] = {
            **self.metrics.to_dict(),
            "reassembly_success": reassembly_metrics.reassembly_success,
            "reassembly_fail": reassembly_metrics.reassembly_fail,
            "avg_reassembly_latency": reassembly_metrics.avg_reassembly_latency,
            "max_reassembly_latency": reassembly_metrics.max_reassembly_latency,
            "scheduled_outputs_pending": len(self._scheduled_outputs),
            "next_output_wait_s": max(0.0, self._next_output_at - time.monotonic()),
        }
        return summary


def run_receiver_cli(args: argparse.Namespace) -> int:
    """Run receiver CLI command."""
    logger = setup_logger("rtcm_receiver", args.log_file)

    in_ep = open_serial_endpoint(args.in_serial, args.baud, timeout=0.1)
    if args.ack_serial and (args.ack_serial != args.in_serial or args.ack_baud != args.baud):
        ack_ep = open_serial_endpoint(args.ack_serial, args.ack_baud, timeout=0.1)
    else:
        ack_ep = in_ep

    sinks: list[BinarySink] = []
    out_serial_ep: ByteStreamEndpoint | None = None
    if args.out_file:
        sinks.append(BinaryFileSink(args.out_file, append=True))
    if args.out_serial:
        out_serial_ep = open_serial_endpoint(args.out_serial, args.out_baud, timeout=0.1)
        sinks.append(out_serial_ep)
    if not sinks:
        raise ValueError("at least one output must be set: --out-file or --out-serial")

    receiver = Receiver(
        in_endpoint=in_ep,
        ack_endpoint=ack_ep,
        out_sink=MultiSink(sinks),
        config=ReceiverConfig(
            reassembly_timeout=args.reassembly_timeout,
            poll_timeout=0.1,
            enable_fake_delay=args.enable_fake_delay,
            fake_delay_min_s=args.fake_delay_min_s,
            fake_delay_max_s=args.fake_delay_max_s,
            output_interval_s=args.output_interval_s,
        ),
        logger=logger,
    )

    stop_event = threading.Event()
    try:
        receiver.run(stop_event)
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        in_ep.close()
        if ack_ep is not in_ep:
            ack_ep.close()
        if out_serial_ep is not None:
            out_serial_ep.close()
        for sink in sinks:
            close = getattr(sink, "close", None)
            if callable(close):
                close()

    logger.info(json.dumps({"event": "receiver_summary", **receiver.summary()}))
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI parser for receiver tool."""
    parser = argparse.ArgumentParser(description="RTCM over LoRa receiver")
    parser.add_argument("--in-serial", type=str, required=True, help="LoRa RX serial device path")
    parser.add_argument("--baud", type=int, default=115200, help="LoRa RX baud rate")
    parser.add_argument("--ack-serial", type=str, default=None, help="ACK TX serial device path")
    parser.add_argument("--ack-baud", type=int, default=115200, help="ACK TX baud rate")

    parser.add_argument("--out-file", type=str, default=None, help="Output file for complete RTCM frames")
    parser.add_argument("--out-serial", type=str, default=None, help="Output serial for complete RTCM frames")
    parser.add_argument("--out-baud", type=int, default=9600, help="Output serial baud")

    parser.add_argument("--reassembly-timeout", type=float, default=5.0, help="Reassembly timeout in seconds")
    parser.add_argument(
        "--enable-fake-delay",
        action="store_true",
        help="Inject random delay before outputting each complete RTCM frame",
    )
    parser.add_argument(
        "--fake-delay-min-s",
        type=float,
        default=1.0,
        help="Minimum fake delay in seconds when --enable-fake-delay is set",
    )
    parser.add_argument(
        "--fake-delay-max-s",
        type=float,
        default=5.0,
        help="Maximum fake delay in seconds when --enable-fake-delay is set",
    )
    parser.add_argument(
        "--output-interval-s",
        type=float,
        default=0.0,
        help="Minimum interval between output RTCM frames in seconds (0 = no pacing)",
    )
    parser.add_argument("--log-file", type=str, default=None, help="Path to JSON lines log file")
    return parser


if __name__ == "__main__":
    raise SystemExit(run_receiver_cli(build_arg_parser().parse_args()))
