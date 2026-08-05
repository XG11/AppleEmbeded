#!/usr/bin/env python3

"""
Interactive IMU and load-cell plotter.

Expected CSV columns:

    host_time_ns
    host_elapsed_s
    teensy_time_us
    type
    acc_x_raw
    acc_y_raw
    acc_z_raw
    gyro_x_raw
    gyro_y_raw
    gyro_z_raw
    load_cell_raw

Packet types:

    imu
        Contains accelerometer and gyroscope readings.
        load_cell_raw is empty.

    loadcell
        Contains load_cell_raw.
        IMU columns are empty.

IMU configuration used:

    Accelerometer: ±32 g
    Gyroscope:     ±2000 degrees/s

Output units:

    Accelerometer: m/s²
    Gyroscope:     rad/s
    Load cell:     raw ADS1220 ADC counts

Install:

    python -m pip install numpy pandas matplotlib

Run:

    python plot_imu_loadcell.py imu_loadcell.csv

Optional time range:

    python plot_imu_loadcell.py imu_loadcell.csv \
        --start 10 \
        --end 15
"""

import argparse
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------
# Sensor conversion constants
# ---------------------------------------------------------------------

STANDARD_GRAVITY_MS2 = 9.80665

# LSM6DSO32 firmware configuration:
#
# CTRL1_XL = 0xA4 -> ±32 g
# CTRL2_G  = 0xAC -> ±2000 degrees/s

ACCEL_G_PER_LSB = 0.000976

ACCEL_MS2_PER_LSB = (
    ACCEL_G_PER_LSB
    * STANDARD_GRAVITY_MS2
)

GYRO_DPS_PER_LSB = 0.070

GYRO_RADS_PER_LSB = np.deg2rad(
    GYRO_DPS_PER_LSB
)


# ADS1220 signed 24-bit output range
ADS1220_MIN = -8_388_608
ADS1220_MAX = 8_388_607


# ---------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------

def parse_arguments():
    parser = argparse.ArgumentParser(
        description=(
            "Interactively plot synchronized IMU and "
            "ADS1220 load-cell readings."
        )
    )

    parser.add_argument(
        "csv",
        type=Path,
        help="Path to imu_loadcell.csv",
    )

    parser.add_argument(
        "--start",
        type=float,
        default=None,
        help=(
            "Optional plot start time in seconds, "
            "using host_elapsed_s."
        ),
    )

    parser.add_argument(
        "--end",
        type=float,
        default=None,
        help=(
            "Optional plot end time in seconds, "
            "using host_elapsed_s."
        ),
    )

    parser.add_argument(
        "--max-points",
        type=int,
        default=200_000,
        help=(
            "Maximum plotted points per signal. "
            "Data is downsampled only for display. "
            "Default: 200000"
        ),
    )

    parser.add_argument(
        "--load-cell-zero",
        type=float,
        default=None,
        help=(
            "Optional zero-load offset in ADC counts."
        ),
    )

    parser.add_argument(
        "--load-cell-counts-per-newton",
        type=float,
        default=None,
        help=(
            "Optional load-cell calibration factor "
            "in ADC counts per newton."
        ),
    )

    args = parser.parse_args()

    calibration_arguments = [
        args.load_cell_zero,
        args.load_cell_counts_per_newton,
    ]

    if (
        any(value is not None for value in calibration_arguments)
        and not all(
            value is not None
            for value in calibration_arguments
        )
    ):
        parser.error(
            "--load-cell-zero and "
            "--load-cell-counts-per-newton "
            "must be provided together."
        )

    if (
        args.load_cell_counts_per_newton is not None
        and args.load_cell_counts_per_newton == 0
    ):
        parser.error(
            "--load-cell-counts-per-newton "
            "cannot be zero."
        )

    if (
        args.start is not None
        and args.end is not None
        and args.start >= args.end
    ):
        parser.error(
            "--start must be smaller than --end."
        )

    return args


# ---------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------

def validate_columns(df):
    required_columns = {
        "host_time_ns",
        "host_elapsed_s",
        "teensy_time_us",
        "type",
        "acc_x_raw",
        "acc_y_raw",
        "acc_z_raw",
        "gyro_x_raw",
        "gyro_y_raw",
        "gyro_z_raw",
        "load_cell_raw",
    }

    missing_columns = required_columns.difference(
        df.columns
    )

    if missing_columns:
        raise ValueError(
            "CSV is missing required columns:\n  "
            + "\n  ".join(
                sorted(missing_columns)
            )
            + "\n\nAvailable columns:\n  "
            + "\n  ".join(
                map(str, df.columns)
            )
        )


