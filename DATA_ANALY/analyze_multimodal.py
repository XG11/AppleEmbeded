#!/usr/bin/env python3

"""
analyze_multimodal.py

First-pass analysis for synchronized GelSight, IMU, and microphone recordings.

Example:
    python analyze_multimodal.py recordings/session_20260714_162302

Outputs:
    analysis/
        synchronization_overview.png
        imu_time_series.png
        imu_sampling_diagnostics.png
        imu_spectrum.png
        audio_overview.png
        audio_spectrogram.png
        gelsight_motion.png
        gelsight_event_frames/
        summary.txt

Dependencies:
    pip install numpy pandas matplotlib scipy opencv-python
"""

import argparse
import json
import math
import os
import re
import warnings
from pathlib import Path

import cv2
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import signal
from scipy.io import wavfile


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

TIMESTAMP_KEYWORDS = [
    "timestamp_ns",
    "time_ns",
    "host_time_ns",
    "perf_counter_ns",
    "timestamp_us",
    "time_us",
    "timestamp_ms",
    "time_ms",
    "timestamp",
    "time",
    "t",
]

ACCEL_KEYWORDS = {
    "x": ["accel_x", "acc_x", "ax", "acceleration_x"],
    "y": ["accel_y", "acc_y", "ay", "acceleration_y"],
    "z": ["accel_z", "acc_z", "az", "acceleration_z"],
}

GYRO_KEYWORDS = {
    "x": ["gyro_x", "gyr_x", "gx", "angular_velocity_x"],
    "y": ["gyro_y", "gyr_y", "gy", "angular_velocity_y"],
    "z": ["gyro_z", "gyr_z", "gz", "angular_velocity_z"],
}


# ---------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------

def normalize_name(name):
    """Normalize a column or filename for comparison."""
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def find_file(session_dir, extensions=None, keywords=None, exclude_keywords=None):
    """
    Find the most likely file using filename keywords and extensions.
    """
    extensions = extensions or []
    keywords = keywords or []
    exclude_keywords = exclude_keywords or []

    candidates = []

    for path in session_dir.iterdir():
        if not path.is_file():
            continue

        name = path.name.lower()

        if extensions and path.suffix.lower() not in extensions:
            continue

        if any(word.lower() in name for word in exclude_keywords):
            continue

        score = sum(word.lower() in name for word in keywords)
        candidates.append((score, path))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    return candidates[0][1]


def find_csv(session_dir, include_keywords, exclude_keywords=None):
    return find_file(
        session_dir,
        extensions=[".csv"],
        keywords=include_keywords,
        exclude_keywords=exclude_keywords or [],
    )


def detect_timestamp_column(df):
    """
    Detect the timestamp column in a DataFrame.
    """
    normalized = {normalize_name(column): column for column in df.columns}

    for candidate in TIMESTAMP_KEYWORDS:
        if candidate in normalized:
            return normalized[candidate]

    for column in df.columns:
        name = normalize_name(column)
        if "timestamp" in name:
            return column

    for column in df.columns:
        name = normalize_name(column)
        if name.startswith("time"):
            return column

    raise ValueError(
        "Could not detect a timestamp column. Available columns: "
        + ", ".join(map(str, df.columns))
    )


def timestamp_to_seconds(values, column_name="timestamp"):
    """
    Convert a timestamp array to seconds.

    The unit is inferred from the column name and approximate magnitude.
    """
    values = pd.to_numeric(pd.Series(values), errors="coerce").to_numpy(dtype=float)

    valid = values[np.isfinite(values)]
    if len(valid) == 0:
        raise ValueError(f"No valid timestamps found in column {column_name}")

    name = normalize_name(column_name)
    median_value = np.nanmedian(np.abs(valid))

    if name.endswith("_ns") or "nanosecond" in name:
        scale = 1e-9
    elif name.endswith("_us") or "microsecond" in name:
        scale = 1e-6
    elif name.endswith("_ms") or "millisecond" in name:
        scale = 1e-3
    else:
        # Magnitude-based fallback.
        if median_value > 1e16:
            scale = 1e-9
        elif median_value > 1e13:
            scale = 1e-6
        elif median_value > 1e10:
            # Could be epoch milliseconds or a long-running nanosecond clock.
            differences = np.diff(valid[: min(len(valid), 10000)])
            median_difference = np.nanmedian(np.abs(differences[differences != 0]))

            if np.isfinite(median_difference) and median_difference > 1e4:
                scale = 1e-9
            else:
                scale = 1e-3
        else:
            scale = 1.0

    return values * scale


