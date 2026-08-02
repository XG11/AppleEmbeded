import argparse
import csv
import re
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import serial
from serial.tools import list_ports


NUMBER_PATTERN = re.compile(r"[-+]?(?:\d*\.\d+|\d+)")


def list_serial_ports():
    ports = list(list_ports.comports())

    if not ports:
        print("No serial ports found.")
        return

    print("Available serial ports:")

    for port in ports:
        print(f"  {port.device:<25} {port.description}")


def parse_loadcell_line(line):
    """
    Extract the last number from each Teensy serial line.

    Supported formats:
        -12345
        modified_weight: -12345
        raw: -12345
        123456789,-12345
    """
    matches = NUMBER_PATTERN.findall(line)

    if not matches:
        return None

    try:
        return float(matches[-1])
    except ValueError:
        return None


def record_loadcell(port, baud, duration, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    sample_count = 0
    invalid_lines = 0
    last_status_time = 0.0

    print("Load-cell recording")
    print(f"  Port:     {port}")
    print(f"  Baud:     {baud}")
    print(f"  Duration: {duration:.2f} seconds")
    print(f"  Output:   {output_path}")
    print("\nRecording... Press Ctrl+C to stop early.")

    try:
        with serial.Serial(
            port=port,
            baudrate=baud,
            timeout=0.1,
        ) as ser:

            ser.reset_input_buffer()

            start_time_ns = time.perf_counter_ns()
            end_time_ns = start_time_ns + int(duration * 1e9)

            with output_path.open("w", newline="") as csv_file:
                writer = csv.writer(csv_file)

                writer.writerow(
                    [
                        "host_time_ns",
                        "host_elapsed_s",
                        "loadcell_raw",
                    ]
                )

                while time.perf_counter_ns() < end_time_ns:
                    raw_bytes = ser.readline()

                    if not raw_bytes:
                        continue

                    host_time_ns = time.perf_counter_ns()

                    line = raw_bytes.decode(
                        "utf-8",
                        errors="ignore",
                    ).strip()

                    value = parse_loadcell_line(line)

                    if value is None:
                        invalid_lines += 1
                        continue

                    elapsed_s = (
                        host_time_ns - start_time_ns
                    ) / 1e9

                    writer.writerow(
                        [
                            host_time_ns,
                            f"{elapsed_s:.9f}",
                            value,
                        ]
                    )

                    sample_count += 1

                    # Print only once per second to avoid slowing recording.
                    if elapsed_s - last_status_time >= 1.0:
                        last_status_time = elapsed_s

                        print(
                            f"\rTime: {elapsed_s:7.2f} s | "
                            f"Samples: {sample_count:8d} | "
                            f"Value: {value:14.3f}",
                            end="",
                            flush=True,
                        )

    except KeyboardInterrupt:
        print("\nRecording stopped by user.")

    except serial.SerialException as error:
        print(f"\nSerial error: {error}")
        return None

    print("\n")
    print(f"Recorded samples: {sample_count}")
    print(f"Invalid lines:    {invalid_lines}")
    print(f"Saved CSV:        {output_path}")

    return output_path


def plot_loadcell(csv_path, remove_offset=False):
    csv_path = Path(csv_path)

    df = pd.read_csv(csv_path)

    required_columns = {
        "host_elapsed_s",
        "loadcell_raw",
    }

    missing_columns = required_columns - set(df.columns)

    if missing_columns:
        raise ValueError(
            f"Missing CSV columns: {sorted(missing_columns)}"
        )

    time_s = pd.to_numeric(
        df["host_elapsed_s"],
        errors="coerce",
    ).to_numpy()

    loadcell = pd.to_numeric(
        df["loadcell_raw"],
        errors="coerce",
    ).to_numpy()

    valid = np.isfinite(time_s) & np.isfinite(loadcell)

    time_s = time_s[valid]
    loadcell = loadcell[valid]

    if len(time_s) == 0:
        print("No valid load-cell samples were recorded.")
        return

    # Start plotted time exactly at zero.
    time_s = time_s - time_s[0]

    if remove_offset:
        baseline_samples = min(100, len(loadcell))
        baseline = np.mean(loadcell[:baseline_samples])
        plotted_loadcell = loadcell - baseline
        ylabel = "Load-cell reading minus initial offset"
    else:
        baseline = 0.0
        plotted_loadcell = loadcell
        ylabel = "Load-cell raw reading"

    if len(time_s) > 1 and time_s[-1] > 0:
        average_sps = (len(time_s) - 1) / time_s[-1]
    else:
        average_sps = 0.0

    print(f"Recording duration:  {time_s[-1]:.3f} seconds")
    print(f"Average sample rate: {average_sps:.2f} SPS")

    if remove_offset:
        print(f"Removed offset:      {baseline:.3f}")

    fig, ax = plt.subplots(figsize=(13, 6))

    ax.plot(
        time_s,
        plotted_loadcell,
        linewidth=0.8,
    )

    ax.set_title(
        f"Load-cell recording — {average_sps:.1f} SPS"
    )
    ax.set_xlabel("Elapsed time (s)")
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)

    # Display precise values when moving the cursor.
    ax.format_coord = lambda x, y: (
        f"time={x:.6f} s, loadcell={y:.3f}"
    )

    fig.tight_layout()

    print("\nUse the Matplotlib toolbar:")
    print("  Magnifying glass: zoom into a selected region")
    print("  Hand:              pan")
    print("  Home:              reset the view")
    print("  Back/forward:      navigate previous zoom levels")
    print("  Save:              save the current plotted view")

    plt.show()


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Record load-cell readings to CSV, then open a "
            "zoomable plot after recording."
        )
    )

    parser.add_argument(
        "--port",
        help="Serial port such as /dev/cu.usbmodem201104101",
    )

    parser.add_argument(
        "--baud",
        type=int,
        default=2_000_000,
        help="Serial baud rate. Default: 2000000",
    )

    parser.add_argument(
        "--duration",
        type=float,
        default=30.0,
        help="Recording duration in seconds. Default: 30",
    )

    parser.add_argument(
        "--output",
        default="loadcell.csv",
        help="Output CSV filename",
    )

    parser.add_argument(
        "--plot-only",
        metavar="CSV",
        help="Plot an existing CSV without recording",
    )

    parser.add_argument(
        "--remove-offset",
        action="store_true",
        help="Subtract the mean of the first 100 samples",
    )

    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Record and save without opening a plot",
    )

    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="List available serial ports",
    )

    args = parser.parse_args()

    if args.list_ports:
        list_serial_ports()
        return

    if args.plot_only:
        plot_loadcell(
            args.plot_only,
            remove_offset=args.remove_offset,
        )
        return

    if not args.port:
        parser.error(
            "--port is required unless --plot-only or "
            "--list-ports is used"
        )

    csv_path = record_loadcell(
        port=args.port,
        baud=args.baud,
        duration=args.duration,
        output_path=args.output,
    )

    if csv_path is not None and not args.no_plot:
        plot_loadcell(
            csv_path,
            remove_offset=args.remove_offset,
        )


if __name__ == "__main__":
    main()