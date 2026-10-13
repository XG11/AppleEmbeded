#!/usr/bin/env python3
"""Stop streaming and power off the MMS101.

warmup_and_tare.py and recorder/force_torque.py deliberately leave the
sensor powered on between trials (re-running the multi-minute warm-up
before every trial would make back-to-back recordings impractical).
Run this once, after the last trial of a data-collection session, to
switch the sensor's supplies off.
"""
import argparse
import sys

from mms101_read import MMS101, MMS101Error, find_port


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", help="serial port (default: auto-detect)")
    ap.add_argument("--cr", action="store_true",
                    help="append CR to each command (try if no response)")
    args = ap.parse_args()

    dev = MMS101(args.port or find_port(), append_cr=args.cr)
    try:
        dev.stop()
        dev.power(False)
        print("Sensor powered off.", file=sys.stderr)
    except MMS101Error as e:
        print("Error:", e, file=sys.stderr)
    finally:
        dev.close()


if __name__ == "__main__":
    main()
