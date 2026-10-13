#!/usr/bin/env python3
"""One-time MMS101 warm-up and zero-tare.

Run this once before a batch of recording trials (e.g. before starting
auto_record_launcher.py or record_multimodal.py). The sensor's Fz axis
drifts for a few minutes after power-on, so the datasheet recommends
warming up before taring -- see README.md, "Drift and zeroing". The
warm-up here means actually streaming data for --warmup-seconds (default
5 minutes), not just sitting powered-on and idle; only once that window
has elapsed does it average a burst of samples, taken from the same
ongoing stream, to find the per-axis zero offset, which is then saved to
a JSON file.

The core sequence (bring-up, warm-up, tare) lives in warm_up_and_tare()
below so mms101/continuous_stream.py can reuse it -- that module keeps
the sensor streaming continuously across a whole batch of trials instead
of running this as a separate one-shot script. This script is the
one-shot, standalone entry point: it calls warm_up_and_tare(), then
additionally stops streaming and saves the result to a JSON file, for
use by recorder/force_torque.py (used by record_multimodal.py when NOT
running under auto_record_launcher.py's continuous-streaming mode).

The sensor is left powered on (streaming stopped) when this script
exits. Run power_off.py once the whole data-collection session is done.

Keep the sensor completely unloaded for the full warm-up and tare.
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    # Running as `python mms101/warmup_and_tare.py`: the script's own
    # directory is on sys.path, so this is a sibling import.
    from mms101_read import AXES, MMS101, MMS101Error, find_port
except ImportError:
    # Imported as mms101.warmup_and_tare (e.g. by
    # mms101/continuous_stream.py, itself imported from repo root):
    # mms101_read has to be reached through the package instead.
    from mms101.mms101_read import AXES, MMS101, MMS101Error, find_port


def warm_up_and_tare(
    dev,
    interval_us=1000,
    restart_samples=100,
    warmup_seconds=300.0,
    tare_samples=500,
    verbose=True,
):
    """
    Run the full MMS101 bring-up, thermal warm-up, and zero-tare.

    Leaves the sensor powered on and ACTIVELY STREAMING when this
    returns -- it does not call dev.stop(). That is deliberate: the
    taring samples are read from the tail of one uninterrupted stream
    that started before the warm-up, so a caller that wants to keep
    streaming afterward (mms101/continuous_stream.py) can just keep
    reading dev.frames() with no gap. A caller that wants to leave the
    sensor idle instead (this module's own main(), below) should call
    dev.stop() itself once this returns.

    Returns a dict: {"offsets": {...}, "sample_count", "interval_us",
    "restart_samples", "warmup_seconds", "created_at"}.
    """

    def log(message, end="\n"):
        if verbose:
            print(message, end=end, file=sys.stderr, flush=True)

    dev.stop()  # in case a previous run left it streaming
    dev.board_select()
    log(f"Firmware: {dev.firmware_version()}")
    dev.power(True)
    time.sleep(0.1)
    dev.idle_all_axes()
    dev.bootload()
    dev.set_interval_us(interval_us)
    dev.set_restart(restart_samples)

    log(
        f"Streaming for {warmup_seconds:.0f} s to warm up. "
        "Keep the sensor completely unloaded."
    )

    dev.start()

    t_us = 0
    warming_up = True
    warmup_deadline = time.monotonic() + warmup_seconds
    last_print = 0.0
    samples = []

    for dt, vals in dev.frames():
        t_us += dt

        if t_us < 10_000:
            continue  # spec: data in the first 10 ms is invalid

        now = time.monotonic()

        if warming_up:
            if now >= warmup_deadline:
                warming_up = False
                log("")
                log(
                    f"Warm-up done. Taring over {tare_samples} samples, "
                    "still streaming. Keep the sensor completely "
                    "unloaded."
                )
                continue

            if now - last_print >= 1.0:
                last_print = now
                remaining = warmup_deadline - now
                log(f"  {remaining:6.0f} s remaining", end="\r")
            continue

        samples.append(vals)
        if len(samples) >= tare_samples:
            break

    offsets = [sum(axis_values) / len(axis_values)
               for axis_values in zip(*samples)]

    tare_data = {
        "offsets": dict(zip(AXES, offsets)),
        "sample_count": len(samples),
        "interval_us": interval_us,
        "restart_samples": restart_samples,
        "warmup_seconds": warmup_seconds,
        "created_at": datetime.now().astimezone().isoformat(),
    }

    log("Tare offsets (sensor is still streaming, unstopped):")
    for name, value in zip(AXES, offsets):
        log(f"  {name} offset = {value:+.5f}")

    return tare_data


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--port", help="serial port (default: auto-detect)")
    ap.add_argument("--interval", type=int, default=1000,
                    help="sample interval in usec (default 1000)")
    ap.add_argument("--restart", type=int, default=100,
                    help="temperature update every N samples (default 100)")
    ap.add_argument("--warmup-seconds", type=float, default=300.0,
                    help="thermal warm-up before taring, in seconds (default 300)")
    ap.add_argument("--tare-samples", type=int, default=500,
                    help="number of samples to average for the zero offset (default 500)")
    ap.add_argument("--tare-file", default="mms101/tare_offsets.json",
                    help="where to save the zero offsets (default mms101/tare_offsets.json)")
    ap.add_argument("--cr", action="store_true",
                    help="append CR to each command (try if no response)")
    args = ap.parse_args()

    dev = MMS101(args.port or find_port(), append_cr=args.cr)
    try:
        tare_data = warm_up_and_tare(
            dev,
            interval_us=args.interval,
            restart_samples=args.restart,
            warmup_seconds=args.warmup_seconds,
            tare_samples=args.tare_samples,
        )

        dev.stop()  # halts streaming only; leaves the sensor powered on

        tare_path = Path(args.tare_file)
        tare_path.parent.mkdir(parents=True, exist_ok=True)
        tare_path.write_text(json.dumps(tare_data, indent=2))

        print(f"Saved tare offsets to {tare_path}", file=sys.stderr)

        print(
            "\nSensor is powered on and idle (not streaming). "
            "Recording trials can now run. "
            "Run power_off.py once the whole session is done.",
            file=sys.stderr,
        )
    except KeyboardInterrupt:
        print("\nInterrupted; sensor left as-is.", file=sys.stderr)
    except MMS101Error as e:
        print("Error:", e, file=sys.stderr)
    finally:
        dev.close()


if __name__ == "__main__":
    main()
