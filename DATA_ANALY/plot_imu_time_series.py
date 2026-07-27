#!/usr/bin/env python3

"""
Plot LSM6DSO32 accelerometer and gyroscope time-series data.

Expected CSV columns:
    host_time_ns,host_elapsed_s,teensy_time_us,type,
    x_raw,y_raw,z_raw,fifo_remaining

Current firmware configuration:
    CTRL1_XL = 0xA4
        Accelerometer full scale: ±32 g
        Sensitivity: 0.976 mg/LSB

    CTRL2_G = 0xAC
        Gyroscope full scale: ±2000 degrees/s
        Sensitivity: 70 mdps/LSB

Outputs:
    imu_time_series.png
    imu_time_series_axes.png
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------
# LSM6DSO32 conversion constants
# ---------------------------------------------------------------------

STANDARD_GRAVITY_MS2 = 9.80665

# CTRL1_XL = 0xA4 -> ±32 g
ACCEL_G_PER_LSB = 0.000976
ACCEL_MS2_PER_LSB = ACCEL_G_PER_LSB * STANDARD_GRAVITY_MS2

# CTRL2_G = 0xAC -> ±2000 degrees/s
GYRO_DPS_PER_LSB = 0.070
GYRO_RADS_PER_LSB = np.deg2rad(GYRO_DPS_PER_LSB)


# ---------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------

def load_imu_csv(csv_path: Path) -> dict[str, dict[str, np.ndarray]]:
    """
    Load an IMU CSV and convert raw measurements into physical units.

    Returns:
        {
            "accel": {
                "time_s": ...,
                "x": ...,
                "y": ...,
                "z": ...,
                "unit": "m/s²",
            },
            "gyro": {
                "time_s": ...,
                "x": ...,
                "y": ...,
                "z": ...,
                "unit": "rad/s",
            },
        }
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"IMU CSV does not exist: {csv_path}")

    df = pd.read_csv(csv_path)

    if df.empty:
        raise ValueError(f"IMU CSV is empty: {csv_path}")

    required_columns = {
        "type",
        "x_raw",
        "y_raw",
        "z_raw",
    }

    missing_columns = required_columns.difference(df.columns)

    if missing_columns:
        raise ValueError(
            f"Missing required columns: {sorted(missing_columns)}\n"
            f"Available columns: {list(df.columns)}"
        )

    # Prefer host_elapsed_s because it is already relative to the recording
    # start. Fall back to host_time_ns or Teensy timestamps when necessary.
    if "host_elapsed_s" in df.columns:
        time_s = pd.to_numeric(
            df["host_elapsed_s"],
            errors="coerce",
        )

    elif "host_time_ns" in df.columns:
        absolute_time_s = pd.to_numeric(
            df["host_time_ns"],
            errors="coerce",
        ) * 1e-9

        time_s = absolute_time_s - absolute_time_s.iloc[0]

    elif "teensy_time_us" in df.columns:
        teensy_time_s = pd.to_numeric(
            df["teensy_time_us"],
            errors="coerce",
        ) * 1e-6

        time_s = teensy_time_s - teensy_time_s.iloc[0]

    else:
        raise ValueError(
            "No usable timestamp column was found. Expected one of: "
            "host_elapsed_s, host_time_ns, or teensy_time_us."
        )

    df["_time_s"] = time_s

    for column in ["x_raw", "y_raw", "z_raw"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df["_type"] = (
        df["type"]
        .astype(str)
        .str.strip()
        .str.lower()
    )

    # Remove rows with missing timestamps or sensor values.
    df = df.dropna(
        subset=["_time_s", "x_raw", "y_raw", "z_raw"]
    ).copy()

    accel_df = df.loc[df["_type"] == "accel"].copy()
    gyro_df = df.loc[df["_type"] == "gyro"].copy()

    if accel_df.empty:
        raise ValueError(
            "No accelerometer rows were found. "
            f"Available type values: {df['type'].unique()}"
        )

    if gyro_df.empty:
        raise ValueError(
            "No gyroscope rows were found. "
            f"Available type values: {df['type'].unique()}"
        )

    output = {
        "accel": {
            "time_s": accel_df["_time_s"].to_numpy(dtype=float),
            "x": (
                accel_df["x_raw"].to_numpy(dtype=float)
                * ACCEL_MS2_PER_LSB
            ),
            "y": (
                accel_df["y_raw"].to_numpy(dtype=float)
                * ACCEL_MS2_PER_LSB
            ),
            "z": (
                accel_df["z_raw"].to_numpy(dtype=float)
                * ACCEL_MS2_PER_LSB
            ),
            "unit": "m/s²",
        },
        "gyro": {
            "time_s": gyro_df["_time_s"].to_numpy(dtype=float),
            "x": (
                gyro_df["x_raw"].to_numpy(dtype=float)
                * GYRO_RADS_PER_LSB
            ),
            "y": (
                gyro_df["y_raw"].to_numpy(dtype=float)
                * GYRO_RADS_PER_LSB
            ),
            "z": (
                gyro_df["z_raw"].to_numpy(dtype=float)
                * GYRO_RADS_PER_LSB
            ),
            "unit": "rad/s",
        },
    }

    print(f"Loaded IMU data from: {csv_path}")
    print(f"  Accelerometer samples: {len(accel_df):,}")
    print(f"  Gyroscope samples:     {len(gyro_df):,}")

    return output


# ---------------------------------------------------------------------
# Plot helpers
# ---------------------------------------------------------------------

def downsample_indices(
    number_of_samples: int,
    max_points: int,
) -> np.ndarray:
    """
    Return evenly spaced indices for plotting large recordings.
    """
    if number_of_samples <= max_points:
        return np.arange(number_of_samples)

    return np.linspace(
        0,
        number_of_samples - 1,
        max_points,
        dtype=int,
    )


def print_signal_ranges(
    imu: dict[str, dict[str, np.ndarray]],
) -> None:
    """
    Print useful range and standard-deviation information.
    """
    for sensor_name in ["accel", "gyro"]:
        data = imu[sensor_name]
        unit = data["unit"]

        print(f"\n{sensor_name.capitalize()}:")

        for axis_name in ["x", "y", "z"]:
            values = np.asarray(data[axis_name], dtype=float)

            print(
                f"  {axis_name}: "
                f"min={np.nanmin(values): .4f}, "
                f"max={np.nanmax(values): .4f}, "
                f"mean={np.nanmean(values): .4f}, "
                f"std={np.nanstd(values): .4f} {unit}"
            )


# ---------------------------------------------------------------------
# Combined x/y/z plot
# ---------------------------------------------------------------------

def plot_combined_time_series(
    imu: dict[str, dict[str, np.ndarray]],
    output_path: Path,
    max_points: int = 100_000,
    center_signals: bool = False,
) -> None:
    """
    Plot accelerometer and gyroscope data, with x/y/z together.
    """
    datasets = [
        (
            "Accelerometer",
            imu["accel"],
            "Acceleration (m/s²)",
        ),
        (
            "Gyroscope",
            imu["gyro"],
            "Angular velocity (rad/s)",
        ),
    ]

    fig, axes = plt.subplots(
        2,
        1,
        figsize=(15, 9),
        sharex=True,
    )

    for axis_plot, (title, data, ylabel) in zip(axes, datasets):
        time_s = np.asarray(data["time_s"], dtype=float)
        indices = downsample_indices(len(time_s), max_points)

        time_plot = time_s[indices]

        for axis_name in ["x", "y", "z"]:
            values = np.asarray(data[axis_name], dtype=float)

            if center_signals:
                values = values - np.nanmean(values)

            axis_plot.plot(
                time_plot,
                values[indices],
                label=axis_name.upper(),
                linewidth=0.8,
            )

        if center_signals:
            title = f"{title} — mean removed"

        axis_plot.set_title(title)
        axis_plot.set_ylabel(ylabel)
        axis_plot.grid(True, alpha=0.3)
        axis_plot.legend(loc="upper right")

    axes[-1].set_xlabel("Time from recording start (s)")

    fig.suptitle("IMU time series", fontsize=15)
    fig.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180)
    plt.close(fig)

    print(f"Saved combined plot: {output_path}")


