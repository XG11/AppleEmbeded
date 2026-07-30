#!/usr/bin/env python3

import argparse
import sys
import time
from collections import deque

import matplotlib.pyplot as plt
import serial
from serial.tools import list_ports


def show_serial_ports() -> None:
    """Print all available serial ports."""
    ports = list(list_ports.comports())

    if not ports:
        print("No serial ports found.")
        return

    print("Available serial ports:")
    for port in ports:
        print(f"  {port.device}: {port.description}")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot ADS1220 load-cell readings in real time."
    )

    parser.add_argument(
        "--port",
        type=str,
        help="Serial port, for example /dev/cu.usbmodem123456",
    )

    parser.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="Serial baud rate. Default: 115200",
    )

    parser.add_argument(
        "--window",
        type=float,
        default=10.0,
        help="Visible time window in seconds. Default: 10",
    )

    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="List available serial ports and exit.",
    )

    parser.add_argument(
        "--zero",
        action="store_true",
        help="Subtract the first valid reading from all later readings.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    if args.list_ports:
        show_serial_ports()
        return

    if not args.port:
        print("Error: --port is required.")
        print()
        show_serial_ports()
        sys.exit(1)

    try:
        ser = serial.Serial(
            port=args.port,
            baudrate=args.baud,
            timeout=0.05,
        )
    except serial.SerialException as exc:
        print(f"Could not open {args.port}: {exc}")
        sys.exit(1)

    # Opening a Teensy serial connection may reset the board.
    time.sleep(2.0)
    ser.reset_input_buffer()

    times = deque()
    values = deque()

    start_time = time.perf_counter()
    zero_offset = None

    plt.ion()

    fig, ax = plt.subplots()
    line, = ax.plot([], [])

    ax.set_title("ADS1220 Load Cell — Real-Time Raw Reading")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel(
        "Zeroed ADC counts" if args.zero else "Raw ADC counts"
    )
    ax.grid(True)

    print(f"Reading from {args.port} at {args.baud} baud")
    print("Close the plot window or press Ctrl+C to stop.")

    invalid_lines = 0

    try:
        while plt.fignum_exists(fig.number):
            # Read all currently available complete lines.
            while ser.in_waiting > 0:
                raw_line = ser.readline()

                try:
                    text = raw_line.decode(
                        "utf-8",
                        errors="ignore",
                    ).strip()

                    if not text:
                        continue

                    # Teensy should print one integer per line.
                    adc_raw = int(text)

                except ValueError:
                    invalid_lines += 1
                    continue

                current_time = time.perf_counter() - start_time

                if zero_offset is None:
                    zero_offset = adc_raw
                    if args.zero:
                        print(f"Initial zero offset: {zero_offset}")

                plotted_value = (
                    adc_raw - zero_offset
                    if args.zero
                    else adc_raw
                )

                times.append(current_time)
                values.append(plotted_value)

                # Remove samples outside the visible time window.
                cutoff_time = current_time - args.window

                while times and times[0] < cutoff_time:
                    times.popleft()
                    values.popleft()

            if times:
                line.set_data(times, values)

                right_edge = max(args.window, times[-1])
                left_edge = max(0.0, right_edge - args.window)

                ax.set_xlim(left_edge, right_edge)

                minimum = min(values)
                maximum = max(values)

                if minimum == maximum:
                    padding = max(1.0, abs(minimum) * 0.01)
                else:
                    padding = max(
                        1.0,
                        0.10 * (maximum - minimum),
                    )

                ax.set_ylim(
                    minimum - padding,
                    maximum + padding,
                )

            fig.canvas.draw_idle()
            fig.canvas.flush_events()
            plt.pause(0.01)

    except KeyboardInterrupt:
        print("\nStopped by user.")

    finally:
        ser.close()
        plt.ioff()

        print(f"Invalid serial lines ignored: {invalid_lines}")
        print("Serial port closed.")


if __name__ == "__main__":
    main()