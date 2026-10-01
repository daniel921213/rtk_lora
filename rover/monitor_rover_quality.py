#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time
from collections import Counter

try:
    import serial  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise SystemExit("pyserial is required: pip install pyserial") from exc


FIX_TEXT = {
    0: "NO_FIX",
    1: "SPS",
    2: "DGPS",
    4: "RTK_FIX",
    5: "RTK_FLOAT",
}


def parse_gga(line: str) -> dict | None:
    if "GGA" not in line:
        return None
    if not (line.startswith("$GNGGA") or line.startswith("$GPGGA") or line.startswith("$GAGGA") or line.startswith("$BDGGA")):
        return None

    p = line.split(",")
    if len(p) < 10:
        return None

    def to_int(v: str):
        try:
            return int(v)
        except Exception:
            return None

    def to_float(v: str):
        try:
            return float(v)
        except Exception:
            return None

    fix_q = to_int(p[6])
    sats = to_int(p[7])
    hdop = to_float(p[8])
    alt = to_float(p[9])
    age = to_float(p[13]) if len(p) > 13 and p[13] else None

    if fix_q is None:
        return None

    return {
        "fix_q": fix_q,
        "sats": sats,
        "hdop": hdop,
        "alt": alt,
        "age": age,
        "raw": line,
    }


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    k = (len(values) - 1) * q
    f = int(k)
    c = min(f + 1, len(values) - 1)
    if f == c:
        return values[f]
    d = k - f
    return values[f] * (1 - d) + values[c] * d


def mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / float(len(values))


def main() -> int:
    ap = argparse.ArgumentParser(description="Monitor rover GNSS quality from NMEA GGA")
    ap.add_argument("--port", default="/dev/ttyUSB0", help="GNSS NMEA serial port")
    ap.add_argument("--baud", type=int, default=115200, help="GNSS serial baud")
    ap.add_argument("--duration", type=float, default=60.0, help="seconds (<=0 means run forever)")
    ap.add_argument("--print-every", type=float, default=1.0, help="status print interval (s)")
    args = ap.parse_args()

    print(f"[quality] read {args.port} @ {args.baud}")
    print(f"[quality] duration={args.duration}s (<=0 means until Ctrl+C)")

    cnt = Counter()
    hdops: list[float] = []
    sats_list: list[int] = []
    ages: list[float] = []

    start = time.time()
    next_print = start + max(0.2, args.print_every)

    try:
        with serial.Serial(args.port, args.baud, timeout=0.5) as ser:
            while True:
                now = time.time()
                if args.duration > 0 and now - start >= args.duration:
                    break

                raw = ser.readline()
                if not raw:
                    continue

                line = raw.decode("ascii", errors="ignore").strip()
                gga = parse_gga(line)
                if not gga:
                    continue

                q = int(gga["fix_q"])
                cnt[q] += 1

                if gga["hdop"] is not None:
                    hdops.append(float(gga["hdop"]))
                if gga["sats"] is not None:
                    sats_list.append(int(gga["sats"]))
                if gga["age"] is not None:
                    ages.append(float(gga["age"]))

                if now >= next_print:
                    print(
                        "[quality] "
                        f"fix={FIX_TEXT.get(q, str(q))} "
                        f"sats={gga['sats']} hdop={gga['hdop']} age={gga['age']}"
                    )
                    next_print = now + max(0.2, args.print_every)
    except KeyboardInterrupt:
        pass
    except serial.SerialException as exc:  # type: ignore[attr-defined]
        print(f"[quality] serial error: {exc}")
        print("[quality] hint: this port may be occupied by source forwarding.")
        print("[quality] hint: run 'select_rover_source.sh stop' first or choose another GNSS NMEA port.")
        return 2

    total = sum(cnt.values())
    if total == 0:
        print("[quality] no GGA received. Check port/baud.")
        return 1

    rtk_fix = cnt[4]
    rtk_float = cnt[5]

    hdops_sorted = sorted(hdops)
    ages_sorted = sorted(ages)

    print("\n=== GNSS Quality Summary ===")
    print(f"GGA samples      : {total}")
    print(f"RTK_FIX ratio    : {rtk_fix / total:.1%} ({rtk_fix}/{total})")
    print(f"RTK_FLOAT ratio  : {rtk_float / total:.1%} ({rtk_float}/{total})")
    print(f"NO_FIX ratio     : {cnt[0] / total:.1%} ({cnt[0]}/{total})")
    print("Fix histogram    : " + ", ".join(f"{k}:{v}" for k, v in sorted(cnt.items())))

    if sats_list:
        sats_avg = mean([float(x) for x in sats_list])
        print(f"Sats avg         : {sats_avg:.2f}" if sats_avg is not None else "Sats avg         : n/a")
    if hdops:
        p95_hdop = pct(hdops_sorted, 0.95)
        hdop_avg = mean(hdops)
        print(f"HDOP avg         : {hdop_avg:.3f}" if hdop_avg is not None else "HDOP avg         : n/a")
        print(f"HDOP p95         : {p95_hdop:.3f}" if p95_hdop is not None else "HDOP p95         : n/a")
    if ages:
        p95_age = pct(ages_sorted, 0.95)
        age_avg = mean(ages)
        print(f"Corr age avg(s)  : {age_avg:.3f}" if age_avg is not None else "Corr age avg(s)  : n/a")
        print(f"Corr age p95(s)  : {p95_age:.3f}" if p95_age is not None else "Corr age p95(s)  : n/a")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