def estimate_sampling_statistics(time_s):
    """
    Estimate sampling interval, rate, jitter, and dropped-sample candidates.
    """
    time_s = np.asarray(time_s, dtype=float)
    time_s = time_s[np.isfinite(time_s)]

    if len(time_s) < 2:
        return {
            "count": len(time_s),
            "duration_s": np.nan,
            "median_dt_s": np.nan,
            "mean_rate_hz": np.nan,
            "median_rate_hz": np.nan,
            "jitter_std_us": np.nan,
            "large_gap_count": 0,
        }

    dt = np.diff(time_s)
    positive_dt = dt[dt > 0]

    if len(positive_dt) == 0:
        return {
            "count": len(time_s),
            "duration_s": time_s[-1] - time_s[0],
            "median_dt_s": np.nan,
            "mean_rate_hz": np.nan,
            "median_rate_hz": np.nan,
            "jitter_std_us": np.nan,
            "large_gap_count": 0,
        }

    median_dt = np.median(positive_dt)
    duration = time_s[-1] - time_s[0]

    return {
        "count": len(time_s),
        "duration_s": duration,
        "median_dt_s": median_dt,
        "mean_rate_hz": (len(time_s) - 1) / duration if duration > 0 else np.nan,
        "median_rate_hz": 1.0 / median_dt if median_dt > 0 else np.nan,
        "jitter_std_us": np.std(positive_dt - median_dt) * 1e6,
        "large_gap_count": int(np.sum(positive_dt > 1.5 * median_dt)),
    }


def find_matching_column(df, keywords):
    """
    Find a DataFrame column matching one of the provided names.
    """
    normalized_columns = {
        normalize_name(column): column
        for column in df.columns
    }

    for keyword in keywords:
        normalized_keyword = normalize_name(keyword)
        if normalized_keyword in normalized_columns:
            return normalized_columns[normalized_keyword]

    return None


def make_relative_time(time_s, reference_s):
    return np.asarray(time_s, dtype=float) - reference_s


def safe_normalize(values):
    values = np.asarray(values, dtype=float)
    valid = np.isfinite(values)

    result = np.full_like(values, np.nan, dtype=float)

    if np.sum(valid) < 2:
        return result

    low = np.nanpercentile(values[valid], 1)
    high = np.nanpercentile(values[valid], 99)

    if high <= low:
        result[valid] = 0
    else:
        result[valid] = (values[valid] - low) / (high - low)

    return np.clip(result, 0, 1)


def downsample_for_plot(time_s, values, max_points=100000):
    """
    Downsample a signal only for plotting.
    """
    time_s = np.asarray(time_s)
    values = np.asarray(values)

    if len(time_s) <= max_points:
        return time_s, values

    step = int(math.ceil(len(time_s) / max_points))
    return time_s[::step], values[::step]


# ---------------------------------------------------------------------
# IMU loading and analysis
# ---------------------------------------------------------------------

def load_imu_csv(path):
    """
    Load the Teensy IMU CSV format:

    host_time_ns,host_elapsed_s,teensy_time_us,type,
    x_raw,y_raw,z_raw,fifo_remaining
    """
    df = pd.read_csv(path)

    if df.empty:
        raise ValueError(f"IMU file is empty: {path}")

    required_columns = {
        "host_time_ns",
        "host_elapsed_s",
        "teensy_time_us",
        "type",
        "x_raw",
        "y_raw",
        "z_raw",
    }

    missing_columns = required_columns.difference(df.columns)

    if missing_columns:
        raise ValueError(
            f"Missing required IMU columns: {sorted(missing_columns)}\n"
            f"Available columns: {list(df.columns)}"
        )

    # host_time_ns is the absolute host perf_counter timestamp.
    # It is the best timestamp for synchronizing with GelSight and audio,
    # provided those recorders also use time.perf_counter_ns().
    df["_time_s"] = pd.to_numeric(
        df["host_time_ns"],
        errors="coerce",
    ) * 1e-9

    # Convert raw sensor values to numeric.
    for column in ["x_raw", "y_raw", "z_raw"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    type_values = (
        df["type"]
        .astype(str)
        .str.strip()
        .str.lower()
    )

    accel_df = df.loc[type_values == "accel"].copy()
    gyro_df = df.loc[type_values == "gyro"].copy()

    if accel_df.empty:
        raise ValueError(
            "No accelerometer rows were found. "
            f"Type values: {df['type'].unique()}"
        )

    if gyro_df.empty:
        raise ValueError(
            "No gyroscope rows were found. "
            f"Type values: {df['type'].unique()}"
        )

    output = {
        "raw": df,
        "timestamp_column": "host_time_ns",
        "time_s": df["_time_s"].to_numpy(dtype=float),

        "accel": {
            "time_s": accel_df["_time_s"].to_numpy(dtype=float),
            "x": accel_df["x_raw"].to_numpy(dtype=float),
            "y": accel_df["y_raw"].to_numpy(dtype=float),
            "z": accel_df["z_raw"].to_numpy(dtype=float),
        },

        "gyro": {
            "time_s": gyro_df["_time_s"].to_numpy(dtype=float),
            "x": gyro_df["x_raw"].to_numpy(dtype=float),
            "y": gyro_df["y_raw"].to_numpy(dtype=float),
            "z": gyro_df["z_raw"].to_numpy(dtype=float),
        },
    }

    print(
        f"Loaded {len(accel_df)} accelerometer samples and "
        f"{len(gyro_df)} gyroscope samples."
    )

    return output

def add_vector_magnitude(data):
    if data is None:
        return

    data["magnitude"] = np.sqrt(
        np.asarray(data["x"], dtype=float) ** 2
        + np.asarray(data["y"], dtype=float) ** 2
        + np.asarray(data["z"], dtype=float) ** 2
    )


def compute_welch_spectrum(values, sampling_rate_hz):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]

    if len(values) < 16 or not np.isfinite(sampling_rate_hz):
        return np.array([]), np.array([])

    values = signal.detrend(values)

    nperseg = min(4096, len(values))
    frequency, power = signal.welch(
        values,
        fs=sampling_rate_hz,
        nperseg=nperseg,
        scaling="density",
    )

    return frequency, power


