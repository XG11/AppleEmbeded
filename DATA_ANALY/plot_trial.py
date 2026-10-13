#!/usr/bin/env python3

"""
Full-trial plotter: one subplot per channel, IMU, MMS101 F/T sensor,
and the RODE microphone.

Reads the files record_multimodal.py writes into one session directory
and plots every channel on its own subplot, all sharing one time axis
(every recorder uses the same host clock -- see recorder/shared.py's
monotonic_ns()/session_t0_ns):

    imu_loadcell.csv    (recorder/imu_loadcell.py)
        host_time_ns, host_elapsed_s, teensy_time_us, type,
        acc_x_raw, acc_y_raw, acc_z_raw,
        gyro_x_raw, gyro_y_raw, gyro_z_raw,
        load_cell_raw   (always empty -- ADS1220 load cell was removed)

        Only rows with type == "imu" are used here.

    force_torque.csv    (recorder/force_torque.py)
        host_time_ns, host_elapsed_s, device_time_us,
        Fx, Fy, Fz, Mx, My, Mz   (already tared and calibrated)

    rode_microphone.wav + rode_microphone_timestamps.csv
        (recorder/microphone.py)
        The timestamps CSV anchors the first audio block to
        host_elapsed_s; every later sample's time is derived from that
        anchor plus the WAV file's own sample rate.

IMU configuration (see src/firmware/main.cpp / lsm6dso32.cpp):
    Accelerometer: ±32 g
    Gyroscope:     ±2000 degrees/s

Output units:
    Accelerometer: m/s²
    Gyroscope:     rad/s
    Force:         N
    Moment:        N*m
    Microphone:    full-scale fraction (int16 PCM / 32768)

Install:

    python -m pip install numpy pandas matplotlib

Run, pointing at a recordings/session_* directory:

    python DATA_ANALY/plot_trial.py recordings/session_20261012_120000

Optional time range, downsampling, or saving a PNG instead of (or as
well as) showing the interactive window:

    python DATA_ANALY/plot_trial.py recordings/session_20261012_120000 \\
        --start 10 --end 15 --save trial.png
"""

import argparse
import math
import wave
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.gridspec import GridSpec


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
# Channel layout
#
# Each entry is one subplot: (key, column, scale, label, ylabel).
# Paired row-by-row so translational IMU sits next to force, and
# rotational IMU next to moment.
# ---------------------------------------------------------------------

IMU_CHANNELS = [
    ("acc_x", "acc_x_raw", ACCEL_MS2_PER_LSB, "Accel X", "m/s²"),
    ("acc_y", "acc_y_raw", ACCEL_MS2_PER_LSB, "Accel Y", "m/s²"),
    ("acc_z", "acc_z_raw", ACCEL_MS2_PER_LSB, "Accel Z", "m/s²"),
    ("gyro_x", "gyro_x_raw", GYRO_RADS_PER_LSB, "Gyro X", "rad/s"),
    ("gyro_y", "gyro_y_raw", GYRO_RADS_PER_LSB, "Gyro Y", "rad/s"),
    ("gyro_z", "gyro_z_raw", GYRO_RADS_PER_LSB, "Gyro Z", "rad/s"),
]

FT_CHANNELS = [
    ("Fx", "Fx", 1.0, "Force X", "N"),
    ("Fy", "Fy", 1.0, "Force Y", "N"),
    ("Fz", "Fz", 1.0, "Force Z", "N"),
    ("Mx", "Mx", 1.0, "Moment X", "N*m"),
    ("My", "My", 1.0, "Moment Y", "N*m"),
    ("Mz", "Mz", 1.0, "Moment Z", "N*m"),
]


# ---------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------