def load_sensor_csv(csv_path):
    df = pd.read_csv(csv_path)

    if df.empty:
        raise ValueError(
            f"CSV file is empty: {csv_path}"
        )

    validate_columns(df)

    original_row_count = len(df)

    # Normalize packet labels.
    df["type"] = (
        df["type"]
        .astype(str)
        .str.strip()
        .str.lower()
    )

    recognized_mask = df["type"].isin(
        ["imu", "loadcell"]
    )

    ignored_packet_count = int(
        np.sum(~recognized_mask)
    )

    df = df.loc[
        recognized_mask
    ].copy()

    # -------------------------------------------------------------
    # Separate asynchronous packet types
    # -------------------------------------------------------------

    imu_df = df.loc[
        df["type"] == "imu"
    ].copy()

    load_cell_df = df.loc[
        df["type"] == "loadcell"
    ].copy()

    # -------------------------------------------------------------
    # Validate IMU rows
    # -------------------------------------------------------------

    imu_numeric_columns = [
        "host_time_ns",
        "host_elapsed_s",
        "teensy_time_us",
        "acc_x_raw",
        "acc_y_raw",
        "acc_z_raw",
        "gyro_x_raw",
        "gyro_y_raw",
        "gyro_z_raw",
    ]

    for column in imu_numeric_columns:
        imu_df[column] = pd.to_numeric(
            imu_df[column],
            errors="coerce",
        )

    imu_count_before_validation = len(
        imu_df
    )

    imu_df = imu_df.dropna(
        subset=imu_numeric_columns
    ).copy()

    imu_valid_mask = np.ones(
        len(imu_df),
        dtype=bool,
    )

    for column in [
        "acc_x_raw",
        "acc_y_raw",
        "acc_z_raw",
        "gyro_x_raw",
        "gyro_y_raw",
        "gyro_z_raw",
    ]:
        imu_valid_mask &= (
            imu_df[column]
            .between(-32768, 32767)
            .to_numpy()
        )

    imu_df = imu_df.loc[
        imu_valid_mask
    ].copy()

    imu_df = imu_df.sort_values(
        "host_time_ns"
    ).reset_index(drop=True)

    rejected_imu_count = (
        imu_count_before_validation
        - len(imu_df)
    )

    # -------------------------------------------------------------
    # Validate load-cell rows
    # -------------------------------------------------------------

    load_cell_numeric_columns = [
        "host_time_ns",
        "host_elapsed_s",
        "teensy_time_us",
        "load_cell_raw",
    ]

    for column in load_cell_numeric_columns:
        load_cell_df[column] = pd.to_numeric(
            load_cell_df[column],
            errors="coerce",
        )

    load_cell_count_before_validation = len(
        load_cell_df
    )

    load_cell_df = load_cell_df.dropna(
        subset=load_cell_numeric_columns
    ).copy()

    load_cell_valid_mask = (
        load_cell_df["load_cell_raw"]
        .between(
            ADS1220_MIN,
            ADS1220_MAX,
        )
        .to_numpy()
    )

    load_cell_df = load_cell_df.loc[
        load_cell_valid_mask
    ].copy()

    load_cell_df = load_cell_df.sort_values(
        "host_time_ns"
    ).reset_index(drop=True)

    rejected_load_cell_count = (
        load_cell_count_before_validation
        - len(load_cell_df)
    )

    if imu_df.empty:
        raise ValueError(
            "No valid IMU packets remained "
            "after validation."
        )

    if load_cell_df.empty:
        raise ValueError(
            "No valid load-cell packets remained "
            "after validation."
        )

    # -------------------------------------------------------------
    # Build output arrays
    # -------------------------------------------------------------

    imu_time_s = (
        imu_df["host_elapsed_s"]
        .to_numpy(dtype=float)
    )

    load_cell_time_s = (
        load_cell_df["host_elapsed_s"]
        .to_numpy(dtype=float)
    )

    acceleration = {
        "time_s": imu_time_s,
        "x": (
            imu_df["acc_x_raw"]
            .to_numpy(dtype=float)
            * ACCEL_MS2_PER_LSB
        ),
        "y": (
            imu_df["acc_y_raw"]
            .to_numpy(dtype=float)
            * ACCEL_MS2_PER_LSB
        ),
        "z": (
            imu_df["acc_z_raw"]
            .to_numpy(dtype=float)
            * ACCEL_MS2_PER_LSB
        ),
    }

    gyroscope = {
        "time_s": imu_time_s,
        "x": (
            imu_df["gyro_x_raw"]
            .to_numpy(dtype=float)
            * GYRO_RADS_PER_LSB
        ),
        "y": (
            imu_df["gyro_y_raw"]
            .to_numpy(dtype=float)
            * GYRO_RADS_PER_LSB
        ),
        "z": (
            imu_df["gyro_z_raw"]
            .to_numpy(dtype=float)
            * GYRO_RADS_PER_LSB
        ),
    }

    load_cell_raw = (
        load_cell_df["load_cell_raw"]
        .to_numpy(
            dtype=float,
            copy=True,
        )
    )

    # Exact minimum and maximum values indicate ADC saturation.
    load_cell_saturated = (
        (load_cell_raw == ADS1220_MIN)
        | (load_cell_raw == ADS1220_MAX)
    )

    load_cell_raw[
        load_cell_saturated
    ] = np.nan

    load_cell = {
        "time_s": load_cell_time_s,
        "raw": load_cell_raw,
        "saturated": load_cell_saturated,
    }

    print()
    print("Sensor data loaded")
    print("-" * 60)
    print(
        f"Total CSV rows:             "
        f"{original_row_count:,}"
    )
    print(
        f"Valid IMU packets:          "
        f"{len(imu_df):,}"
    )
    print(
        f"Valid load-cell packets:    "
        f"{len(load_cell_df):,}"
    )
    print(
        f"Rejected IMU packets:       "
        f"{rejected_imu_count:,}"
    )
    print(
        f"Rejected load-cell packets: "
        f"{rejected_load_cell_count:,}"
    )
    print(
        f"Ignored packet rows:        "
        f"{ignored_packet_count:,}"
    )
    print(
        f"ADS1220 saturated samples:  "
        f"{np.sum(load_cell_saturated):,}"
    )

    return {
        "accel": acceleration,
        "gyro": gyroscope,
        "load_cell": load_cell,
    }