# ---------------------------------------------------------------------
# Audio loading and analysis
# ---------------------------------------------------------------------

def load_audio(wav_path, timestamp_csv=None):
    sample_rate, audio = wavfile.read(wav_path)

    if audio.ndim == 2:
        audio = np.mean(audio.astype(np.float64), axis=1)
    else:
        audio = audio.astype(np.float64)

    # Normalize integer PCM into approximately [-1, 1].
    original_dtype = wavfile.read(wav_path, mmap=True)[1].dtype

    if np.issubdtype(original_dtype, np.integer):
        max_value = max(
            abs(np.iinfo(original_dtype).min),
            np.iinfo(original_dtype).max,
        )
        audio = audio / max_value

    start_time_s = None
    timestamp_df = None

    if timestamp_csv is not None and timestamp_csv.exists():
        timestamp_df = pd.read_csv(timestamp_csv)

        if not timestamp_df.empty:
            timestamp_column = detect_timestamp_column(timestamp_df)
            timestamp_values_s = timestamp_to_seconds(
                timestamp_df[timestamp_column],
                timestamp_column,
            )

            valid = timestamp_values_s[np.isfinite(timestamp_values_s)]

            if len(valid) > 0:
                start_time_s = valid[0]

    return {
        "sample_rate": sample_rate,
        "samples": audio,
        "duration_s": len(audio) / sample_rate,
        "start_time_s": start_time_s,
        "timestamp_df": timestamp_df,
    }


def compute_audio_envelope(audio, sample_rate, output_rate_hz=200):
    """
    Calculate RMS audio envelope at a lower rate.
    """
    audio = np.asarray(audio, dtype=float)

    window_samples = max(1, int(sample_rate / output_rate_hz))
    count = len(audio) // window_samples

    if count == 0:
        return np.array([]), np.array([])

    trimmed = audio[: count * window_samples]
    blocks = trimmed.reshape(count, window_samples)

    rms = np.sqrt(np.mean(blocks ** 2, axis=1))
    time_s = (
        np.arange(count) * window_samples + window_samples / 2
    ) / sample_rate

    return time_s, rms


# ---------------------------------------------------------------------
# GelSight loading and analysis
# ---------------------------------------------------------------------

def load_gelsight_timestamps(timestamp_path):
    df = pd.read_csv(timestamp_path)

    if df.empty:
        raise ValueError(f"GelSight timestamp file is empty: {timestamp_path}")

    timestamp_column = detect_timestamp_column(df)
    time_s = timestamp_to_seconds(df[timestamp_column], timestamp_column)

    frame_column = None

    for column in df.columns:
        if normalize_name(column) in {
            "frame",
            "frame_id",
            "frame_index",
            "frame_number",
        }:
            frame_column = column
            break

    if frame_column is None:
        frame_indices = np.arange(len(df))
    else:
        frame_indices = pd.to_numeric(
            df[frame_column],
            errors="coerce",
        ).fillna(pd.Series(np.arange(len(df)))).astype(int).to_numpy()

    return {
        "raw": df,
        "timestamp_column": timestamp_column,
        "time_s": time_s,
        "frame_indices": frame_indices,
    }