# ---------------------------------------------------------------------
# Separate axis plot
# ---------------------------------------------------------------------

def plot_separate_axes(
    imu: dict[str, dict[str, np.ndarray]],
    output_path: Path,
    max_points: int = 100_000,
    center_signals: bool = False,
) -> None:
    """
    Plot each accelerometer and gyroscope axis in its own subplot.

    This prevents the approximately 1-g accelerometer z offset from
    visually compressing the smaller x and y signals.
    """
    datasets = [
        (
            "Accelerometer",
            imu["accel"],
            "Acceleration (m/s²)",
        ),
        (
            "Gyroscope",
            imu["gyro"],
            "Angular velocity (rad/s)",
        ),
    ]

    fig, axes = plt.subplots(
        6,
        1,
        figsize=(15, 16),
        sharex=True,
    )

    plot_index = 0

    for sensor_title, data, ylabel in datasets:
        time_s = np.asarray(data["time_s"], dtype=float)
        indices = downsample_indices(len(time_s), max_points)
        time_plot = time_s[indices]

        for axis_name in ["x", "y", "z"]:
            axis_plot = axes[plot_index]
            values = np.asarray(data[axis_name], dtype=float)

            if center_signals:
                values = values - np.nanmean(values)

            axis_plot.plot(
                time_plot,
                values[indices],
                linewidth=0.8,
            )

            title = f"{sensor_title} {axis_name.upper()}"

            if center_signals:
                title += " — mean removed"

            axis_plot.set_title(title)
            axis_plot.set_ylabel(ylabel)
            axis_plot.grid(True, alpha=0.3)

            plot_index += 1

    axes[-1].set_xlabel("Time from recording start (s)")

    fig.suptitle("IMU time series by axis", fontsize=15)
    fig.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180)
    plt.close(fig)

    print(f"Saved separate-axis plot: {output_path}")


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plot LSM6DSO32 accelerometer and gyroscope "
            "time-series data in physical units."
        )
    )

    parser.add_argument(
        "csv",
        type=Path,
        help="Path to imu.csv",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Directory for output plots. By default, plots are "
            "saved beside the input CSV."
        ),
    )

    parser.add_argument(
        "--max-points",
        type=int,
        default=100_000,
        help=(
            "Maximum number of points drawn per signal. "
            "Default: 100000"
        ),
    )

    parser.add_argument(
        "--center",
        action="store_true",
        help=(
            "Subtract the mean from each axis before plotting. "
            "Useful for viewing dynamic motion."
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    if args.max_points <= 0:
        raise ValueError("--max-points must be greater than zero.")

    csv_path = args.csv.expanduser().resolve()

    if args.output_dir is None:
        output_directory = csv_path.parent
    else:
        output_directory = args.output_dir.expanduser().resolve()

    imu = load_imu_csv(csv_path)

    print_signal_ranges(imu)

    suffix = "_centered" if args.center else ""

    combined_output = (
        output_directory
        / f"imu_time_series{suffix}.png"
    )

    separate_output = (
        output_directory
        / f"imu_time_series_axes{suffix}.png"
    )

    plot_combined_time_series(
        imu=imu,
        output_path=combined_output,
        max_points=args.max_points,
        center_signals=args.center,
    )

    plot_separate_axes(
        imu=imu,
        output_path=separate_output,
        max_points=args.max_points,
        center_signals=args.center,
    )


if __name__ == "__main__":
    main()