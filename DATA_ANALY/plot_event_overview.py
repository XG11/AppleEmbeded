#!/usr/bin/env python3

"""
plot_event_overview.py

Plot every IMU channel, the load cell, and the microphone (raw waveform
and spectrogram) for one recording session, with dashed vertical lines
marking:

    t_press:      when the physical press was first initiated
                   (detected from the load-cell onset, before t_connect)
    t_connect:    when electrical connection was first made
                   (connector_label.json -> success_times_ms)

Example:
    python plot_event_overview.py recordings/session_20260902_151903

Output:
    SESSION_DIR/analysis/event_overview.png
"""

import argparse
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import signal

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_multimodal import (  # noqa: E402
    add_vector_magnitude,
    downsample_for_plot,
    load_audio,
    load_imu_csv,
    make_relative_time,
)


# ---------------------------------------------------------------------
# Event timing
# ---------------------------------------------------------------------

def load_connect_time_s(session_dir, event_index=0):
    """
    Load the electrical-connection timestamp from connector_label.json.

    Uses success_times_ms, which is the ground-truth confirmed-insertion
    timestamp (see auto_record_launcher.py's save_label docstring).
    """
    label_path = session_dir / "connector_label.json"

    if not label_path.exists():
        return None

    with label_path.open() as f:
        label = json.load(f)

    success_times_ms = label.get("success_times_ms") or []

    if event_index >= len(success_times_ms):
        return None

    return success_times_ms[event_index] / 1000.0


def detect_press_onset_s(
    load_cell,
    t_connect_s,
    onset_fraction=0.1,
    quiet_window_s=(0.5, 3.0),
    search_lookback_s=8.0,
    smoothing_window_s=0.03,
):
    """
    Detect when a press began, using the load-cell signal.

    The connector's electrical contacts close near the end of a press, so
    the mechanical press itself starts somewhat earlier. This finds the
    load-cell peak closest to t_connect_s and walks backward to the point
    where the (smoothed) signal first rises above `onset_fraction` of the
    peak's deviation from baseline -- a standard rise-time onset measure.
    """
    time_s = np.asarray(load_cell["time_s"], dtype=float)
    raw = np.asarray(load_cell["raw"], dtype=float)

    valid = np.isfinite(time_s) & np.isfinite(raw)
    time_s = time_s[valid]
    raw = raw[valid]

    if len(time_s) < 10 or t_connect_s is None:
        return None

    dt = np.median(np.diff(time_s))
    window = max(3, int(round(smoothing_window_s / dt)))
    if window % 2 == 0:
        window += 1

    smoothed = (
        pd.Series(raw)
        .rolling(window, center=True, min_periods=1)
        .median()
        .to_numpy()
    )

    quiet_mask = (
        (time_s > time_s[0] + quiet_window_s[0])
        & (time_s < time_s[0] + quiet_window_s[1])
    )

    if not np.any(quiet_mask):
        return None

    baseline = float(np.median(raw[quiet_mask]))

    search_mask = (
        (time_s > t_connect_s - search_lookback_s)
        & (time_s < t_connect_s + 1.0)
    )

    if not np.any(search_mask):
        return None

    search_time = time_s[search_mask]
    search_values = smoothed[search_mask]

    peak_index = int(np.argmax(np.abs(search_values - baseline)))
    peak_value = search_values[peak_index]

    threshold = baseline + onset_fraction * (peak_value - baseline)

    index = peak_index
    if peak_value >= baseline:
        while index > 0 and search_values[index] > threshold:
            index -= 1
    else:
        while index > 0 and search_values[index] < threshold:
            index -= 1

    return float(search_time[index])


# ---------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------

def add_event_lines(axis_plot, t_press_s, t_connect_s, with_label=False):
    if t_press_s is not None:
        axis_plot.axvline(
            t_press_s,
            color="tab:orange",
            linestyle="--",
            linewidth=1.4,
            label="press initiated" if with_label else None,
        )

    if t_connect_s is not None:
        axis_plot.axvline(
            t_connect_s,
            color="tab:red",
            linestyle="--",
            linewidth=1.4,
            label="electrical connection" if with_label else None,
        )


