import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import signal


ADC_MAX = 4095.0
ADC_REFERENCE_MV = 3300.0


def load_piezo_csv(csv_path: Path):
    df = pd.read_csv(csv_path)

    print("Piezo CSV columns:")
    print(list(df.columns))

    if "host_elapsed_s" in df.columns:
        time_s = df["host_elapsed_s"].to_numpy(
            dtype=np.float64
        )

    elif "host_elapsed_ms" in df.columns:
        time_s = (
            df["host_elapsed_ms"].to_numpy(
                dtype=np.float64
            )
            / 1000.0
        )

    else:
        raise ValueError(
            "Piezo CSV must contain either "
            "'host_elapsed_s' or 'host_elapsed_ms'."
        )

    if "voltage_mv" in df.columns:
        signal_mv = df["voltage_mv"].to_numpy(
            dtype=np.float64
        )

    elif "adc_raw" in df.columns:
        adc_raw = df["adc_raw"].to_numpy(
            dtype=np.float64
        )

        signal_mv = (
            adc_raw
            * ADC_REFERENCE_MV
            / ADC_MAX
        )

    else:
        raise ValueError(
            "Piezo CSV must contain either "
            "'voltage_mv' or 'adc_raw'."
        )

    valid = (
        np.isfinite(time_s)
        & np.isfinite(signal_mv)
    )

    time_s = time_s[valid]
    signal_mv = signal_mv[valid]

    if len(time_s) < 2:
        raise ValueError(
            "Piezo CSV does not contain enough valid samples."
        )

    order = np.argsort(time_s)

    time_s = time_s[order]
    signal_mv = signal_mv[order]

    return time_s, signal_mv


def estimate_sample_rate(time_s):
    time_difference = np.diff(time_s)

    valid_differences = time_difference[
        time_difference > 0
    ]

    if len(valid_differences) == 0:
        raise ValueError(
            "Unable to estimate sampling rate."
        )

    median_period_s = np.median(
        valid_differences
    )

    sample_rate_hz = 1.0 / median_period_s

    return sample_rate_hz, valid_differences


def calculate_fft(
    signal_mv,
    sample_rate_hz,
):
    centered_signal = (
        signal_mv
        - np.mean(signal_mv)
    )

    window = np.hanning(
        len(centered_signal)
    )

    windowed_signal = (
        centered_signal
        * window
    )

    fft_values = np.fft.rfft(
        windowed_signal
    )

    frequencies_hz = np.fft.rfftfreq(
        len(windowed_signal),
        d=1.0 / sample_rate_hz,
    )

    magnitude_mv = np.abs(
        fft_values
    )

    window_gain = np.sum(window)

    if window_gain > 0:
        magnitude_mv = (
            2.0
            * magnitude_mv
            / window_gain
        )

    if len(magnitude_mv) > 0:
        magnitude_mv[0] /= 2.0

    return (
        frequencies_hz,
        magnitude_mv,
        centered_signal,
    )


def calculate_spectrogram(
    signal_mv,
    sample_rate_hz,
    window_duration_s=0.02,
    overlap_fraction=0.75,
):
    centered_signal = (
        signal_mv
        - np.mean(signal_mv)
    )

    samples_per_window = int(
        sample_rate_hz
        * window_duration_s
    )

    samples_per_window = max(
        32,
        samples_per_window,
    )

    samples_per_window = min(
        samples_per_window,
        len(centered_signal),
    )

    overlap_samples = int(
        samples_per_window
        * overlap_fraction
    )

    overlap_samples = min(
        overlap_samples,
        samples_per_window - 1,
    )

    (
        frequencies_hz,
        times_s,
        power,
    ) = signal.spectrogram(
        centered_signal,
        fs=sample_rate_hz,
        window="hann",
        nperseg=samples_per_window,
        noverlap=overlap_samples,
        detrend=False,
        scaling="density",
        mode="psd",
    )

    power_db = 10.0 * np.log10(
        power
        + np.finfo(float).eps
    )

    return (
        frequencies_hz,
        times_s,
        power_db,
    )


def print_statistics(
    time_s,
    signal_mv,
    centered_signal,
    sample_rate_hz,
    sample_periods,
):
    duration_s = (
        time_s[-1]
        - time_s[0]
    )

    rms_mv = np.sqrt(
        np.mean(
            centered_signal ** 2
        )
    )

    peak_absolute_mv = np.max(
        np.abs(centered_signal)
    )

    peak_to_peak_mv = (
        np.max(signal_mv)
        - np.min(signal_mv)
    )

    mean_period_us = (
        np.mean(sample_periods)
        * 1e6
    )

    median_period_us = (
        np.median(sample_periods)
        * 1e6
    )

    print()
    print("Piezo recording summary")
    print(
        f"Samples:             "
        f"{len(signal_mv)}"
    )
    print(
        f"Duration:            "
        f"{duration_s:.3f} s"
    )
    print(
        f"Estimated rate:      "
        f"{sample_rate_hz:.2f} Hz"
    )
    print(
        f"Mean sample period:  "
        f"{mean_period_us:.2f} us"
    )
    print(
        f"Median period:       "
        f"{median_period_us:.2f} us"
    )
    print(
        f"Mean voltage:        "
        f"{np.mean(signal_mv):.2f} mV"
    )
    print(
        f"Minimum voltage:     "
        f"{np.min(signal_mv):.2f} mV"
    )
    print(
        f"Maximum voltage:     "
        f"{np.max(signal_mv):.2f} mV"
    )
    print(
        f"Peak-to-peak:        "
        f"{peak_to_peak_mv:.2f} mV"
    )
    print(
        f"AC RMS:              "
        f"{rms_mv:.2f} mV"
    )
    print(
        f"Maximum AC peak:     "
        f"{peak_absolute_mv:.2f} mV"
    )