def analyze_gelsight_motion(
    video_path,
    frame_times_s,
    resize_width=320,
    max_frames=None,
):
    """
    Compute a frame-to-frame visual motion score.

    This is not yet a tactile deformation reconstruction. It is a useful
    first-pass indicator for when visible contact or marker motion occurs.
    """
    capture = cv2.VideoCapture(str(video_path))

    if not capture.isOpened():
        raise RuntimeError(f"Could not open GelSight video: {video_path}")

    video_frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    video_fps = capture.get(cv2.CAP_PROP_FPS)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))

    usable_frames = min(video_frame_count, len(frame_times_s))

    if max_frames is not None:
        usable_frames = min(usable_frames, max_frames)

    motion_scores = []
    valid_times = []
    previous_gray = None

    for frame_index in range(usable_frames):
        success, frame = capture.read()

        if not success:
            break

        if resize_width and frame.shape[1] > resize_width:
            scale = resize_width / frame.shape[1]
            resized_height = int(frame.shape[0] * scale)
            frame = cv2.resize(
                frame,
                (resize_width, resized_height),
                interpolation=cv2.INTER_AREA,
            )

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)

        if previous_gray is None:
            motion = 0.0
        else:
            difference = cv2.absdiff(gray, previous_gray)
            motion = float(np.mean(difference))

        motion_scores.append(motion)
        valid_times.append(frame_times_s[frame_index])
        previous_gray = gray

    capture.release()

    return {
        "time_s": np.asarray(valid_times),
        "motion": np.asarray(motion_scores),
        "video_frame_count": video_frame_count,
        "processed_frame_count": len(motion_scores),
        "fps_reported": video_fps,
        "width": width,
        "height": height,
    }