def parse_arguments():
    parser = argparse.ArgumentParser(
        description=(
            "Plot every IMU and force/torque channel from one "
            "recorded trial, one subplot per channel."
        )
    )

    parser.add_argument(
        "session_dir",
        type=Path,
        help=(
            "Session directory written by record_multimodal.py "
            "(contains imu_loadcell.csv and force_torque.csv)."
        ),
    )

    parser.add_argument(
        "--imu-csv",
        type=Path,
        default=None,
        help="Override path to imu_loadcell.csv.",
    )

    parser.add_argument(
        "--ft-csv",
        type=Path,
        default=None,
        help="Override path to force_torque.csv.",
    )

    parser.add_argument(
        "--mic-wav",
        type=Path,
        default=None,
        help="Override path to rode_microphone.wav.",
    )

    parser.add_argument(
        "--mic-timestamps",
        type=Path,
        default=None,
        help="Override path to rode_microphone_timestamps.csv.",
    )

    parser.add_argument(
        "--start",
        type=float,
        default=None,
        help="Optional plot start time in seconds, using host_elapsed_s.",
    )

    parser.add_argument(
        "--end",
        type=float,
        default=None,
        help="Optional plot end time in seconds, using host_elapsed_s.",
    )

    parser.add_argument(
        "--max-points",
        type=int,
        default=200_000,
        help=(
            "Maximum plotted points per channel. Data is downsampled "
            "only for display. Default: 200000"
        ),
    )

    parser.add_argument(
        "--save",
        type=Path,
        default=None,
        help=(
            "Save the figure to this path instead of opening an "
            "interactive window."
        ),
    )

    args = parser.parse_args()

    if (
        args.start is not None
        and args.end is not None
        and args.start >= args.end
    ):
        parser.error("--start must be smaller than --end.")

    return args


# ---------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------

def load_imu_csv(csv_path: Path) -> dict:
    df = pd.read_csv(csv_path)

    if df.empty:
        raise ValueError(f"CSV file is empty: {csv_path}")

    required_columns = {"host_elapsed_s", "type"} | {
        raw_column for _, raw_column, _, _, _ in IMU_CHANNELS
    }

    missing_columns = required_columns.difference(df.columns)

    if missing_columns:
        raise ValueError(
            f"{csv_path} is missing required columns: "
            + ", ".join(sorted(missing_columns))
        )

    imu_df = df.loc[
        df["type"].astype(str).str.strip().str.lower() == "imu"
    ].copy()

    numeric_columns = ["host_elapsed_s"] + [
        raw_column for _, raw_column, _, _, _ in IMU_CHANNELS
    ]

    for column in numeric_columns:
        imu_df[column] = pd.to_numeric(imu_df[column], errors="coerce")

    imu_df = imu_df.dropna(subset=numeric_columns)

    valid_mask = np.ones(len(imu_df), dtype=bool)

    for _, raw_column, _, _, _ in IMU_CHANNELS:
        valid_mask &= imu_df[raw_column].between(-32768, 32767).to_numpy()

    imu_df = imu_df.loc[valid_mask].sort_values("host_elapsed_s")

    if imu_df.empty:
        raise ValueError(f"No valid IMU packets remained in {csv_path}.")

    time_s = imu_df["host_elapsed_s"].to_numpy(dtype=float)

    channels = {
        key: imu_df[raw_column].to_numpy(dtype=float) * scale
        for key, raw_column, scale, _, _ in IMU_CHANNELS
    }

    print(f"IMU:          {len(imu_df):,} valid samples from {csv_path}")

    return {"time_s": time_s, **channels}


def load_force_torque_csv(csv_path: Path) -> dict:
    df = pd.read_csv(csv_path)

    if df.empty:
        raise ValueError(f"CSV file is empty: {csv_path}")

    required_columns = {"host_elapsed_s"} | {
        raw_column for _, raw_column, _, _, _ in FT_CHANNELS
    }

    missing_columns = required_columns.difference(df.columns)

    if missing_columns:
        raise ValueError(
            f"{csv_path} is missing required columns: "
            + ", ".join(sorted(missing_columns))
        )

    numeric_columns = ["host_elapsed_s"] + [
        raw_column for _, raw_column, _, _, _ in FT_CHANNELS
    ]

    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = df.dropna(subset=numeric_columns).sort_values("host_elapsed_s")

    if df.empty:
        raise ValueError(f"No valid force/torque samples remained in {csv_path}.")

    time_s = df["host_elapsed_s"].to_numpy(dtype=float)

    channels = {
        key: df[raw_column].to_numpy(dtype=float) * scale
        for key, raw_column, scale, _, _ in FT_CHANNELS
    }

    print(f"Force/torque: {len(df):,} valid samples from {csv_path}")

    return {"time_s": time_s, **channels}