def plot_results(
    time_s,
    signal_mv,
    centered_signal,
    frequencies_hz,
    magnitude_mv,
    spectrogram_frequencies_hz,
    spectrogram_times_s,
    spectrogram_power_db,
    max_frequency_hz,
):
    relative_time_s = (
        time_s
        - time_s[0]
    )

    plt.figure(
        figsize=(12, 5)
    )

    plt.plot(
        relative_time_s,
        signal_mv,
        linewidth=0.7,
    )

    plt.xlabel("Time (s)")
    plt.ylabel("Voltage (mV)")
    plt.title(
        "Piezo Contact Microphone Raw Signal"
    )
    plt.grid(True)
    plt.tight_layout()

    plt.figure(
        figsize=(12, 5)
    )

    plt.plot(
        relative_time_s,
        centered_signal,
        linewidth=0.7,
    )

    plt.xlabel("Time (s)")
    plt.ylabel("AC voltage (mV)")
    plt.title(
        "Piezo Signal with DC Offset Removed"
    )
    plt.grid(True)
    plt.tight_layout()

    frequency_mask = (
        frequencies_hz
        <= max_frequency_hz
    )

    plt.figure(
        figsize=(12, 5)
    )

    plt.plot(
        frequencies_hz[
            frequency_mask
        ],
        magnitude_mv[
            frequency_mask
        ],
        linewidth=0.8,
    )

    plt.xlabel("Frequency (Hz)")
    plt.ylabel("Magnitude (mV)")
    plt.title(
        "Piezo Contact Microphone Spectrum"
    )
    plt.grid(True)
    plt.tight_layout()

    spectrogram_frequency_mask = (
        spectrogram_frequencies_hz
        <= max_frequency_hz
    )

    plt.figure(
        figsize=(12, 6)
    )

    mesh = plt.pcolormesh(
        spectrogram_times_s,
        spectrogram_frequencies_hz[
            spectrogram_frequency_mask
        ],
        spectrogram_power_db[
            spectrogram_frequency_mask,
            :
        ],
        shading="auto",
    )

    plt.xlabel("Time (s)")
    plt.ylabel("Frequency (Hz)")
    plt.title(
        "Piezo Contact Microphone Spectrogram"
    )

    colorbar = plt.colorbar(mesh)

    colorbar.set_label(
        "Power spectral density (dB)"
    )

    plt.tight_layout()
    plt.show()


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Analyze the piezo contact "
            "microphone CSV."
        )
    )

    parser.add_argument(
        "input",
        type=Path,
        help=(
            "Path to piezo.csv or to a "
            "recording session directory."
        ),
    )

    parser.add_argument(
        "--max-frequency",
        type=float,
        default=5000.0,
        help=(
            "Maximum frequency displayed "
            "in the FFT and spectrogram. "
            "Default: 5000 Hz."
        ),
    )

    parser.add_argument(
        "--window-duration",
        type=float,
        default=0.02,
        help=(
            "Spectrogram window duration "
            "in seconds. Default: 0.02."
        ),
    )

    parser.add_argument(
        "--overlap",
        type=float,
        default=0.75,
        help=(
            "Spectrogram overlap fraction "
            "between 0 and less than 1. "
            "Default: 0.75."
        ),
    )

    args = parser.parse_args()

    if args.window_duration <= 0:
        raise ValueError(
            "--window-duration must be greater than 0."
        )

    if not 0 <= args.overlap < 1:
        raise ValueError(
            "--overlap must be between 0 and less than 1."
        )

    input_path = (
        args.input
        .expanduser()
        .resolve()
    )

    if input_path.is_dir():
        csv_path = (
            input_path
            / "piezo.csv"
        )
    else:
        csv_path = input_path

    if not csv_path.exists():
        raise FileNotFoundError(
            f"Piezo CSV not found: {csv_path}"
        )

    print(
        f"Loading piezo data: "
        f"{csv_path}"
    )

    (
        time_s,
        signal_mv,
    ) = load_piezo_csv(
        csv_path
    )

    (
        sample_rate_hz,
        sample_periods,
    ) = estimate_sample_rate(
        time_s
    )

    (
        frequencies_hz,
        magnitude_mv,
        centered_signal,
    ) = calculate_fft(
        signal_mv=signal_mv,
        sample_rate_hz=sample_rate_hz,
    )

    (
        spectrogram_frequencies_hz,
        spectrogram_times_s,
        spectrogram_power_db,
    ) = calculate_spectrogram(
        signal_mv=signal_mv,
        sample_rate_hz=sample_rate_hz,
        window_duration_s=(
            args.window_duration
        ),
        overlap_fraction=(
            args.overlap
        ),
    )

    print_statistics(
        time_s=time_s,
        signal_mv=signal_mv,
        centered_signal=centered_signal,
        sample_rate_hz=sample_rate_hz,
        sample_periods=sample_periods,
    )

    maximum_available_frequency = (
        sample_rate_hz / 2.0
    )

    display_frequency = min(
        args.max_frequency,
        maximum_available_frequency,
    )

    plot_results(
        time_s=time_s,
        signal_mv=signal_mv,
        centered_signal=centered_signal,
        frequencies_hz=frequencies_hz,
        magnitude_mv=magnitude_mv,
        spectrogram_frequencies_hz=(
            spectrogram_frequencies_hz
        ),
        spectrogram_times_s=(
            spectrogram_times_s
        ),
        spectrogram_power_db=(
            spectrogram_power_db
        ),
        max_frequency_hz=(
            display_frequency
        ),
    )


if __name__ == "__main__":
    main()