# ---------------------------------------------------------------------
# Plot helpers
# ---------------------------------------------------------------------

def downsample_signal(
    time_s,
    values,
    max_points,
):
    time_s = np.asarray(
        time_s,
        dtype=float,
    )

    values = np.asarray(
        values,
        dtype=float,
    )

    if max_points is None or max_points <= 0:
        return time_s, values

    if len(time_s) <= max_points:
        return time_s, values

    step = int(
        math.ceil(
            len(time_s)
            / max_points
        )
    )

    return (
        time_s[::step],
        values[::step],
    )


def apply_time_range(
    time_s,
    values,
    start_s,
    end_s,
):
    time_s = np.asarray(
        time_s,
        dtype=float,
    )

    values = np.asarray(
        values,
        dtype=float,
    )

    valid = (
        np.isfinite(time_s)
        & np.isfinite(values)
    )

    if start_s is not None:
        valid &= time_s >= start_s

    if end_s is not None:
        valid &= time_s <= end_s

    return (
        time_s[valid],
        values[valid],
    )


def prepare_signal(
    time_s,
    values,
    start_s,
    end_s,
    max_points,
):
    plot_time, plot_values = apply_time_range(
        time_s,
        values,
        start_s,
        end_s,
    )

    return downsample_signal(
        plot_time,
        plot_values,
        max_points,
    )


# ---------------------------------------------------------------------
# Interactive plotting
# ---------------------------------------------------------------------