def plot_event_overview(
    imu,
    audio,
    reference_s,
    t_press_s,
    t_connect_s,
    output_path,
    max_points=100_000,
    xlim=None,
):
    imu_groups = [
        ("Accelerometer", imu["accel"], "Acceleration (m/s²)"),
        ("Gyroscope", imu["gyro"], "Angular velocity (rad/s)"),
    ]

    axis_colors = {"x": "tab:blue", "y": "tab:orange", "z": "tab:green"}

    n_rows = len(imu_groups) + 3  # + load cell + audio raw + spectrogram

    fig, axes = plt.subplots(
        n_rows,
        1,
        figsize=(15, 3 * n_rows),
        sharex=True,
    )

    row = 0

    # IMU: accelerometer and gyroscope, each with x/y/z overlaid
    for title, data, ylabel in imu_groups:
        axis_plot = axes[row]
        time_relative = make_relative_time(data["time_s"], reference_s)

        for axis_name in ["x", "y", "z"]:
            plot_time, plot_values = downsample_for_plot(
                time_relative,
                data[axis_name],
                max_points=max_points,
            )

            axis_plot.plot(
                plot_time,
                plot_values,
                linewidth=0.7,
                color=axis_colors[axis_name],
                label=axis_name.upper(),
            )

        axis_plot.set_title(title)
        axis_plot.set_ylabel(ylabel)
        axis_plot.grid(True, alpha=0.3)
        add_event_lines(axis_plot, t_press_s, t_connect_s, with_label=(row == 0))
        axis_plot.legend(loc="upper right", ncol=4)

        row += 1

    # Load cell
    load_cell = imu["load_cell"]
    load_cell_time = make_relative_time(load_cell["time_s"], reference_s)
    load_cell_values = (
        load_cell["force_n"] if "force_n" in load_cell else load_cell["raw"]
    )
    load_cell_ylabel = "Force (N)" if "force_n" in load_cell else "ADC raw"

    plot_time, plot_values = downsample_for_plot(
        load_cell_time,
        np.asarray(load_cell_values, dtype=float),
        max_points=max_points,
    )

    axis_plot = axes[row]
    axis_plot.plot(plot_time, plot_values, linewidth=0.7, color="tab:green")
    axis_plot.set_title("Load cell")
    axis_plot.set_ylabel(load_cell_ylabel)
    axis_plot.grid(True, alpha=0.3)
    add_event_lines(axis_plot, t_press_s, t_connect_s)
    row += 1

    # Audio raw waveform
    sample_rate = audio["sample_rate"]
    samples = audio["samples"]

    audio_start = (
        audio["start_time_s"] if audio["start_time_s"] is not None else reference_s
    )
    relative_start = audio_start - reference_s

    waveform_time = np.arange(len(samples)) / sample_rate + relative_start
    plot_time, plot_samples = downsample_for_plot(
        waveform_time,
        samples,
        max_points=200_000,
    )

    axis_plot = axes[row]
    axis_plot.plot(plot_time, plot_samples, linewidth=0.4, color="tab:gray")
    axis_plot.set_title("Microphone waveform")
    axis_plot.set_ylabel("Amplitude")
    axis_plot.grid(True, alpha=0.3)
    add_event_lines(axis_plot, t_press_s, t_connect_s)
    row += 1

    # Audio spectrogram
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
    spectrogram_time = time_local + relative_start

    axis_plot = axes[row]
    image = axis_plot.pcolormesh(
        spectrogram_time,
        frequency,
        spectrum_db,
        shading="auto",
        rasterized=True,
    )
    axis_plot.set_title("Microphone spectrogram")
    axis_plot.set_ylabel("Frequency (Hz)")
    axis_plot.set_ylim(0, sample_rate / 2)

    colorbar = fig.colorbar(image, ax=axis_plot)
    colorbar.set_label("Power (dB)")

    add_event_lines(axis_plot, t_press_s, t_connect_s)

    for axis_plot in axes:
        axis_plot.set_xlabel("Time from session start (s)")
        axis_plot.tick_params(labelbottom=True)

        if xlim is not None:
            axis_plot.set_xlim(xlim)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180)
    plt.close(fig)

    print(f"Saved: {output_path}")


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Plot IMU channels, load cell, and microphone "
            "(raw + spectrogram) with press/connection event markers."
        )
    )

    parser.add_argument("session_dir", type=Path, help="Recording session directory.")

    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output PNG path. Default: SESSION_DIR/analysis/event_overview.png",
    )

    parser.add_argument(
        "--event-index",
        type=int,
        default=0,
        help="Which success_times_ms entry to mark. Default: 0 (first).",
    )

    parser.add_argument(
        "--press-onset-fraction",
        type=float,
        default=0.1,
        help=(
            "Fraction of peak load-cell deviation used as the press-onset "
            "threshold (10%%-90%% rise-time convention). Default: 0.1"
        ),
    )

    parser.add_argument(
        "--xlim",
        type=float,
        nargs=2,
        default=None,
        metavar=("START_S", "END_S"),
        help="Zoom the plotted time range, e.g. --xlim 18 26",
    )

    args = parser.parse_args()

    session_dir = args.session_dir.expanduser().resolve()

    if not session_dir.exists():
        raise FileNotFoundError(f"Session directory does not exist: {session_dir}")

    output_path = (
        args.output.expanduser().resolve()
        if args.output is not None
        else session_dir / "analysis" / "event_overview.png"
    )

    imu_path = session_dir / "imu_loadcell.csv"
    audio_path = session_dir / "rode_microphone.wav"
    audio_timestamp_path = session_dir / "rode_microphone_timestamps.csv"

    print("Loading IMU and load-cell data...")
    imu = load_imu_csv(imu_path)
    add_vector_magnitude(imu["accel"])
    add_vector_magnitude(imu["gyro"])

    print("Loading audio...")
    audio = load_audio(audio_path, audio_timestamp_path)

    reference_s = float(
        min(
            np.nanmin(imu["accel"]["time_s"]),
            np.nanmin(imu["load_cell"]["time_s"]),
        )
    )

    t_connect_s = load_connect_time_s(session_dir, event_index=args.event_index)

    if t_connect_s is None:
        print("No electrical-connection timestamp found in connector_label.json.")
        t_press_s = None
    else:
        print(f"Electrical connection made at t = {t_connect_s:.3f} s")

        t_press_s = detect_press_onset_s(
            imu["load_cell"],
            t_connect_s=reference_s + t_connect_s,
            onset_fraction=args.press_onset_fraction,
        )

        if t_press_s is not None:
            t_press_s -= reference_s
            print(
                f"Press initiated at t = {t_press_s:.3f} s "
                f"({t_connect_s - t_press_s:.3f} s before connection)"
            )
        else:
            print("Could not detect a press onset from the load-cell signal.")

    plot_event_overview(
        imu,
        audio,
        reference_s,
        t_press_s,
        t_connect_s,
        output_path,
        xlim=args.xlim,
    )


if __name__ == "__main__":
    main()