def save_event_frames(
    video_path,
    frame_times_relative_s,
    event_times_relative_s,
    output_dir,
    offsets_s=(-0.2, 0.0, 0.2),
):
    """
    Save GelSight frames before, at, and after selected events.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    capture = cv2.VideoCapture(str(video_path))

    if not capture.isOpened():
        warnings.warn(f"Could not reopen video for event frames: {video_path}")
        return

    frame_times_relative_s = np.asarray(frame_times_relative_s)

    for event_number, event_time in enumerate(event_times_relative_s, start=1):
        for offset in offsets_s:
            target_time = event_time + offset

            if len(frame_times_relative_s) == 0:
                continue

            frame_index = int(
                np.argmin(np.abs(frame_times_relative_s - target_time))
            )

            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            success, frame = capture.read()

            if not success:
                continue

            label = (
                f"event={event_number}, "
                f"target={target_time:.3f}s, "
                f"frame={frame_index}"
            )

            cv2.putText(
                frame,
                label,
                (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

            offset_label = f"{offset:+.3f}".replace("+", "p").replace("-", "m")
            filename = (
                f"event_{event_number:02d}_"
                f"time_{event_time:.3f}s_"
                f"offset_{offset_label}s.png"
            )

            cv2.imwrite(str(output_dir / filename), frame)

    capture.release()


# ---------------------------------------------------------------------
# Event detection
# ---------------------------------------------------------------------

def detect_peaks_from_signal(
    time_s,
    values,
    minimum_distance_s=0.25,
    prominence_fraction=0.15,
    maximum_events=20,
):
    """
    Detect large transient events using scipy.signal.find_peaks.
    """
    time_s = np.asarray(time_s, dtype=float)
    values = np.asarray(values, dtype=float)

    valid = np.isfinite(time_s) & np.isfinite(values)
    time_s = time_s[valid]
    values = values[valid]

    if len(values) < 10:
        return np.array([]), np.array([])

    median_dt = np.median(np.diff(time_s))

    if median_dt <= 0:
        return np.array([]), np.array([])

    distance_samples = max(1, int(minimum_distance_s / median_dt))

    value_range = np.percentile(values, 99) - np.percentile(values, 10)
    prominence = max(1e-12, prominence_fraction * value_range)

    peak_indices, properties = signal.find_peaks(
        values,
        distance=distance_samples,
        prominence=prominence,
    )

    if len(peak_indices) == 0:
        return np.array([]), np.array([])

    prominences = properties["prominences"]
    order = np.argsort(prominences)[::-1]
    order = order[:maximum_events]

    selected_indices = peak_indices[order]
    selected_indices = selected_indices[
        np.argsort(time_s[selected_indices])
    ]

    return time_s[selected_indices], values[selected_indices]


# ---------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------

def plot_imu_time_series(imu, reference_s, output_path):
    available = []

    if imu["accel"] is not None:
        available.append(("Accelerometer", imu["accel"]))

    if imu["gyro"] is not None:
        available.append(("Gyroscope", imu["gyro"]))

    if not available:
        return

    fig, axes = plt.subplots(
        len(available),
        1,
        figsize=(14, 4 * len(available)),
        sharex=True,
    )

    if len(available) == 1:
        axes = [axes]

    for axis_plot, (title, data) in zip(axes, available):
        time_relative = make_relative_time(data["time_s"], reference_s)

        axis_plot.plot(time_relative, data["x"], label="x", linewidth=0.8)
        axis_plot.plot(time_relative, data["y"], label="y", linewidth=0.8)
        axis_plot.plot(time_relative, data["z"], label="z", linewidth=0.8)

        axis_plot.set_title(title)
        axis_plot.set_ylabel("Sensor value")
        axis_plot.grid(True, alpha=0.3)
        axis_plot.legend()

    axes[-1].set_xlabel("Time from shared reference (s)")

    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def plot_imu_sampling_diagnostics(imu, output_path):
    datasets = []

    if imu["accel"] is not None:
        datasets.append(("Accelerometer", imu["accel"]["time_s"]))

    if imu["gyro"] is not None:
        datasets.append(("Gyroscope", imu["gyro"]["time_s"]))

    if not datasets:
        return

    fig, axes = plt.subplots(
        len(datasets),
        1,
        figsize=(14, 4 * len(datasets)),
    )

    if len(datasets) == 1:
        axes = [axes]

    for axis_plot, (name, time_s) in zip(axes, datasets):
        dt = np.diff(time_s) * 1e6
        sample_indices = np.arange(1, len(time_s))

        sample_indices, dt = downsample_for_plot(
            sample_indices,
            dt,
            max_points=100000,
        )

        axis_plot.plot(sample_indices, dt, linewidth=0.7)
        axis_plot.set_title(f"{name} inter-sample timing")
        axis_plot.set_xlabel("Sample index")
        axis_plot.set_ylabel("Δt (µs)")
        axis_plot.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def plot_imu_spectrum(imu, output_path):
    spectra = []

    for sensor_name in ["accel", "gyro"]:
        data = imu.get(sensor_name)

        if data is None:
            continue

        statistics = estimate_sampling_statistics(data["time_s"])
        sampling_rate = statistics["median_rate_hz"]

        for axis_name in ["x", "y", "z", "magnitude"]:
            frequency, power = compute_welch_spectrum(
                data[axis_name],
                sampling_rate,
            )

            if len(frequency):
                spectra.append(
                    (
                        f"{sensor_name} {axis_name}",
                        frequency,
                        power,
                    )
                )

    if not spectra:
        return

    fig, axis_plot = plt.subplots(figsize=(14, 7))

    for label, frequency, power in spectra:
        valid = frequency > 0

        axis_plot.semilogy(
            frequency[valid],
            power[valid],
            label=label,
            linewidth=1,
        )

    axis_plot.set_title("IMU power spectral density")
    axis_plot.set_xlabel("Frequency (Hz)")
    axis_plot.set_ylabel("Power spectral density")
    axis_plot.grid(True, alpha=0.3)
    axis_plot.legend(ncol=2)

    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def plot_audio(audio, reference_s, output_path):
    sample_rate = audio["sample_rate"]
    samples = audio["samples"]

    if audio["start_time_s"] is None:
        absolute_start = reference_s
    else:
        absolute_start = audio["start_time_s"]

    relative_start = absolute_start - reference_s

    waveform_time = np.arange(len(samples)) / sample_rate + relative_start
    plot_time, plot_samples = downsample_for_plot(
        waveform_time,
        samples,
        max_points=200000,
    )

    envelope_time, envelope = compute_audio_envelope(
        samples,
        sample_rate,
    )
    envelope_time = envelope_time + relative_start

    frequency, power = compute_welch_spectrum(samples, sample_rate)

    fig, axes = plt.subplots(3, 1, figsize=(14, 11))

    axes[0].plot(plot_time, plot_samples, linewidth=0.5)
    axes[0].set_title("Audio waveform")
    axes[0].set_xlabel("Time from shared reference (s)")
    axes[0].set_ylabel("Amplitude")
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(envelope_time, envelope, linewidth=1)
    axes[1].set_title("Audio RMS envelope")
    axes[1].set_xlabel("Time from shared reference (s)")
    axes[1].set_ylabel("RMS amplitude")
    axes[1].grid(True, alpha=0.3)

    valid = frequency > 0
    axes[2].semilogy(frequency[valid], power[valid], linewidth=1)
    axes[2].set_title("Audio power spectral density")
    axes[2].set_xlabel("Frequency (Hz)")
    axes[2].set_ylabel("Power spectral density")
    axes[2].grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def plot_audio_spectrogram(audio, reference_s, output_path):
    sample_rate = audio["sample_rate"]
    samples = audio["samples"]

    if audio["start_time_s"] is None:
        relative_start = 0.0
    else:
        relative_start = audio["start_time_s"] - reference_s

    nperseg = min(2048, len(samples))
    noverlap = int(0.75 * nperseg)

    frequency, time_local, spectrum = signal.spectrogram(
        samples,
        fs=sample_rate,
        nperseg=nperseg,
        noverlap=noverlap,
        scaling="density",
        mode="psd",
    )

    spectrum_db = 10 * np.log10(spectrum + 1e-15)
    time_relative = time_local + relative_start

    fig, axis_plot = plt.subplots(figsize=(14, 6))

    image = axis_plot.pcolormesh(
        time_relative,
        frequency,
        spectrum_db,
        shading="auto",
    )

    axis_plot.set_title("Audio spectrogram")
    axis_plot.set_xlabel("Time from shared reference (s)")
    axis_plot.set_ylabel("Frequency (Hz)")
    axis_plot.set_ylim(0, min(sample_rate / 2, 12000))

    colorbar = fig.colorbar(image, ax=axis_plot)
    colorbar.set_label("Power (dB)")

    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def plot_gelsight_motion(gelsight_motion, reference_s, output_path):
    time_relative = make_relative_time(
        gelsight_motion["time_s"],
        reference_s,
    )

    fig, axis_plot = plt.subplots(figsize=(14, 5))

    axis_plot.plot(
        time_relative,
        gelsight_motion["motion"],
        linewidth=1,
    )

    axis_plot.set_title("GelSight frame-to-frame visual motion")
    axis_plot.set_xlabel("Time from shared reference (s)")
    axis_plot.set_ylabel("Mean absolute pixel difference")
    axis_plot.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def plot_synchronization_overview(
    imu,
    audio,
    gelsight_motion,
    reference_s,
    output_path,
):
    rows = []

    if imu["accel"] is not None:
        rows.append(("Accelerometer magnitude", "accel"))

    if imu["gyro"] is not None:
        rows.append(("Gyroscope magnitude", "gyro"))

    rows.append(("Audio RMS envelope", "audio"))
    rows.append(("GelSight visual motion", "gelsight"))

    fig, axes = plt.subplots(
        len(rows),
        1,
        figsize=(15, 3 * len(rows)),
        sharex=True,
    )

    if len(rows) == 1:
        axes = [axes]

    for axis_plot, (title, data_type) in zip(axes, rows):
        if data_type in {"accel", "gyro"}:
            data = imu[data_type]
            time_relative = make_relative_time(data["time_s"], reference_s)

            plot_time, plot_values = downsample_for_plot(
                time_relative,
                safe_normalize(data["magnitude"]),
            )

        elif data_type == "audio":
            envelope_time, envelope = compute_audio_envelope(
                audio["samples"],
                audio["sample_rate"],
            )

            audio_start = (
                audio["start_time_s"]
                if audio["start_time_s"] is not None
                else reference_s
            )

            plot_time = envelope_time + audio_start - reference_s
            plot_values = safe_normalize(envelope)

        else:
            plot_time = make_relative_time(
                gelsight_motion["time_s"],
                reference_s,
            )
            plot_values = safe_normalize(gelsight_motion["motion"])

        axis_plot.plot(plot_time, plot_values, linewidth=1)
        axis_plot.set_title(title)
        axis_plot.set_ylabel("Normalized")
        axis_plot.set_ylim(-0.05, 1.05)
        axis_plot.grid(True, alpha=0.3)

    axes[-1].set_xlabel("Time from shared reference (s)")

    fig.suptitle("Synchronized multimodal recording overview", y=1.01)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------
# Summary writing
# ---------------------------------------------------------------------

def format_statistics(name, statistics):
    return (
        f"{name}\n"
        f"  Samples: {statistics['count']}\n"
        f"  Duration: {statistics['duration_s']:.6f} s\n"
        f"  Mean sampling rate: {statistics['mean_rate_hz']:.3f} Hz\n"
        f"  Median sampling rate: {statistics['median_rate_hz']:.3f} Hz\n"
        f"  Median interval: {statistics['median_dt_s'] * 1e6:.3f} us\n"
        f"  Timing jitter standard deviation: "
        f"{statistics['jitter_std_us']:.3f} us\n"
        f"  Gaps greater than 1.5x median interval: "
        f"{statistics['large_gap_count']}\n"
    )


def write_summary(
    output_path,
    paths,
    imu,
    audio,
    gelsight_timestamps,
    gelsight_motion,
    reference_s,
    detected_events,
):
    lines = []

    lines.append("MULTIMODAL RECORDING ANALYSIS")
    lines.append("=" * 70)
    lines.append("")

    lines.append("Input files")
    lines.append(f"  IMU: {paths['imu']}")
    lines.append(f"  Audio: {paths['audio']}")
    lines.append(f"  Audio timestamp: {paths['audio_timestamp']}")
    lines.append(f"  GelSight video: {paths['video']}")
    lines.append(f"  GelSight timestamp: {paths['video_timestamp']}")
    lines.append("")

    lines.append(f"Shared reference timestamp: {reference_s:.9f} s")
    lines.append("")

    if imu["accel"] is not None:
        statistics = estimate_sampling_statistics(
            imu["accel"]["time_s"]
        )
        lines.append(format_statistics("Accelerometer", statistics))

    if imu["gyro"] is not None:
        statistics = estimate_sampling_statistics(
            imu["gyro"]["time_s"]
        )
        lines.append(format_statistics("Gyroscope", statistics))

    video_statistics = estimate_sampling_statistics(
        gelsight_timestamps["time_s"]
    )
    lines.append(format_statistics("GelSight timestamps", video_statistics))

    lines.append("Audio")
    lines.append(f"  Sample rate: {audio['sample_rate']} Hz")
    lines.append(f"  Samples: {len(audio['samples'])}")
    lines.append(f"  Duration: {audio['duration_s']:.6f} s")
    lines.append(f"  Recorded start timestamp: {audio['start_time_s']}")
    lines.append("")

    lines.append("GelSight video")
    lines.append(
        f"  Resolution: "
        f"{gelsight_motion['width']} x {gelsight_motion['height']}"
    )
    lines.append(
        f"  FPS reported by AVI: {gelsight_motion['fps_reported']:.6f}"
    )
    lines.append(
        f"  Frames reported by AVI: "
        f"{gelsight_motion['video_frame_count']}"
    )
    lines.append(
        f"  Timestamp rows: {len(gelsight_timestamps['time_s'])}"
    )
    lines.append(
        f"  Frames processed for motion: "
        f"{gelsight_motion['processed_frame_count']}"
    )
    lines.append("")

    lines.append("Detected candidate contact events")
    if len(detected_events) == 0:
        lines.append("  No events were automatically detected.")
    else:
        for index, event_time in enumerate(detected_events, start=1):
            lines.append(
                f"  Event {index:02d}: "
                f"{event_time:.6f} s from shared reference"
            )

    output_path.write_text("\n".join(lines))


# ---------------------------------------------------------------------
# Main program
# ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Analyze synchronized GelSight, IMU, and audio data."
    )

    parser.add_argument(
        "session_dir",
        type=Path,
        help="Directory containing the multimodal recording.",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Analysis output directory. Default: SESSION_DIR/analysis",
    )

    parser.add_argument(
        "--max-video-frames",
        type=int,
        default=None,
        help=(
            "Optionally limit video processing for a quick test. "
            "For example: --max-video-frames 1000"
        ),
    )

    parser.add_argument(
        "--maximum-events",
        type=int,
        default=12,
        help="Maximum number of automatically selected events.",
    )

    args = parser.parse_args()

    session_dir = args.session_dir.expanduser().resolve()

    if not session_dir.exists():
        raise FileNotFoundError(f"Session directory does not exist: {session_dir}")

    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else session_dir / "analysis"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    # Find input files.
    video_path = find_file(
        session_dir,
        extensions=[".avi"],
        keywords=["gelsight", "video", "camera"],
    )

    audio_path = find_file(
        session_dir,
        extensions=[".wav"],
        keywords=["audio", "microphone", "mic"],
    )

    imu_path = find_csv(
        session_dir,
        include_keywords=["imu", "accel", "gyro"],
        exclude_keywords=["timestamp", "camera", "video", "audio"],
    )

    video_timestamp_path = find_csv(
        session_dir,
        include_keywords=["gelsight", "video", "camera", "frame", "timestamp"],
        exclude_keywords=["imu", "audio", "mic"],
    )

    audio_timestamp_path = find_csv(
        session_dir,
        include_keywords=["audio", "microphone", "mic", "timestamp"],
        exclude_keywords=["imu", "camera", "video", "gelsight"],
    )

    required = {
        "GelSight AVI": video_path,
        "GelSight timestamp CSV": video_timestamp_path,
        "IMU CSV": imu_path,
        "audio WAV": audio_path,
    }

    missing = [name for name, path in required.items() if path is None]

    if missing:
        print("Files found in session directory:")
        for path in sorted(session_dir.iterdir()):
            print(f"  {path.name}")

        raise FileNotFoundError(
            "Could not automatically locate: " + ", ".join(missing)
        )

    print("Input files")
    print(f"  GelSight video:     {video_path.name}")
    print(f"  GelSight timestamp: {video_timestamp_path.name}")
    print(f"  IMU:                {imu_path.name}")
    print(f"  Audio:              {audio_path.name}")
    print(
        f"  Audio timestamp:    "
        f"{audio_timestamp_path.name if audio_timestamp_path else 'not found'}"
    )

    # Load datasets.
    print("\nLoading IMU...")
    imu = load_imu_csv(imu_path)
    add_vector_magnitude(imu["accel"])
    add_vector_magnitude(imu["gyro"])

    print("Loading audio...")
    audio = load_audio(audio_path, audio_timestamp_path)

    print("Loading GelSight timestamps...")
    gelsight_timestamps = load_gelsight_timestamps(
        video_timestamp_path
    )

    # Establish shared time reference.
    start_candidates = []

    if imu["accel"] is not None:
        start_candidates.append(
            np.nanmin(imu["accel"]["time_s"])
        )

    if imu["gyro"] is not None:
        start_candidates.append(
            np.nanmin(imu["gyro"]["time_s"])
        )

    start_candidates.append(
        np.nanmin(gelsight_timestamps["time_s"])
    )

    if audio["start_time_s"] is not None:
        start_candidates.append(audio["start_time_s"])

    reference_s = float(np.nanmin(start_candidates))

    print(f"Shared timestamp reference: {reference_s:.9f} s")

    # GelSight motion analysis.
    print("Analyzing GelSight video motion...")
    gelsight_motion = analyze_gelsight_motion(
        video_path,
        gelsight_timestamps["time_s"],
        max_frames=args.max_video_frames,
    )

    # Plots.
    print("Creating IMU plots...")
    plot_imu_time_series(
        imu,
        reference_s,
        output_dir / "imu_time_series.png",
    )

    plot_imu_sampling_diagnostics(
        imu,
        output_dir / "imu_sampling_diagnostics.png",
    )

    plot_imu_spectrum(
        imu,
        output_dir / "imu_spectrum.png",
    )

    print("Creating audio plots...")
    plot_audio(
        audio,
        reference_s,
        output_dir / "audio_overview.png",
    )

    plot_audio_spectrogram(
        audio,
        reference_s,
        output_dir / "audio_spectrogram.png",
    )

    print("Creating GelSight plots...")
    plot_gelsight_motion(
        gelsight_motion,
        reference_s,
        output_dir / "gelsight_motion.png",
    )

    print("Creating synchronized overview...")
    plot_synchronization_overview(
        imu,
        audio,
        gelsight_motion,
        reference_s,
        output_dir / "synchronization_overview.png",
    )

    # Detect candidate events. Prefer accelerometer magnitude.
    detected_event_times = np.array([])

    if imu["accel"] is not None:
        accel_time_relative = make_relative_time(
            imu["accel"]["time_s"],
            reference_s,
        )

        accel_magnitude = signal.detrend(
            np.nan_to_num(imu["accel"]["magnitude"])
        )

        # Absolute detrended magnitude emphasizes sudden impacts.
        event_signal = np.abs(accel_magnitude)

        detected_event_times, _ = detect_peaks_from_signal(
            accel_time_relative,
            event_signal,
            minimum_distance_s=0.25,
            prominence_fraction=0.15,
            maximum_events=args.maximum_events,
        )

    # Fall back to GelSight motion.
    if len(detected_event_times) == 0:
        gelsight_time_relative = make_relative_time(
            gelsight_motion["time_s"],
            reference_s,
        )

        detected_event_times, _ = detect_peaks_from_signal(
            gelsight_time_relative,
            gelsight_motion["motion"],
            minimum_distance_s=0.25,
            prominence_fraction=0.15,
            maximum_events=args.maximum_events,
        )

    print(
        f"Detected {len(detected_event_times)} "
        f"candidate contact events."
    )

    save_event_frames(
        video_path,
        make_relative_time(
            gelsight_timestamps["time_s"],
            reference_s,
        ),
        detected_event_times,
        output_dir / "gelsight_event_frames",
    )

    paths = {
        "imu": imu_path,
        "audio": audio_path,
        "audio_timestamp": audio_timestamp_path,
        "video": video_path,
        "video_timestamp": video_timestamp_path,
    }

    write_summary(
        output_dir / "summary.txt",
        paths,
        imu,
        audio,
        gelsight_timestamps,
        gelsight_motion,
        reference_s,
        detected_event_times,
    )

    print("\nAnalysis complete.")
    print(f"Results saved to: {output_dir}")
    print("\nImportant outputs:")
    print(f"  {output_dir / 'synchronization_overview.png'}")
    print(f"  {output_dir / 'imu_spectrum.png'}")
    print(f"  {output_dir / 'audio_spectrogram.png'}")
    print(f"  {output_dir / 'gelsight_motion.png'}")
    print(f"  {output_dir / 'summary.txt'}")


if __name__ == "__main__":
    main()