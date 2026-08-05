#!/usr/bin/env python3
"""
Plot a selected time clip from piezo.csv and compute its FFT.

Example:
    python plot_piezo_fft_clip.py piezo.csv --start 5.0 --end 7.0

Optional:
    python plot_piezo_fft_clip.py piezo.csv --start 5.0 --end 7.0 --max-freq 3000
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def load_piezo_csv(csv_path: Path):
    df = pd.read_csv(csv_path)

    required_columns = {"host_elapsed_s", "adc_raw"}
    missing = required_columns - set(df.columns)
    if missing:
        raise ValueError(
            f"Missing required CSV columns: {sorted(missing)}\n"
            f"Available columns: {list(df.columns)}"
        )

    time_s = df["host_elapsed_s"].to_numpy(dtype=np.float64)
    signal_raw = df["adc_raw"].to_numpy(dtype=np.float64)

    valid = np.isfinite(time_s) & np.isfinite(signal_raw)
    time_s = time_s[valid]
    signal_raw = signal_raw[valid]

    if len(time_s) < 2:
        raise ValueError("The CSV does not contain enough valid samples.")

    # Ensure timestamps are increasing.
    order = np.argsort(time_s)
    time_s = time_s[order]
    signal_raw = signal_raw[order]

    # Remove duplicate timestamps because interpolation requires unique x values.
    time_s, unique_indices = np.unique(time_s, return_index=True)
    signal_raw = signal_raw[unique_indices]

    return time_s, signal_raw


def select_clip(time_s, signal_raw, start_s, end_s):
    if start_s < time_s[0]:
        raise ValueError(
            f"--start must be at least {time_s[0]:.6f} s."
        )

    if end_s > time_s[-1]:
        raise ValueError(
            f"--end must be no greater than {time_s[-1]:.6f} s."
        )

    if end_s <= start_s:
        raise ValueError("--end must be greater than --start.")

    mask = (time_s >= start_s) & (time_s <= end_s)
    clip_time = time_s[mask]
    clip_signal = signal_raw[mask]

    if len(clip_time) < 4:
        raise ValueError(
            "The selected clip contains too few samples. "
            "Choose a longer time interval."
        )

    return clip_time, clip_signal


def resample_uniformly(clip_time, clip_signal):
    """
    The host timestamps are not perfectly evenly spaced, while a standard FFT
    assumes uniform sampling. This function interpolates the selected clip onto
    a uniform time grid using the median timestamp spacing.
    """
    dt = np.diff(clip_time)
    positive_dt = dt[dt > 0]

    if len(positive_dt) == 0:
        raise ValueError("Could not determine a valid sample interval.")

    median_dt = np.median(positive_dt)
    sample_rate_hz = 1.0 / median_dt

    number_of_samples = int(
        np.floor((clip_time[-1] - clip_time[0]) * sample_rate_hz)
    ) + 1

    uniform_time = (
        clip_time[0]
        + np.arange(number_of_samples, dtype=np.float64) / sample_rate_hz
    )

    uniform_signal = np.interp(
        uniform_time,
        clip_time,
        clip_signal,
    )

    return uniform_time, uniform_signal, sample_rate_hz


def calculate_fft(signal_raw, sample_rate_hz):
    # Remove DC offset so the zero-frequency peak does not dominate.
    centered_signal = signal_raw - np.mean(signal_raw)

    # Hann window reduces spectral leakage at the ends of the clip.
    window = np.hanning(len(centered_signal))
    windowed_signal = centered_signal * window

    fft_complex = np.fft.rfft(windowed_signal)
    frequencies_hz = np.fft.rfftfreq(
        len(windowed_signal),
        d=1.0 / sample_rate_hz,
    )

    # One-sided amplitude spectrum with window-gain correction.
    coherent_gain = np.mean(window)
    amplitude = (
        2.0
        * np.abs(fft_complex)
        / (len(windowed_signal) * coherent_gain)
    )

    # DC and Nyquist bins should not be doubled.
    amplitude[0] *= 0.5
    if len(windowed_signal) % 2 == 0:
        amplitude[-1] *= 0.5

    return frequencies_hz, amplitude


def main():
    parser = argparse.ArgumentParser(
        description="Plot a time clip from piezo.csv and calculate its FFT."
    )
    parser.add_argument(
        "csv",
        type=Path,
        help="Path to piezo.csv",
    )
    parser.add_argument(
        "--start",
        type=float,
        required=True,
        help="Clip start time in seconds.",
    )
    parser.add_argument(
        "--end",
        type=float,
        required=True,
        help="Clip end time in seconds.",
    )
    parser.add_argument(
        "--max-freq",
        type=float,
        default=None,
        help=(
            "Maximum displayed FFT frequency in Hz. "
            "Default: Nyquist frequency."
        ),
    )
    parser.add_argument(
        "--log-y",
        action="store_true",
        help="Display the FFT amplitude axis using a logarithmic scale.",
    )
    args = parser.parse_args()

    time_s, signal_raw = load_piezo_csv(args.csv)

    print(f"Dataset time range: {time_s[0]:.6f} to {time_s[-1]:.6f} s")

    clip_time, clip_signal = select_clip(
        time_s,
        signal_raw,
        args.start,
        args.end,
    )

    uniform_time, uniform_signal, sample_rate_hz = resample_uniformly(
        clip_time,
        clip_signal,
    )

    frequencies_hz, amplitude = calculate_fft(
        uniform_signal,
        sample_rate_hz,
    )

    clip_duration_s = uniform_time[-1] - uniform_time[0]
    frequency_resolution_hz = sample_rate_hz / len(uniform_signal)
    nyquist_hz = sample_rate_hz / 2.0

    print(f"Selected clip: {args.start:.6f} to {args.end:.6f} s")
    print(f"Clip duration: {clip_duration_s:.6f} s")
    print(f"Samples in resampled clip: {len(uniform_signal)}")
    print(f"Estimated sample rate: {sample_rate_hz:.2f} Hz")
    print(f"FFT frequency resolution: {frequency_resolution_hz:.3f} Hz")
    print(f"Nyquist frequency: {nyquist_hz:.2f} Hz")

    # Ignore DC when reporting the strongest nonzero frequency.
    if len(amplitude) > 1:
        peak_index = np.argmax(amplitude[1:]) + 1
        print(
            f"Strongest non-DC frequency: "
            f"{frequencies_hz[peak_index]:.2f} Hz "
            f"(amplitude {amplitude[peak_index]:.3f} ADC counts)"
        )

    # Figure 1: selected time clip
    plt.figure(figsize=(12, 5))
    plt.plot(
        uniform_time,
        uniform_signal,
        linewidth=0.8,
    )
    plt.title(
        f"Piezo Signal Clip: {args.start:.3f}–{args.end:.3f} s"
    )
    plt.xlabel("Time (s)")
    plt.ylabel("ADC raw")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()

    # Figure 2: FFT
    plt.figure(figsize=(12, 5))
    plt.plot(
        frequencies_hz,
        amplitude,
        linewidth=0.8,
    )
    plt.title(
        f"FFT of Piezo Clip: {args.start:.3f}–{args.end:.3f} s"
    )
    plt.xlabel("Frequency (Hz)")
    plt.ylabel("Amplitude (ADC counts)")

    if args.max_freq is not None:
        if args.max_freq <= 0:
            raise ValueError("--max-freq must be greater than zero.")
        plt.xlim(0, min(args.max_freq, nyquist_hz))
    else:
        plt.xlim(0, nyquist_hz)

    if args.log_y:
        positive_amplitude = amplitude[amplitude > 0]
        if len(positive_amplitude) > 0:
            plt.yscale("log")

    plt.grid(True, alpha=0.3)
    plt.tight_layout()

    # Matplotlib's window toolbar allows pan and zoom.
    plt.show()


if __name__ == "__main__":
    main()