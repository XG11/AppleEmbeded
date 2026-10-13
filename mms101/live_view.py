#!/usr/bin/env python3
"""Live view of the MMS101 while auto_record_launcher.py is running.

auto_record_launcher.py's continuous streamer (mms101/continuous_stream.py)
holds the sensor's serial port open for the whole session, so
mms101_plot.py cannot also open it -- it would fail with "port busy".
This script instead polls the small rolling snapshot CSV that the
continuous streamer writes out (by default mms101/live_snapshot.csv,
rewritten every --live-snapshot-interval seconds, 0.1 s by default,
with the last --live-snapshot-window seconds of tared samples) and
plots that, so it can run in a second terminal at any time during a
session without touching the serial port at all.

Run (in a second terminal, while auto_record_launcher.py is running):

    python mms101/live_view.py

Keys (plot window focused): t = request a mid-session re-tare
(auto_record_launcher.py's --retare-request-file; it is the one
actually holding the sensor, so this only touches a small trigger file
and waits for it to notice -- see mms101/continuous_stream.py's
retare()).

Close the window to stop. If the snapshot file does not exist yet, or
goes stale (no session currently running), the window keeps polling and
shows a "waiting for data" message instead of erroring out.
"""
import argparse
import csv
import time
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

AXES = ["Fx", "Fy", "Fz", "Mx", "My", "Mz"]
COLORS = ["tab:red", "tab:blue", "tab:green"]

STALE_AFTER_S = 2.0


def read_snapshot(path: Path):
    """
    Returns (time_s, {axis: [values]}) read from the snapshot CSV, with
    time_s relative to the snapshot's own last sample (0 = most
    recent). Returns None if the file is missing, empty, or stale.
    """
    try:
        with path.open(newline="") as snapshot_file:
            rows = list(csv.reader(snapshot_file))
    except FileNotFoundError:
        return None

    if len(rows) < 2:
        return None

    header = rows[0]
    try:
        time_index = header.index("host_time_ns")
        axis_indices = [header.index(axis) for axis in AXES]
    except ValueError:
        return None

    host_time_ns = []
    values = {axis: [] for axis in AXES}

    for row in rows[1:]:
        try:
            host_time_ns.append(int(row[time_index]))
            for axis, index in zip(AXES, axis_indices):
                values[axis].append(float(row[index]))
        except (ValueError, IndexError):
            continue

    if not host_time_ns:
        return None

    now_ns = time.perf_counter_ns()
    age_s = (now_ns - host_time_ns[-1]) / 1e9

    if age_s > STALE_AFTER_S:
        return None

    latest_ns = host_time_ns[-1]
    time_s = [(t - latest_ns) / 1e9 for t in host_time_ns]

    return time_s, values


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--snapshot-file",
        default="mms101/live_snapshot.csv",
        help="Snapshot file written by the continuous streamer (default: mms101/live_snapshot.csv)",
    )
    ap.add_argument(
        "--refresh-ms",
        type=int,
        default=100,
        help=(
            "Plot refresh interval in milliseconds (default 100). "
            "Total lag is roughly this plus auto_record_launcher.py's "
            "own --live-snapshot-interval (not synchronized) -- lower "
            "both for a snappier view."
        ),
    )
    ap.add_argument(
        "--retare-request-file",
        default="mms101/retare_request.flag",
        help=(
            "Touched when 't' is pressed, to ask "
            "auto_record_launcher.py to re-tare. Must match its own "
            "--retare-request-file. "
            "Default: mms101/retare_request.flag."
        ),
    )
    args = ap.parse_args()

    snapshot_path = Path(args.snapshot_file)
    retare_request_path = Path(args.retare_request_file)
    retare_requested_at = None

    fig, (ax_f, ax_m) = plt.subplots(2, 1, sharex=True, figsize=(10, 7))
    fig.canvas.manager.set_window_title("MMS101 live (auto_record_launcher.py)")

    lines = []
    for ax, names, unit in ((ax_f, AXES[:3], "Force [N]"),
                            (ax_m, AXES[3:], "Moment [N*m]")):
        for name, color in zip(names, COLORS):
            (ln,) = ax.plot([], [], color=color, lw=1, label=name)
            lines.append(ln)
        ax.set_ylabel(unit)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="upper left", ncol=3)
    ax_m.set_xlabel("Time relative to latest sample [s]   (t = request re-tare)")

    status_text = fig.text(0.5, 0.5, "", ha="center", va="center", fontsize=14, color="gray")
    retare_status_text = fig.text(
        0.99, 0.99, "", ha="right", va="top", fontsize=9, color="tab:orange"
    )

    def on_key_press(event):
        nonlocal retare_requested_at
        if event.key == "t":
            retare_request_path.touch()
            retare_requested_at = time.monotonic()

    fig.canvas.mpl_connect("key_press_event", on_key_press)

    def update(_):
        nonlocal retare_requested_at

        if retare_requested_at is not None:
            if retare_request_path.exists():
                retare_status_text.set_text("Retare requested...")
            elif time.monotonic() - retare_requested_at < 2.0:
                retare_status_text.set_text("Retare applied")
            else:
                retare_status_text.set_text("")
                retare_requested_at = None

        snapshot = read_snapshot(snapshot_path)

        if snapshot is None:
            for ln in lines:
                ln.set_data([], [])
            status_text.set_text(
                "Waiting for data...\n"
                f"({snapshot_path} missing or stale -- "
                "is auto_record_launcher.py running?)"
            )
            return lines + [status_text, retare_status_text]

        status_text.set_text("")
        time_s, values = snapshot

        for i, (axis, ln) in enumerate(zip(AXES, lines)):
            ln.set_data(time_s, values[axis])

        ax_f.set_xlim(min(time_s), max(0.0, max(time_s)))
        for ax in (ax_f, ax_m):
            ax.relim()
            ax.autoscale_view(scalex=False)

        latest = {axis: values[axis][-1] for axis in AXES}
        ax_f.set_title(
            "  ".join(f"{n}={latest[n]:+.3f}" for n in AXES[:3]) + " N",
            fontsize=10, family="monospace",
        )
        ax_m.set_title(
            "  ".join(f"{n}={latest[n]:+.5f}" for n in AXES[3:]) + " N*m",
            fontsize=10, family="monospace",
        )

        return lines + [status_text, retare_status_text]

    anim = FuncAnimation(  # noqa: F841
        fig, update, interval=args.refresh_ms, cache_frame_data=False,
    )

    fig.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