def load_microphone_audio(wav_path: Path, timestamps_csv_path: Path) -> dict:
    timestamps_df = pd.read_csv(timestamps_csv_path)

    if timestamps_df.empty:
        raise ValueError(f"CSV file is empty: {timestamps_csv_path}")

    first_block_elapsed_s = float(timestamps_df["host_elapsed_s"].iloc[0])

    with wave.open(str(wav_path), "rb") as wav_file:
        channel_count = wav_file.getnchannels()
        sample_rate = wav_file.getframerate()
        frame_count = wav_file.getnframes()
        raw_bytes = wav_file.readframes(frame_count)

    samples = np.frombuffer(raw_bytes, dtype=np.int16)
    samples = samples.reshape(-1, channel_count).astype(np.float32) / 32768.0

    time_s = first_block_elapsed_s + np.arange(frame_count) / sample_rate

    print(
        f"Microphone:   {frame_count:,} samples from {wav_path} "
        f"({channel_count} channel(s) at {sample_rate} Hz)"
    )

    return {
        "time_s": time_s,
        "channels": [samples[:, i] for i in range(channel_count)],
    }


# ---------------------------------------------------------------------
# Plot helpers
# ---------------------------------------------------------------------

def downsample_signal(time_s, values, max_points):
    time_s = np.asarray(time_s, dtype=float)
    values = np.asarray(values, dtype=float)

    if max_points is None or max_points <= 0 or len(time_s) <= max_points:
        return time_s, values

    step = int(math.ceil(len(time_s) / max_points))

    return time_s[::step], values[::step]


def apply_time_range(time_s, values, start_s, end_s):
    time_s = np.asarray(time_s, dtype=float)
    values = np.asarray(values, dtype=float)

    valid = np.isfinite(time_s) & np.isfinite(values)

    if start_s is not None:
        valid &= time_s >= start_s

    if end_s is not None:
        valid &= time_s <= end_s

    return time_s[valid], values[valid]


def prepare_signal(time_s, values, start_s, end_s, max_points):
    plot_time, plot_values = apply_time_range(time_s, values, start_s, end_s)
    return downsample_signal(plot_time, plot_values, max_points)


# ---------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------