def plot_interactive(
    sensor_data,
    start_s=None,
    end_s=None,
    max_points=200_000,
    load_cell_zero=None,
    counts_per_newton=None,
):
    accel = sensor_data["accel"]
    gyro = sensor_data["gyro"]
    load_cell = sensor_data["load_cell"]

    if (
        load_cell_zero is not None
        and counts_per_newton is not None
    ):
        load_cell_values = (
            load_cell["raw"]
            - load_cell_zero
        ) / counts_per_newton

        load_cell_ylabel = "Force (N)"
        load_cell_title = "Load Cell Force"
    else:
        load_cell_values = load_cell["raw"]
        load_cell_ylabel = "ADC counts"
        load_cell_title = "Load Cell Raw Output"

    fig, axes = plt.subplots(
        3,
        1,
        figsize=(16, 10),
        sharex=True,
    )

    # -------------------------------------------------------------
    # Accelerometer
    # -------------------------------------------------------------

    for axis_name, label in [
        ("x", "X"),
        ("y", "Y"),
        ("z", "Z"),
    ]:
        plot_time, plot_values = prepare_signal(
            accel["time_s"],
            accel[axis_name],
            start_s,
            end_s,
            max_points,
        )

        axes[0].plot(
            plot_time,
            plot_values,
            linewidth=0.8,
            label=label,
        )

    axes[0].set_title(
        "Accelerometer"
    )

    axes[0].set_ylabel(
        "Acceleration (m/s²)"
    )

    axes[0].grid(
        True,
        alpha=0.3,
    )

    axes[0].legend(
        loc="upper right"
    )

    # -------------------------------------------------------------
    # Gyroscope
    # -------------------------------------------------------------

    for axis_name, label in [
        ("x", "X"),
        ("y", "Y"),
        ("z", "Z"),
    ]:
        plot_time, plot_values = prepare_signal(
            gyro["time_s"],
            gyro[axis_name],
            start_s,
            end_s,
            max_points,
        )

        axes[1].plot(
            plot_time,
            plot_values,
            linewidth=0.8,
            label=label,
        )

    axes[1].set_title(
        "Gyroscope"
    )

    axes[1].set_ylabel(
        "Angular velocity (rad/s)"
    )

    axes[1].grid(
        True,
        alpha=0.3,
    )

    axes[1].legend(
        loc="upper right"
    )

    # -------------------------------------------------------------
    # Load cell
    # -------------------------------------------------------------

    plot_time, plot_values = prepare_signal(
        load_cell["time_s"],
        load_cell_values,
        start_s,
        end_s,
        max_points,
    )

    axes[2].plot(
        plot_time,
        plot_values,
        linewidth=0.9,
        label="Load cell",
    )

    axes[2].set_title(
        load_cell_title
    )

    axes[2].set_ylabel(
        load_cell_ylabel
    )

    axes[2].set_xlabel(
        "Elapsed time (s)"
    )

    axes[2].grid(
        True,
        alpha=0.3,
    )

    axes[2].legend(
        loc="upper right"
    )

    # -------------------------------------------------------------
    # Figure formatting
    # -------------------------------------------------------------

    fig.suptitle(
        "Interactive IMU and Load-Cell Readings",
        fontsize=15,
    )

    fig.tight_layout(
        rect=[0, 0, 1, 0.97]
    )

    # Keyboard interactions.
    original_limits = {
        "x": axes[2].get_xlim(),
        "y": [
            axis.get_ylim()
            for axis in axes
        ],
    }

    def on_key_press(event):
        if event.key == "r":
            axes[2].set_xlim(
                original_limits["x"]
            )

            for axis, limits in zip(
                axes,
                original_limits["y"],
            ):
                axis.set_ylim(limits)

            fig.canvas.draw_idle()

        elif event.key == "g":
            for axis in axes:
                axis.grid(
                    not axis.xaxis._major_tick_kw.get(
                        "gridOn",
                        False,
                    ),
                    alpha=0.3,
                )

            fig.canvas.draw_idle()

    fig.canvas.mpl_connect(
        "key_press_event",
        on_key_press,
    )

    print()
    print("Interactive plot controls")
    print("-" * 60)
    print(
        "Magnifying glass: drag to zoom"
    )
    print(
        "Hand tool:         drag to pan"
    )
    print(
        "Home button:       reset view"
    )
    print(
        "Back/forward:      navigate previous views"
    )
    print(
        "Keyboard r:        reset view"
    )
    print(
        "Keyboard g:        toggle grid"
    )
    print()

    plt.show()


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    args = parse_arguments()

    csv_path = (
        args.csv
        .expanduser()
        .resolve()
    )

    if not csv_path.exists():
        raise FileNotFoundError(
            f"CSV file does not exist: "
            f"{csv_path}"
        )

    print(
        f"Loading: {csv_path}"
    )

    sensor_data = load_sensor_csv(
        csv_path
    )

    plot_interactive(
        sensor_data,
        start_s=args.start,
        end_s=args.end,
        max_points=args.max_points,
        load_cell_zero=args.load_cell_zero,
        counts_per_newton=(
            args.load_cell_counts_per_newton
        ),
    )


if __name__ == "__main__":
    main()