def plot_trial(
    imu_data,
    ft_data,
    mic_data,
    start_s=None,
    end_s=None,
    max_points=200_000,
    save_path=None,
):
    fig = plt.figure(figsize=(16, 20))
    gs = GridSpec(7, 2, figure=fig, height_ratios=[1] * 6 + [1.3])

    grid_axes = []
    ref_axis = None

    for row in range(6):
        row_axes = []
        for col in range(2):
            axis = fig.add_subplot(gs[row, col], sharex=ref_axis)
            if ref_axis is None:
                ref_axis = axis
            row_axes.append(axis)
        grid_axes.append(row_axes)

    mic_axis = fig.add_subplot(gs[6, :], sharex=ref_axis)

    for row, (
        (imu_key, _, _, imu_label, imu_unit),
        (ft_key, _, _, ft_label, ft_unit),
    ) in enumerate(zip(IMU_CHANNELS, FT_CHANNELS)):

        imu_axis, ft_axis = grid_axes[row]

        imu_time, imu_values = prepare_signal(
            imu_data["time_s"],
            imu_data[imu_key],
            start_s,
            end_s,
            max_points,
        )

        imu_axis.plot(imu_time, imu_values, linewidth=0.8)
        imu_axis.set_title(imu_label, fontsize=10)
        imu_axis.set_ylabel(imu_unit)
        imu_axis.grid(True, alpha=0.3)

        ft_time, ft_values = prepare_signal(
            ft_data["time_s"],
            ft_data[ft_key],
            start_s,
            end_s,
            max_points,
        )

        ft_axis.plot(ft_time, ft_values, linewidth=0.8, color="tab:red")
        ft_axis.set_title(ft_label, fontsize=10)
        ft_axis.set_ylabel(ft_unit)
        ft_axis.grid(True, alpha=0.3)

    grid_axes[-1][0].set_xlabel("Elapsed time (s)")
    grid_axes[-1][1].set_xlabel("Elapsed time (s)")

    for channel_index, channel_values in enumerate(mic_data["channels"]):
        mic_time, mic_values = prepare_signal(
            mic_data["time_s"],
            channel_values,
            start_s,
            end_s,
            max_points,
        )

        mic_axis.plot(
            mic_time,
            mic_values,
            linewidth=0.5,
            label=f"channel {channel_index}",
        )

    mic_axis.set_title("Microphone", fontsize=10)
    mic_axis.set_ylabel("Amplitude (full scale)")
    mic_axis.set_xlabel("Elapsed time (s)")
    mic_axis.grid(True, alpha=0.3)

    if len(mic_data["channels"]) > 1:
        mic_axis.legend(loc="upper right")

    all_axes = [axis for row_axes in grid_axes for axis in row_axes] + [mic_axis]

    fig.suptitle(
        "Trial overview -- IMU (left), force/torque (right), microphone (bottom)",
        fontsize=15,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.97])

    if save_path is not None:
        fig.savefig(save_path, dpi=150)
        print(f"Saved: {save_path}")
        return

    # -------------------------------------------------------------
    # Keyboard interactions.
    # -------------------------------------------------------------

    original_limits = {
        "x": ref_axis.get_xlim(),
        "y": [axis.get_ylim() for axis in all_axes],
    }

    def on_key_press(event):
        if event.key == "r":
            for axis in all_axes:
                axis.set_xlim(original_limits["x"])

            for axis, limits in zip(all_axes, original_limits["y"]):
                axis.set_ylim(limits)

            fig.canvas.draw_idle()

        elif event.key == "g":
            for axis in all_axes:
                axis.grid(
                    not axis.xaxis._major_tick_kw.get("gridOn", False),
                    alpha=0.3,
                )

            fig.canvas.draw_idle()

    fig.canvas.mpl_connect("key_press_event", on_key_press)

    print()
    print("Interactive plot controls")
    print("-" * 60)
    print("Magnifying glass: drag to zoom")
    print("Hand tool:         drag to pan")
    print("Home button:       reset view")
    print("Keyboard r:        reset view")
    print("Keyboard g:        toggle grid")
    print()

    plt.show()


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    args = parse_arguments()

    session_dir = args.session_dir.expanduser().resolve()

    imu_csv_path = (
        args.imu_csv.expanduser().resolve()
        if args.imu_csv is not None
        else session_dir / "imu_loadcell.csv"
    )

    ft_csv_path = (
        args.ft_csv.expanduser().resolve()
        if args.ft_csv is not None
        else session_dir / "force_torque.csv"
    )

    mic_wav_path = (
        args.mic_wav.expanduser().resolve()
        if args.mic_wav is not None
        else session_dir / "rode_microphone.wav"
    )

    mic_timestamps_path = (
        args.mic_timestamps.expanduser().resolve()
        if args.mic_timestamps is not None
        else session_dir / "rode_microphone_timestamps.csv"
    )

    if not imu_csv_path.exists():
        raise FileNotFoundError(f"IMU CSV does not exist: {imu_csv_path}")

    if not ft_csv_path.exists():
        raise FileNotFoundError(f"Force/torque CSV does not exist: {ft_csv_path}")

    if not mic_wav_path.exists():
        raise FileNotFoundError(f"Microphone WAV does not exist: {mic_wav_path}")

    if not mic_timestamps_path.exists():
        raise FileNotFoundError(
            f"Microphone timestamps CSV does not exist: {mic_timestamps_path}"
        )

    imu_data = load_imu_csv(imu_csv_path)
    ft_data = load_force_torque_csv(ft_csv_path)
    mic_data = load_microphone_audio(mic_wav_path, mic_timestamps_path)

    plot_trial(
        imu_data,
        ft_data,
        mic_data,
        start_s=args.start,
        end_s=args.end,
        max_points=args.max_points,
        save_path=args.save,
    )


if __name__ == "__main__":
    main()
